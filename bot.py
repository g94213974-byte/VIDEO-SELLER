import os, json, threading, time, datetime, re
from flask import Flask
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaVideo

TOKEN   = os.environ.get('BOT_TOKEN')
OWNER_ID = int(os.environ.get('OWNER_ID', '0'))
LOG_CHANNEL_ID = int(os.environ.get('LOG_CHANNEL_ID', '0'))

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# Bot Username dynamically fetch
BOT_USERNAME = ""
try:
    BOT_USERNAME = bot.get_me().username
except Exception:
    pass

# ---------------- TIME PARSER & FORMATTER HELPERS ----------------
def parse_duration(time_str):
    time_str = str(time_str).strip().lower()
    match = re.match(r"^(\d+)([smhd])?$", time_str)
    if not match: return None
    val, unit = match.groups()
    val = int(val)
    if unit == 's' or not unit: return val
    elif unit == 'm': return val * 60
    elif unit == 'h': return val * 3600
    elif unit == 'd': return val * 86400
    return None

def format_time_left(expiry_time):
    if not expiry_time:
        return "Unlimited ♾️"
    rem = int(expiry_time - time.time())
    if rem <= 0:
        return "Expired ❌"
    days = rem // 86400
    hours = (rem % 86400) // 3600
    mins = (rem % 3600) // 60
    secs = rem % 60
    parts = []
    if days > 0: parts.append(f"{days}d")
    if hours > 0: parts.append(f"{hours}h")
    if mins > 0: parts.append(f"{mins}m")
    if secs > 0 and not days and not hours: parts.append(f"{secs}s")
    return " ".join(parts) + " left ⏳"

# Safe delete helper
def delete_msg_safe(chat_id, msg_id):
    try:
        bot.delete_message(chat_id, msg_id)
    except Exception:
        pass

# ---------------- DATA MODEL ----------------
def new_store():
    return {
        "welcome_msg": "👋 Hello, {name}!\n\nChoose a plan to get started:",
        "start_videos": [], "how_to_use_video": "", "payment_photo": "",
        "payment_msg": "💳 **Payment Instructions**\n\nPlease scan the QR and pay, then click 'I have paid'.",
        "reject_msg": "❌ Payment not received. Please try again...",
        "layout_style": "vertical", "products": [], "blocked_users": [],
        "users": [], "buyers": [],
        "auto_bc": {"status": False, "interval_seconds": 3600,
                    "message_type": None, "file_id": None, "text": None},
        "broadcast_perm": False,
        "expiry_time": None,
        "seller_name": "Seller"
    }

def fresh_state():
    return {"sellers": {}, "hub": new_store()}

DB_STATE = fresh_state()

# ---------------- CHANNEL DB (file pin) ----------------
def save_db():
    try:
        doc = bot.send_document(LOG_CHANNEL_ID, ("bot_backup.json", json.dumps(DB_STATE).encode()))
        try: bot.pin_chat_message(LOG_CHANNEL_ID, doc.message_id)
        except Exception: pass
    except Exception as e: print("DB save ERROR:", e)

def load_db():
    global DB_STATE
    try:
        chat = bot.get_chat(LOG_CHANNEL_ID); pm = chat.pinned_message
        if pm and pm.document:
            f = bot.get_file(pm.document.file_id)
            data = json.loads(bot.download_file(f.file_path).decode())
            d = fresh_state(); d.update(data)
            d.setdefault("hub", new_store()); d.setdefault("sellers", {})
            DB_STATE = d
    except Exception as e:
        print("No DB yet:", e); save_db()

load_db()

# ---------------- HELPERS ----------------
def store_of_key(key):
    return DB_STATE["hub"] if key in ("hub", None) else DB_STATE["sellers"].get(key, new_store())

def key_of_seller_tg(tg):
    now = time.time()
    for k, s in DB_STATE["sellers"].items():
        if s.get("tg_id") == tg:
            exp = s.get("expiry_time")
            if exp and now > exp:
                return None
            return k
    return None

def send_videos(chat, vids):
    if not vids: return
    if len(vids) == 1:
        try: bot.send_video(chat, vids[0])
        except Exception: pass
    else:
        for i in range(0, len(vids), 10):
            try: bot.send_media_group(chat, [InputMediaVideo(v) for v in vids[i:i+10]])
            except Exception:
                for v in vids[i:i+10]:
                    try: bot.send_video(chat, v)
                    except Exception: pass

# ---------------- SINGLE-MESSAGE PANEL ENGINE ----------------
panel = {}
user_states = {}

def edit_panel(uid, text, markup=None):
    text = text if text else " "
    try:
        p = panel.get(uid)
        if p and p.get("msg_id"):
            try:
                bot.edit_message_text(text, uid, p["msg_id"], reply_markup=markup, parse_mode="Markdown", disable_web_page_preview=True)
                return
            except Exception: pass
        msg = bot.send_message(uid, text, reply_markup=markup, parse_mode="Markdown", disable_web_page_preview=True)
        panel[uid] = {"msg_id": msg.message_id}
    except Exception:
        try:
            msg = bot.send_message(uid, text, reply_markup=markup, parse_mode="Markdown")
            panel[uid] = {"msg_id": msg.message_id}
        except Exception: pass

def push(uid, route):
    user_states.setdefault(uid, {}).setdefault("nav", []).append(route)

def back(uid):
    nav = user_states.get(uid, {}).get("nav", [])
    if nav: nav.pop()
    return nav[-1] if nav else None

def top(uid):
    nav = user_states.get(uid, {}).get("nav", [])
    return nav[-1] if nav else None

def BACK(uid, label="🔙 Back"):
    return InlineKeyboardButton(label, callback_data="NAV_BACK")

def kb(*rows):
    m = InlineKeyboardMarkup()
    for r in rows: m.row(*r)
    return m

# ---------------- ROUTE RENDERERS ----------------
def render(uid):
    ctx = user_states.get(uid, {})
    route = top(uid)
    key = ctx.get("key", "hub")

    # ADMIN PANEL
    if route == "admin" or route == "seller":
        store = store_of_key(key)
        lp = "↔️ Horizontal" if store.get("layout_style") == "horizontal" else "↕️ Vertical"
        
        time_status = format_time_left(store.get("expiry_time")) if key != "hub" else "Owner Access 👑"
        sname = store.get("seller_name", "Seller")
        
        # Build Product Link for Admin
        prod_link = f"https://t.me/{BOT_USERNAME}?start={key}" if key != "hub" else f"https://t.me/{BOT_USERNAME}"
        
        rows = [
            [InlineKeyboardButton("🛍️ Product Button Management", callback_data="SUB_products")],
            [InlineKeyboardButton("📐 Layout: " + lp, callback_data="ACT_togglelayout")],
            [InlineKeyboardButton("📦 Buyers List", callback_data="GO_bl")],
            [InlineKeyboardButton("🎞️ Start Videos", callback_data="SUB_startvids")],
            [InlineKeyboardButton("📝 Welcome Text", callback_data="SUB_welcome")],
            [InlineKeyboardButton("🎥 How-To-Use Video", callback_data="SUB_howvid")],
            [InlineKeyboardButton("💳 Payment Config", callback_data="SUB_pay")],
            [InlineKeyboardButton("💾 Backup & Restore Store", callback_data="GO_bk")]
        ]
        if uid == OWNER_ID:
            rows.append([InlineKeyboardButton("👑 Switch to Owner Panel", callback_data="GO_owner")])
            
        header = f"🛠️ **Admin Panel** ({sname})\n"
        header += f"⌛ **Time Remaining:** `{time_status}`\n"
        header += f"🔗 **Your Bot Store Link:**\n`{prod_link}`"
        
        edit_panel(uid, header, kb(*rows))
        return

    # OWNER PANEL
    if route == "owner":
        rows = [
            [InlineKeyboardButton("👥 Manage Sellers", callback_data="GO_sellers")],
            [InlineKeyboardButton("🚀 Custom BC", callback_data="SUB_cb")],
            [InlineKeyboardButton("⏱️ Auto BC", callback_data="SUB_ab")],
            [InlineKeyboardButton("👑 BC to Buyers", callback_data="SUB_bb")],
            [InlineKeyboardButton("💰 All Sales", callback_data="GO_sales")],
            [InlineKeyboardButton("📣 Send BC to Seller Users", callback_data="GO_ownbc")],
            [InlineKeyboardButton("⚙️ Single Seller Backup/Restore", callback_data="GO_single_bk")],
            [InlineKeyboardButton("💾 Backup & Restore", callback_data="GO_bk")],
            [InlineKeyboardButton("🛠️ Switch to Admin Panel", callback_data="GO_admin")]
        ]
        edit_panel(uid, "👑 **OWNER PANEL**", kb(*rows))
        return

    # MANAGE SELLERS
    if route == "sellers":
        s = "👥 **Sellers Management**\n\n"
        for k, st in DB_STATE["sellers"].items():
            name = st.get('seller_name', 'Seller')
            rem = format_time_left(st.get('expiry_time'))
            s += f"👤 **{name}** (`{k}`) | Time: `{rem}`\n"
        if not DB_STATE["sellers"]: s += "(No seller added yet)\n"
        
        edit_panel(uid, s, kb(
            [InlineKeyboardButton("➕ Add Seller", callback_data="W_addseller")],
            [InlineKeyboardButton("❌ Delete Seller", callback_data="GO_del_seller_menu")],
            [InlineKeyboardButton("⏳ Extend/Set Seller Time", callback_data="W_settime")],
            [BACK(uid)]))
        return

    # DELETE SELLER MENU
    if route == "del_seller_menu":
        if not DB_STATE["sellers"]:
            edit_panel(uid, "❌ No seller to delete.", kb([BACK(uid)]))
            return
        rows = []
        for k, st in DB_STATE["sellers"].items():
            sname = st.get("seller_name", f"Seller {k}")
            rows.append([InlineKeyboardButton(f"🗑️ Delete {sname}", callback_data=f"ACT_delseller_{k}")])
        rows.append([BACK(uid)])
        edit_panel(uid, "🗑️ Select a seller button below to delete:", kb(*rows))
        return

    if route == "single_bk":
        edit_panel(uid, "⚙️ **Single Seller / Specific Setting Backup & Restore**", kb(
            [InlineKeyboardButton("📤 Export Seller Backup", callback_data="W_expseller")],
            [InlineKeyboardButton("📥 Import Seller Backup", callback_data="W_impseller")],
            [BACK(uid)]))
        return

    if route == "sales":
        s = "💰 **Sales**\n"
        tot = 0
        def ln(nm, st):
            nonlocal tot
            c = len(st.get("buyers", [])); tot += c
            return f"• {nm}: {c}\n"
        s += ln("HUB Owner", DB_STATE["hub"])
        for k, st in DB_STATE["sellers"].items(): 
            s += ln(st.get("seller_name", k), st)
        s += f"\n**Total Sales: {tot}**"
        edit_panel(uid, s, kb([BACK(uid)]))
        return

    if route == "ownbc":
        s = "📣 Broadcast to seller's users:\n"
        for k, st in DB_STATE["sellers"].items():
            sname = st.get('seller_name', k)
            s += f"`bct_{k}` → **{sname}** ({len(st.get('users', []))} users)\n"
        s += "\nSend format: `bct_s1` then message."
        edit_panel(uid, s, kb([BACK(uid)]))
        return

    if route == "bk":
        edit_panel(uid, "💾 **Backup & Restore**", kb(
            [InlineKeyboardButton("⬇️ Download Backup File", callback_data="ACT_getbk")],
            [InlineKeyboardButton("📥 Restore Database", callback_data="W_restore")],
            [BACK(uid)]))
        return

    if route == "bl":
        st = store_of_key(key)
        if not st.get("buyers"):
            edit_panel(uid, "📦 No buyers found.", kb([BACK(uid)])); return
        sname = st.get("seller_name", key)
        s = f"📦 **Buyers List** ({sname})\n\n"
        for b in st["buyers"][-15:]:
            s += f"• {b.get('product')} | `{b.get('user_id')}` | {b.get('date')}\n"
        edit_panel(uid, s, kb([BACK(uid)]))
        return

    if route.startswith("SUB_"):
        sub = route[4:]
        if sub == "startvids":
            user_states[uid]["wait"] = "startvids"
            edit_panel(uid, f"📥 Send videos. Type `/done` when finished.\nTotal: {len(store_of_key(key)['start_videos'])}", kb([BACK(uid)]))
        elif sub == "welcome":
            user_states[uid]["wait"] = "welcome"
            edit_panel(uid, "📝 Send new Welcome text. You can use `{name}` placeholder.", kb([BACK(uid)]))
        elif sub == "howvid":
            user_states[uid]["wait"] = "howvid"
            edit_panel(uid, "🎥 Send 'How To Use' video.", kb([BACK(uid)]))
        elif sub == "pay":
            edit_panel(uid, "💳 **Payment Config**", kb(
                [InlineKeyboardButton("🖼️ Set QR Photo", callback_data="W_payphoto")],
                [InlineKeyboardButton("✏️ Set Payment Text", callback_data="W_paytext")],
                [BACK(uid)]))
        elif sub == "cb":
            user_states[uid]["wait"] = "cb"
            edit_panel(uid, "🚀 Send broadcast message (text/photo/video).", kb([BACK(uid)]))
        elif sub == "ab":
            user_states[uid]["wait"] = "ab_msg"
            edit_panel(uid, "⏱️ Send Auto-BC message.", kb([BACK(uid)]))
        elif sub == "bb":
            user_states[uid]["wait"] = "bb"
            edit_panel(uid, "👑 Send broadcast to buyers.", kb([BACK(uid)]))
        elif sub == "products":
            st = store_of_key(key)
            sname = st.get("seller_name", key)
            
            s = f"🛍️ **Product Button Management ({sname})**\n\n"
            s += "📌 **Add Product Method:**\n"
            s += "1. Send or Forward Video.\n"
            s += "2. Send Text: `Product Name https://link.com`\n\n"
            s += "📌 **Delete Product:** Click 'Delete Button' below or send `DEL_id`"
            
            # Buttons matching user screenshot (excluding Edit Details / Link)
            rows = [
                [InlineKeyboardButton("❇️ Add New Button", callback_data="W_addbutton")],
                [InlineKeyboardButton("🔢 Change Position", callback_data="W_posbutton")],
                [InlineKeyboardButton("🎥 Add Videos", callback_data="W_addvidprod")],
                [InlineKeyboardButton("⚙️ Manage Videos", callback_data="W_manvidprod")],
                [InlineKeyboardButton("🗑️ Delete Button", callback_data="W_delbutton")],
                [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="NAV_BACK")]
            ]
            edit_panel(uid, s, kb(*rows))
            user_states[uid]["wait"] = "prodcmd"
        return

    edit_panel(uid, "Menu", kb([BACK(uid)]))

# ---------------- CALLBACKS ----------------
@bot.callback_query_handler(func=lambda c: True)
def cb(c):
    try: bot.answer_callback_query(c.id)
    except Exception: pass
    uid = c.message.chat.id
    d = c.data
    ctx = user_states.setdefault(uid, {})
    ctx.setdefault("nav", [])
    key = ctx.get("key", "hub")

    # USER ACTION HANDLERS (Product & How to use button fix)
    if d.startswith("home_") or d == "HOME":
        st = store_of_key(key)
        send_menu(uid, st, (c.message.from_user.first_name or "User")); return
    
    if d == "HOW":
        st = store_of_key(key)
        v = st.get("how_to_use_video")
        if v: bot.send_video(uid, v)
        else: bot.send_message(uid, "ℹ️ 'How to use' video not set.")
        return

    if d == "REP":
        ctx["wait"] = "report"
        bot.send_message(uid, "📝 Type your report/problem:")
        return

    if d.startswith("BUY_"):
        pid = d.split("_")[1]
        st = store_of_key(key)
        p = next((x for x in st.get("products", []) if str(x["id"]) == str(pid)), None)
        if not p:
            bot.send_message(uid, "❌ Product not found.")
            return
        if p.get("videos"): 
            send_videos(uid, p["videos"])
        cap = "📌 **" + p["name"] + "**"
        pay = p.get("pay_msg") or st["payment_msg"]
        mk = kb([InlineKeyboardButton("I have paid ✅", callback_data="PAID_" + str(pid))],
                [InlineKeyboardButton("Back 🔙", callback_data="HOME")])
        if st.get("payment_photo"):
            bot.send_photo(uid, st["payment_photo"], caption=cap + "\n\n" + pay, reply_markup=mk, parse_mode="Markdown")
        else:
            bot.send_message(uid, cap + "\n\n" + pay, reply_markup=mk, parse_mode="Markdown")
        return

    if d.startswith("PAID_"):
        ctx["wait"] = "shot"; ctx["pid"] = d.split("_")[1]
        bot.send_message(uid, "📸 Send payment screenshot.")
        return

    if d == "none": return

    # Panel Navigation Switches
    if d == "GO_owner": push(uid, "owner"); render(uid); return
    if d == "GO_admin": push(uid, "admin"); render(uid); return
    if d == "GO_sellers": push(uid, "sellers"); render(uid); return
    if d == "GO_del_seller_menu": push(uid, "del_seller_menu"); render(uid); return
    if d == "GO_single_bk": push(uid, "single_bk"); render(uid); return
    if d == "GO_sales": push(uid, "sales"); render(uid); return
    if d == "GO_ownbc": push(uid, "ownbc"); render(uid); return
    if d == "GO_bk": push(uid, "bk"); render(uid); return
    if d == "GO_bl": push(uid, "bl"); render(uid); return

    if d.startswith("SUB_"):
        push(uid, d); render(uid); return

    if d == "NAV_BACK":
        if not ctx["nav"]:
            push(uid, "admin")
            render(uid)
        else:
            back(uid)
            render(uid)
        return

    if d.startswith("W_"):
        ctx["wait"] = d[2:].lower()
        if ctx["wait"] == "addbutton":
            edit_panel(uid, "❇️ Send details to add button:\nFormat: `Product Name https://link.com`", kb([BACK(uid)]))
        elif ctx["wait"] == "posbutton":
            edit_panel(uid, "🔢 Send product position format:\n`ID NewPosition` (e.g., `1 2`)", kb([BACK(uid)]))
        elif ctx["wait"] == "addvidprod":
            edit_panel(uid, "🎥 Send or Forward a Video now.", kb([BACK(uid)]))
        elif ctx["wait"] == "manvidprod":
            st = store_of_key(key)
            txt = "⚙️ **Product Videos Count:**\n\n"
            for p in st.get("products", []):
                txt += f"• ID `{p['id']}` ({p['name']}): {len(p.get('videos', []))} vids\n"
            edit_panel(uid, txt, kb([BACK(uid)]))
        elif ctx["wait"] == "delbutton":
            st = store_of_key(key)
            txt = "🗑️ **Delete Button:**\nSend `DEL_id` (e.g. `DEL_1`)\n\n"
            for p in st.get("products", []):
                txt += f"🆔 `{p['id']}` - {p['name']}\n"
            edit_panel(uid, txt, kb([BACK(uid)]))
        elif ctx["wait"] == "addseller":
            edit_panel(uid, "Format: Send Telegram ID & Time\nExample: `123456789 1d` or `123456789 12h`", kb([BACK(uid)]))
        elif ctx["wait"] == "settime":
            edit_panel(uid, "Format: `s1 2d` or `s1 +1d`", kb([BACK(uid)]))
        elif ctx["wait"] == "expseller":
            edit_panel(uid, "Seller ID type (e.g. `s1`):", kb([BACK(uid)]))
        elif ctx["wait"] == "impseller":
            edit_panel(uid, "Send Seller JSON Data:", kb([BACK(uid)]))
        elif ctx["wait"] == "payphoto":
            edit_panel(uid, "Send QR Photo.", kb([BACK(uid)]))
        elif ctx["wait"] == "paytext":
            edit_panel(uid, "Send Payment Text.", kb([BACK(uid)]))
        elif ctx["wait"] == "restore":
            edit_panel(uid, "📥 Upload `.json` File or Backup Code.", kb([BACK(uid)]))
        return

    if d.startswith("ACT_"):
        act = d[4:]
        if act == "togglelayout":
            st = store_of_key(key)
            st["layout_style"] = "vertical" if st.get("layout_style") != "vertical" else "horizontal"
            save_db(); render(uid)
        elif act.startswith("delseller_"):
            sid = act.split("_", 1)[1]
            if sid in DB_STATE["sellers"]:
                sname = DB_STATE["sellers"][sid].get("seller_name", sid)
                del DB_STATE["sellers"][sid]
                save_db()
                bot.send_message(uid, f"✅ Seller **{sname}** deleted successfully!")
            back(uid)
            render(uid)
        elif act == "getbk":
            bot.send_document(uid, ("bot_backup.json", json.dumps(DB_STATE, indent=2).encode()))
            edit_panel(uid, "✅ Full Backup file sent.", kb(
                [InlineKeyboardButton("📥 Restore Now", callback_data="W_restore")],
                [BACK(uid)]))
        return

# ---------------- MAIN USER MENU ----------------
def send_menu(uid, store, name):
    if store.get("start_videos"): send_videos(uid, store["start_videos"])
    welcome = store.get("welcome_msg", "{name}").format(name=name)
    mk = InlineKeyboardMarkup()
    prods = sorted(store.get("products", []), key=lambda x: x.get("position", 999))
    if store.get("layout_style") == "horizontal":
        row = []
        for p in prods:
            row.append(InlineKeyboardButton(p["name"], callback_data="BUY_" + str(p["id"])))
            if len(row) == 2: mk.row(*row); row = []
        if row: mk.row(*row)
    else:
        for p in prods: mk.row(InlineKeyboardButton(p["name"], callback_data="BUY_" + str(p["id"])))
    
    mk.row(InlineKeyboardButton("How to use ❓", callback_data="HOW"),
           InlineKeyboardButton("Report 📩", callback_data="REP"))
    try: bot.send_message(uid, welcome, reply_markup=mk, parse_mode="Markdown")
    except Exception: pass

# /start Command Handler
@bot.message_handler(commands=['start'])
def start_cmd(m):
    uid = m.chat.id; name = m.from_user.first_name or "User"
    txt = m.text or ""
    payload = txt.split(" ", 1)[1] if " " in txt else ""
    payload = payload.split("?")[0].replace("/start", "").strip()
    ctx = user_states.setdefault(uid, {})

    # OWNER Fast Admin Open
    if uid == OWNER_ID:
        ctx.clear(); ctx["mode"] = "owner"; ctx["key"] = "hub"; ctx["nav"] = ["admin"]
        render(uid); return

    sk = key_of_seller_tg(uid)
    if sk:
        ctx.clear(); ctx["mode"] = "seller"; ctx["key"] = sk; ctx["nav"] = ["admin"]
        render(uid); return

    if payload.startswith("s") and payload[1:].isdigit():
        key = payload
        st = DB_STATE["sellers"].get(key)
        if not st:
            bot.send_message(uid, "❌ Store link invalid or expired."); return
        ctx["mode"] = "user"; ctx["key"] = key; ctx["nav"] = []
        if uid not in st["users"]: st["users"].append(uid); save_db()
        send_menu(uid, st, name)
        return

    ctx["mode"] = "user"; ctx["key"] = "hub"; ctx["nav"] = []
    h = DB_STATE["hub"]
    if uid not in h["users"]: h["users"].append(uid); save_db()
    send_menu(uid, h, name)

# Payment confirmation buttons
@bot.callback_query_handler(func=lambda c: c.data.startswith(("adm_confirm_", "adm_reject_", "adm_block_")))
def confirm_cb(c):
    try: bot.answer_callback_query(c.id)
    except Exception: pass
    uid = c.message.chat.id; d = c.data
    try:
        if d.startswith("adm_confirm_"):
            parts = d.split("_")
            key, pid, tu = parts[2], parts[3], int(parts[4])
            st = store_of_key(key)
            p = next((x for x in st["products"] if str(x["id"]) == str(pid)), None)
            link = p["link"] if p else "No link"
            nm = p["name"] if p else "Product"
            st["buyers"].append({"user_id": tu, "name": "User", "username": "?", "product": nm,
                                 "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")})
            save_db()
            bot.send_message(tu, "✅ **Payment Confirmed!**\n\n🔗 " + link, parse_mode="Markdown")
        elif d.startswith("adm_reject_"):
            tu = int(d.split("_")[2]); bot.send_message(tu, "❌ Payment not received. Please try again...")
        elif d.startswith("adm_block_"):
            tu = int(d.split("_")[2])
            k = key_of_seller_tg(uid) or "hub"
            st = store_of_key(k)
            if tu not in st["blocked_users"]: st["blocked_users"].append(tu); save_db()
    except Exception as e: print("confirm err", e)
    try: bot.delete_message(uid, c.message.message_id)
    except Exception: pass

# ---------------- TEXT / MEDIA / COMMAND INPUT ----------------
@bot.message_handler(func=lambda m: True, content_types=['text','photo','video','document'])
def inp(m):
    uid = m.chat.id
    txt = (m.text or m.caption or "").strip()
    ctx = user_states.setdefault(uid, {})
    key = ctx.get("key", "hub")
    st = store_of_key(key)
    wait = ctx.get("wait")

    if txt in ("/done", "/cancel"):
        ctx.pop("wait", None)
        bot.send_message(uid, "✅ Cancelled / Completed.")
        delete_msg_safe(uid, m.message_id)
        return

    if wait == "report":
        ctx.pop("wait", None)
        dest = OWNER_ID
        if key != "hub":
            s = DB_STATE["sellers"].get(key)
            dest = (s or {}).get("tg_id") or OWNER_ID
        tag = "@" + m.from_user.username if m.from_user.username else "NoUser"
        bot.send_message(dest, f"📩 Report from {tag} (`{uid}`):\n\n{txt}", parse_mode="Markdown")
        bot.send_message(uid, "✅ Report sent successfully.")
        return

    if wait == "shot" and m.content_type == "photo":
        ctx.pop("wait", None)
        pid = ctx.get("pid")
        p = next((x for x in st["products"] if str(x["id"]) == str(pid)), None)
        nm = p["name"] if p else "Product"
        if key == "hub":
            adm = OWNER_ID; label = "Owner Hub"
            mk = kb([InlineKeyboardButton("CONFIRM ✅", callback_data=f"adm_confirm_hub_{pid}_{uid}"),
                     InlineKeyboardButton("REJECT ❌", callback_data=f"adm_reject_{uid}"),
                     InlineKeyboardButton("BLOCK 🚫", callback_data=f"adm_block_{uid}")])
        else:
            s = DB_STATE["sellers"].get(key, {})
            adm = s.get("tg_id") or OWNER_ID; label = s.get("seller_name", key)
            mk = kb([InlineKeyboardButton("CONFIRM ✅", callback_data=f"adm_confirm_{key}_{pid}_{uid}"),
                     InlineKeyboardButton("REJECT ❌", callback_data=f"adm_reject_{uid}"),
                     InlineKeyboardButton("BLOCK 🚫", callback_data=f"adm_block_{uid}")])
        tag = "@" + m.from_user.username if m.from_user.username else "NoUser"
        bot.send_message(uid, "⏳ Checking payment... Please wait 5-10 minutes.")
        try:
            bot.send_photo(adm, m.photo[-1].file_id,
                caption=f"📸 **New Payment!**\nStore: {label}\nProduct: {nm}\nUser: {tag}\nID: `{uid}`",
                reply_markup=mk, parse_mode="Markdown")
        except Exception as e: print("shot err", e)
        return

    is_owner = (uid == OWNER_ID)
    seller_key = key_of_seller_tg(uid)
    if not is_owner and not seller_key: return

    # ========= RESTORE DATABASE =========
    if wait == "restore":
        try:
            if m.content_type == "document":
                f = bot.get_file(m.document.file_id)
                data = json.loads(bot.download_file(f.file_path).decode())
            else:
                data = json.loads(txt)
            d2 = fresh_state(); d2.update(data)
            d2.setdefault("hub", new_store()); d2.setdefault("sellers", {})
            DB_STATE.clear(); DB_STATE.update(d2); save_db()
            ctx.pop("wait", None)
            bot.send_message(uid, "✅ Restore Complete! Type /start")
        except Exception as e:
            bot.send_message(uid, f"❌ Restore error: {e}")
        delete_msg_safe(uid, m.message_id)
        return

    if is_owner:
        if wait == "expseller":
            sid = txt.strip()
            if sid in DB_STATE["sellers"]:
                s_data = json.dumps({sid: DB_STATE["sellers"][sid]}, indent=2)
                bot.send_document(uid, (f"{sid}_backup.json", s_data.encode()))
                bot.send_message(uid, f"✅ Backup for `{sid}` sent successfully.")
            else:
                bot.send_message(uid, f"❌ Seller `{sid}` not found.")
            delete_msg_safe(uid, m.message_id)
            ctx.pop("wait", None); render(uid); return

        if wait == "impseller":
            try:
                if m.content_type == "document":
                    f = bot.get_file(m.document.file_id)
                    data = json.loads(bot.download_file(f.file_path).decode())
                else:
                    data = json.loads(txt)
                for sid, s_store in data.items():
                    DB_STATE["sellers"][sid] = s_store
                save_db()
                bot.send_message(uid, "✅ Specific seller data restored successfully!")
            except Exception as e:
                bot.send_message(uid, f"❌ Single restore error: {e}")
            delete_msg_safe(uid, m.message_id)
            ctx.pop("wait", None); render(uid); return

        if wait == "addseller":
            try:
                parts = txt.split()
                tg_id = int(parts[0])
                time_str = parts[1] if len(parts) > 1 else "1d"

                try:
                    user_chat = bot.get_chat(tg_id)
                    seller_name = user_chat.first_name or f"Seller_{tg_id}"
                except Exception:
                    seller_name = f"Seller_{tg_id}"

                sid = f"s{len(DB_STATE['sellers']) + 1}"
                s = new_store()
                s["tg_id"] = tg_id
                s["seller_name"] = seller_name
                s["broadcast_perm"] = False
                
                sec = parse_duration(time_str)
                if sec:
                    s["expiry_time"] = time.time() + sec

                DB_STATE["sellers"][sid] = s
                save_db()
                bot.send_message(uid, f"✅ Seller **{seller_name}** (`{sid}`) added successfully!")
            except Exception:
                bot.send_message(uid, "❌ Format: `123456789 1d` (TG_ID Time)")
            delete_msg_safe(uid, m.message_id)
            ctx.pop("wait", None); render(uid); return

        if wait == "settime":
            try:
                parts = txt.split()
                sid = parts[0]
                t_val = parts[1]
                if sid in DB_STATE["sellers"]:
                    is_add = t_val.startswith("+")
                    clean_t = t_val.lstrip("+")
                    sec = parse_duration(clean_t)
                    if sec:
                        curr = DB_STATE["sellers"][sid].get("expiry_time")
                        now = time.time()
                        if is_add and curr and curr > now:
                            DB_STATE["sellers"][sid]["expiry_time"] = curr + sec
                        else:
                            DB_STATE["sellers"][sid]["expiry_time"] = now + sec
                        save_db()
                        bot.send_message(uid, f"✅ Time updated for `{sid}`.")
                    else:
                        bot.send_message(uid, "❌ Invalid time format.")
                else:
                    bot.send_message(uid, f"❌ Seller `{sid}` not found.")
            except Exception:
                bot.send_message(uid, "❌ Format: `s1 2d` or `s1 +1d`")
            delete_msg_safe(uid, m.message_id)
            ctx.pop("wait", None); render(uid); return

        if wait == "ownbc" and txt.startswith("bct_"):
            sid = txt.split("_", 1)[1]
            ctx["_bc_key"] = sid; ctx["wait"] = "cb"
            delete_msg_safe(uid, m.message_id)
            edit_panel(uid, f"{sid} users broadcast message:", kb([BACK(uid)])); return

    if wait == "startvids" and m.content_type == "video":
        st["start_videos"].append(m.video.file_id); save_db()
        delete_msg_safe(uid, m.message_id)
        edit_panel(uid, f"✅ Video added! Total: {len(st['start_videos'])}", kb([BACK(uid)]))
        return

    if wait == "welcome":
        st["welcome_msg"] = txt; save_db(); ctx.pop("wait", None)
        delete_msg_safe(uid, m.message_id)
        render(uid); return

    if wait == "howvid" and m.content_type == "video":
        st["how_to_use_video"] = m.video.file_id; save_db(); ctx.pop("wait", None)
        delete_msg_safe(uid, m.message_id)
        render(uid); return

    if wait == "payphoto" and m.content_type == "photo":
        st["payment_photo"] = m.photo[-1].file_id; save_db(); ctx.pop("wait", None)
        delete_msg_safe(uid, m.message_id)
        render(uid); return

    if wait == "paytext":
        st["payment_msg"] = txt; save_db(); ctx.pop("wait", None)
        delete_msg_safe(uid, m.message_id)
        render(uid); return

    if wait == "cb":
        do_bc(uid, st.get("users", []), m); ctx.pop("wait", None)
        delete_msg_safe(uid, m.message_id)
        render(uid); return

    if wait == "bb":
        do_bc(uid, [b.get("user_id") for b in st.get("buyers", [])], m); ctx.pop("wait", None)
        delete_msg_safe(uid, m.message_id)
        render(uid); return

    if wait == "ab_msg":
        store_ab(st, m); save_db(); ctx["wait"] = "ab_time"
        delete_msg_safe(uid, m.message_id)
        edit_panel(uid, "Interval seconds (e.g. 3600 = 1 hour):", kb([BACK(uid)])); return

    if wait == "ab_time" and m.content_type == "text":
        try:
            st["auto_bc"]["interval_seconds"] = int(txt); st["auto_bc"]["status"] = True; save_db()
            bot.send_message(uid, f"✅ Auto-BC ON (Interval: {int(txt)}s)")
        except Exception: bot.send_message(uid, "❌ Send correct number.")
        delete_msg_safe(uid, m.message_id)
        ctx.pop("wait", None); render(uid); return

    # ========= FORWARD VIDEO & FAST PRODUCT ADD METHOD (WITH AUTO-DELETE) =========
    if m.content_type == "video" or wait in ("prodcmd", "addbutton", "addvidprod", "posbutton"):
        st2 = store_of_key(key)

        # 1. Video Add (Direct/Forward)
        if m.content_type == "video":
            vid_id = m.video.file_id
            if not st2["products"]:
                pid = "1"
                st2["products"].append({
                    "id": pid, "name": "New Product", "videos": [vid_id],
                    "link": "https://example.com", "position": 1
                })
            else:
                p = st2["products"][-1]
                p.setdefault("videos", []).append(vid_id)
                pid = p["id"]
            save_db()
            delete_msg_safe(uid, m.message_id)  # Auto delete user video message to clean chat
            render(uid)
            return

        # 2. Position Change Handler
        if wait == "posbutton":
            try:
                parts = txt.split()
                pid, pos = parts[0], int(parts[1])
                for p in st2["products"]:
                    if str(p["id"]) == str(pid):
                        p["position"] = pos
                save_db()
            except Exception: pass
            delete_msg_safe(uid, m.message_id)
            ctx.pop("wait", None); render(uid); return

        # 3. Add Name & Link via Simple Text
        if "http://" in txt or "https://" in txt:
            parts = txt.split()
            link = next((x for x in parts if x.startswith("http://") or x.startswith("https://")), None)
            if link:
                name_parts = [x for x in parts if x != link]
                pname = " ".join(name_parts) if name_parts else "Product"
                
                if st2["products"] and st2["products"][-1].get("name") == "New Product":
                    p = st2["products"][-1]
                    p["name"] = pname
                    p["link"] = link
                else:
                    pid = str(len(st2["products"]) + 1)
                    st2["products"].append({
                        "id": pid, "name": pname, "videos": [],
                        "link": link, "position": len(st2["products"]) + 1
                    })
                save_db()
                delete_msg_safe(uid, m.message_id)  # Auto delete user text message
                render(uid)
                return

        # Delete Product Command
        if txt.startswith("DEL_") or txt.startswith("DEL "):
            parts = txt.replace("DEL ", "DEL_").split("_", 1)
            if len(parts) == 2:
                pid = parts[1].strip()
                st2["products"] = [x for x in st2["products"] if str(x["id"]) != str(pid)]
                save_db()
                delete_msg_safe(uid, m.message_id)
                render(uid)
            return

def store_ab(st, m):
    if m.content_type == "text":
        st["auto_bc"] = {"status": True, "interval_seconds": 3600, "message_type": "text", "file_id": None, "text": m.text}
    elif m.content_type == "photo":
        st["auto_bc"] = {"status": True, "interval_seconds": 3600, "message_type": "photo", "file_id": m.photo[-1].file_id, "text": m.caption or ""}
    elif m.content_type == "video":
        st["auto_bc"] = {"status": True, "interval_seconds": 3600, "message_type": "video", "file_id": m.video.file_id, "text": m.caption or ""}

def do_bc(uid, usrs, m):
    ok = fail = 0
    for u in usrs:
        try:
            if m.content_type == "text": bot.send_message(u, m.text, parse_mode="Markdown")
            elif m.content_type == "photo": bot.send_photo(u, m.photo[-1].file_id, caption=m.caption, parse_mode="Markdown")
            elif m.content_type == "video": bot.send_video(u, m.video.file_id, caption=m.caption, parse_mode="Markdown")
            elif m.content_type == "document": bot.send_document(u, m.document.file_id, caption=m.caption, parse_mode="Markdown")
            ok += 1
        except Exception: fail += 1
    bot.send_message(uid, f"✅ Sent: {ok} | Failed: {fail}")

# ---------------- AUTO-BC WORKER ----------------
def worker():
    while True:
        try:
            for st in list(DB_STATE["sellers"].values()): run_ab(st)
            run_ab(DB_STATE["hub"])
        except Exception: pass
        time.sleep(2)

def run_ab(st):
    bc = st.get("auto_bc", {})
    if not bc.get("status"): return
    now = int(time.time())
    if bc.get("last") and now - bc["last"] < bc.get("interval_seconds", 3600): return
    bc["last"] = now; save_db()
    for u in list(st.get("users", [])):
        if u in st.get("blocked_users", []): continue
        try:
            t = bc.get("message_type")
            if t == "photo": bot.send_photo(u, bc["file_id"], caption=bc.get("text"), parse_mode="Markdown")
            elif t == "video": bot.send_video(u, bc["file_id"], caption=bc.get("text"), parse_mode="Markdown")
            elif t == "document": bot.send_document(u, bc["file_id"], caption=bc.get("text"), parse_mode="Markdown")
            else: bot.send_message(u, bc.get("text"), parse_mode="Markdown")
        except Exception: pass

@app.route('/')
def home(): return "Bot running successfully!"

if __name__ == "__main__":
    threading.Thread(target=lambda: bot.infinity_polling(), daemon=True).start()
    threading.Thread(target=worker, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
