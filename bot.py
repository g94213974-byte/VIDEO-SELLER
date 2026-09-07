import os, json, threading, time, datetime
from flask import Flask
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaVideo

TOKEN   = os.environ.get('BOT_TOKEN')
OWNER_ID = int(os.environ.get('OWNER_ID', '0'))
LOG_CHANNEL_ID = int(os.environ.get('LOG_CHANNEL_ID', '0'))

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

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
    }

def fresh_state():
    return {"sellers": {}, "hub": new_store(), "takeovers": []}

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
            d.setdefault("hub", new_store()); d.setdefault("sellers", {}); d.setdefault("takeovers", [])
            DB_STATE = d
    except Exception as e:
        print("No DB yet:", e); save_db()

load_db()

# ---------------- HELPERS ----------------
def store_of_key(key):
    return DB_STATE["hub"] if key in ("hub", None) else DB_STATE["sellers"].get(key, new_store())

def key_of_seller_tg(tg):
    for k, s in DB_STATE["sellers"].items():
        if s.get("tg_id") == tg: return k
    return None

def now_min():
    n = datetime.datetime.now(); return n.hour*60 + n.minute

def takeover_of(key):
    m = now_min()
    for t in DB_STATE["takeovers"]:
        if t.get("seller") == key and t.get("active") and t["from_min"] <= m < t["to_min"]:
            return t
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

    # ADMIN PANEL (Custom BC, Auto BC, BC to Buyers - এই ৩টি অপশন সম্পূর্ণ সরিয়ে দেওয়া হয়েছে)
    if route == "admin" or route == "seller":
        store = store_of_key(key)
        lp = "↔️ Horizontal" if store.get("layout_style") == "horizontal" else "↕️ Vertical"
        
        rows = [
            [InlineKeyboardButton("🛍️ Products", callback_data="SUB_products")],
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
            
        edit_panel(uid, f"🛠️ **Admin Panel**\n\nStore: `{key}`", kb(*rows))
        return

    # OWNER PANEL (সকল Broadcast সুবিধা শুধুমাত্র Owner এর জন্য রাখা হলো)
    if route == "owner":
        rows = [
            [InlineKeyboardButton("👥 Manage Sellers", callback_data="GO_sellers")],
            [InlineKeyboardButton("⏱️ Takeover Schedule", callback_data="GO_tk")],
            [InlineKeyboardButton("🚀 Custom BC", callback_data="SUB_cb")],
            [InlineKeyboardButton("⏱️ Auto BC", callback_data="SUB_ab")],
            [InlineKeyboardButton("👑 BC to Buyers", callback_data="SUB_bb")],
            [InlineKeyboardButton("💰 All Sales", callback_data="GO_sales")],
            [InlineKeyboardButton("📣 Send BC to Seller Users", callback_data="GO_ownbc")],
            [InlineKeyboardButton("💾 Backup & Restore", callback_data="GO_bk")],
            [InlineKeyboardButton("🛠️ Switch to Admin Panel", callback_data="GO_admin")]
        ]
        edit_panel(uid, "👑 **OWNER PANEL**", kb(*rows))
        return

    if route == "sellers":
        s = "👥 **Sellers**\n\n"
        for k, st in DB_STATE["sellers"].items():
            s += f"`{k}` | tg:{st.get('tg_id')} | BC:{'✅' if st.get('broadcast_perm') else '❌'}\n"
        if not DB_STATE["sellers"]: s += "(No seller yet)\n"
        edit_panel(uid, s, kb(
            [InlineKeyboardButton("➕ Add Seller", callback_data="W_addseller")],
            [InlineKeyboardButton("🔁 Toggle BC Permission", callback_data="W_toggleperm")],
            [BACK(uid)]))
        return

    if route == "tk":
        s = "⏱️ **Takeover Schedule** (server time)\n"
        for i, t in enumerate(DB_STATE["takeovers"]):
            s += f"{i}. `{t['seller']}` {t['from_min']//60}:{t['from_min']%60:02d}-{t['to_min']//60}:{t['to_min']%60:02d} active={'✅' if t['active'] else '❌'}\n"
        if not DB_STATE["takeovers"]: s += "(None yet)\n"
        edit_panel(uid, s, kb(
            [InlineKeyboardButton("➕ Add (seller fromHH:MM toHH:MM)", callback_data="W_tkadd")],
            [InlineKeyboardButton("↕️ Toggle / ❌ Delete", callback_data="W_tkmgmt")],
            [BACK(uid)]))
        return

    if route == "sales":
        s = "💰 **Sales**\n"
        tot = 0
        def ln(nm, st):
            nonlocal tot
            c = len(st.get("buyers", [])); tot += c
            return f"• {nm}: {c}\n"
        s += ln("HUB", DB_STATE["hub"])
        for k, st in DB_STATE["sellers"].items(): s += ln(k, st)
        s += f"\n**Total: {tot}**"
        edit_panel(uid, s, kb([BACK(uid)]))
        return

    if route == "ownbc":
        s = "📣 Broadcast to seller's users:\n"
        for k, st in DB_STATE["sellers"].items():
            s += f"`bct_{k}` → {k} ({len(st.get('users', []))} users)\n"
        s += "\nSend: `bct_s1` then message."
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
        s = f"📦 **Buyers List** ({key})\n\n"
        for b in st["buyers"][-15:]:
            s += f"• {b.get('product')} | `{b.get('user_id')}` | {b.get('date')}\n"
        edit_panel(uid, s, kb([BACK(uid)]))
        return

    if route.startswith("SUB_"):
        sub = route[4:]
        if sub == "startvids":
            user_states[uid]["wait"] = "startvids"
            edit_panel(uid, f"📥 Video pathan. Done er jonno `/done` type korun.\nTotal: {len(store_of_key(key)['start_videos'])}", kb([BACK(uid)]))
        elif sub == "welcome":
            user_states[uid]["wait"] = "welcome"
            edit_panel(uid, "📝 Noya Welcome text pathan. `{name}` use korte parben.", kb([BACK(uid)]))
        elif sub == "howvid":
            user_states[uid]["wait"] = "howvid"
            edit_panel(uid, "🎥 'How To Use' video pathan.", kb([BACK(uid)]))
        elif sub == "pay":
            edit_panel(uid, "💳 **Payment Config**", kb(
                [InlineKeyboardButton("🖼️ Set QR Photo", callback_data="W_payphoto")],
                [InlineKeyboardButton("✏️ Set Payment Text", callback_data="W_paytext")],
                [BACK(uid)]))
        elif sub == "cb":
            user_states[uid]["wait"] = "cb"
            edit_panel(uid, "🚀 Broadcast message pathan (text/photo/video).", kb([BACK(uid)]))
        elif sub == "ab":
            user_states[uid]["wait"] = "ab_msg"
            edit_panel(uid, "⏱️ Auto-BC message pathan. Erpor interval time set korben.", kb([BACK(uid)]))
        elif sub == "bb":
            user_states[uid]["wait"] = "bb"
            edit_panel(uid, "👑 Buyers der broadcast message pathan.", kb([BACK(uid)]))
        elif sub == "products":
            st = store_of_key(key)
            s = f"🛍️ **Products Management** ({key})\n\n"
            for p in st["products"]:
                s += f"🆔 `{p['id']}` - **{p['name']}**\n🔗 Link: {p.get('link','None')}\n\n"
            s += "📌 **Commands:**\n"
            s += "• Add Product: `ADD_Product Name`\n"
            s += "• Set Link: `LINK_id_url`\n"
            s += "• Set Description: `DESC_id_text`\n"
            s += "• Add Video: `VID_id` (Erpor video pathaben)\n"
            s += "• Delete Product: `DEL_id`"
            edit_panel(uid, s, kb([BACK(uid)]))
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

    if d.startswith("home_"):
        st = store_of_key(key)
        send_menu(uid, st, (c.message.from_user.first_name or "User")); return
    if d == "none": return

    # Panel Navigation Switches
    if d == "GO_owner": push(uid, "owner"); render(uid); return
    if d == "GO_admin": push(uid, "admin"); render(uid); return
    if d == "GO_sellers": push(uid, "sellers"); render(uid); return
    if d == "GO_tk": push(uid, "tk"); render(uid); return
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
        if ctx["wait"] == "addseller":
            edit_panel(uid, "Format:\n`s1 123456789 Ramesh`", kb([BACK(uid)]))
        elif ctx["wait"] == "toggleperm":
            s = "Toggle BC Permission:\n"
            for k, st in DB_STATE["sellers"].items():
                s += f"`perm_{k}` → {k} ({'ON' if st.get('broadcast_perm') else 'OFF'})\n"
            edit_panel(uid, s or "No sellers.", kb([BACK(uid)]))
        elif ctx["wait"] == "tkadd":
            edit_panel(uid, "Format:\n`s1 02:00 06:19`", kb([BACK(uid)]))
        elif ctx["wait"] == "tkmgmt":
            edit_panel(uid, "Format:\n`tog_0` or `deltk_0`", kb([BACK(uid)]))
        elif ctx["wait"] == "payphoto":
            edit_panel(uid, "QR photo pathan.", kb([BACK(uid)]))
        elif ctx["wait"] == "paytext":
            edit_panel(uid, "Payment Text pathan.", kb([BACK(uid)]))
        elif ctx["wait"] == "restore":
            edit_panel(uid, "📥 Backup Code/JSON Text pathan othoba `.json` File upload korun.", kb([BACK(uid)]))
        return

    if d.startswith("ACT_"):
        act = d[4:]
        if act == "togglelayout":
            st = store_of_key(key)
            st["layout_style"] = "vertical" if st.get("layout_style") != "vertical" else "horizontal"
            save_db(); render(uid)
        elif act == "getbk":
            bot.send_document(uid, ("bot_backup.json", json.dumps(DB_STATE, indent=2).encode()))
            edit_panel(uid, "✅ Full Backup file pathano hoyeche.", kb(
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
            row.append(InlineKeyboardButton(p["name"], callback_data="BUY_" + p["id"]))
            if len(row) == 2: mk.row(*row); row = []
        if row: mk.row(*row)
    else:
        for p in prods: mk.row(InlineKeyboardButton(p["name"], callback_data="BUY_" + p["id"]))
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
            bot.send_message(uid, "❌ Store link invalid."); return
        ctx["mode"] = "user"; ctx["key"] = key; ctx["nav"] = []
        if takeover_of(key):
            ctx["_src"] = key
            ctx["key"] = "hub"
            h = DB_STATE["hub"]
            if uid not in h["users"]: h["users"].append(uid); save_db()
            bot.send_message(uid, "🛒 This store is currently open on **Owner Hub**.")
            send_menu(uid, h, name)
        else:
            if uid not in st["users"]: st["users"].append(uid); save_db()
            send_menu(uid, st, name)
        return

    ctx["mode"] = "user"; ctx["key"] = "hub"; ctx["nav"] = []
    h = DB_STATE["hub"]
    if uid not in h["users"]: h["users"].append(uid); save_db()
    send_menu(uid, h, name)

# User Callback Handler
@bot.callback_query_handler(func=lambda c: c.data.startswith(("BUY_", "HOW", "REP")))
def user_cb(c):
    try: bot.answer_callback_query(c.id)
    except Exception: pass
    uid = c.message.chat.id; d = c.data
    ctx = user_states.setdefault(uid, {})
    key = ctx.get("key", "hub"); st = store_of_key(key)
    
    if d == "HOW":
        v = st.get("how_to_use_video")
        if v: bot.send_video(uid, v)
        else: bot.send_message(uid, "ℹ️ Video set kora nai.")
        return
    if d == "REP":
        ctx["wait"] = "report"; bot.send_message(uid, "📝 Apnar report/problem likhun:"); return
    if d.startswith("BUY_"):
        pid = d.split("_")[1]
        p = next((x for x in st["products"] if x["id"] == pid), None)
        if not p: return
        if p.get("videos"): send_videos(uid, p["videos"])
        cap = "📌 **" + p["name"] + "**"
        if p.get("desc"): cap += "\n\n" + p["desc"]
        pay = p.get("pay_msg") or st["payment_msg"]
        mk = kb([InlineKeyboardButton("I have paid ✅", callback_data="PAID_" + pid)],
                [InlineKeyboardButton("Back 🔙", callback_data="HOME")])
        if st.get("payment_photo"):
            bot.send_photo(uid, st["payment_photo"], caption=cap + "\n\n" + pay, reply_markup=mk, parse_mode="Markdown")
        else:
            bot.send_message(uid, cap + "\n\n" + pay, reply_markup=mk, parse_mode="Markdown")
    if d == "HOME":
        send_menu(uid, store_of_key(ctx.get("key","hub")), c.message.from_user.first_name or "User")
    if d.startswith("PAID_"):
        ctx["wait"] = "shot"; ctx["pid"] = d.split("_")[1]
        bot.send_message(uid, "📸 Payment screenshot pathan.")

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
            p = next((x for x in st["products"] if x["id"] == pid), None)
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
        bot.send_message(uid, "✅ Cancelled / Completed."); return

    if wait == "report":
        ctx.pop("wait", None)
        dest = OWNER_ID
        if key != "hub" and not ctx.get("_src"):
            s = DB_STATE["sellers"].get(key)
            dest = (s or {}).get("tg_id") or OWNER_ID
        tag = "@" + m.from_user.username if m.from_user.username else "NoUser"
        bot.send_message(dest, f"📩 Report from {tag} (`{uid}`):\n\n{txt}", parse_mode="Markdown")
        bot.send_message(uid, "✅ Report send hoyeche.")
        return

    if wait == "shot" and m.content_type == "photo":
        ctx.pop("wait", None)
        pid = ctx.get("pid")
        p = next((x for x in st["products"] if x["id"] == pid), None)
        nm = p["name"] if p else "Product"
        if key == "hub":
            adm = OWNER_ID; src = ctx.get("_src")
            label = "HUB/OWNER" + (f" (via {src})" if src else "")
            mk = kb([InlineKeyboardButton("CONFIRM ✅", callback_data=f"adm_confirm_hub_{pid}_{uid}"),
                     InlineKeyboardButton("REJECT ❌", callback_data=f"adm_reject_{uid}"),
                     InlineKeyboardButton("BLOCK 🚫", callback_data=f"adm_block_{uid}")])
        else:
            s = DB_STATE["sellers"].get(key, {})
            adm = s.get("tg_id") or OWNER_ID; label = key
            mk = kb([InlineKeyboardButton("CONFIRM ✅", callback_data=f"adm_confirm_{key}_{pid}_{uid}"),
                     InlineKeyboardButton("REJECT ❌", callback_data=f"adm_reject_{uid}"),
                     InlineKeyboardButton("BLOCK 🚫", callback_data=f"adm_block_{uid}")])
        tag = "@" + m.from_user.username if m.from_user.username else "NoUser"
        bot.send_message(uid, "⏳ Payment check hocche... Donnodogho 5-10 min apekkha korun.")
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
            d2.setdefault("hub", new_store()); d2.setdefault("sellers", {}); d2.setdefault("takeovers", [])
            DB_STATE.clear(); DB_STATE.update(d2); save_db()
            ctx.pop("wait", None)
            bot.send_message(uid, "✅ Restore Complete! Type /start")
        except Exception as e:
            bot.send_message(uid, f"❌ Restore error: {e}")
        return

    if wait == "startvids" and m.content_type == "video":
        st["start_videos"].append(m.video.file_id); save_db()
        edit_panel(uid, f"✅ Video added! Total: {len(st['start_videos'])}. Add more or `/done`", kb([BACK(uid)]))
        return
    if wait == "welcome":
        st["welcome_msg"] = txt; save_db(); ctx.pop("wait", None); render(uid); return
    if wait == "howvid" and m.content_type == "video":
        st["how_to_use_video"] = m.video.file_id; save_db(); ctx.pop("wait", None); render(uid); return
    if wait == "payphoto" and m.content_type == "photo":
        st["payment_photo"] = m.photo[-1].file_id; save_db(); ctx.pop("wait", None); render(uid); return
    if wait == "paytext":
        st["payment_msg"] = txt; save_db(); ctx.pop("wait", None); render(uid); return

    if wait == "cb":
        do_bc(uid, st.get("users", []), m); ctx.pop("wait", None); render(uid); return
    if wait == "bb":
        do_bc(uid, [b.get("user_id") for b in st.get("buyers", [])], m); ctx.pop("wait", None); render(uid); return
    if wait == "ab_msg":
        store_ab(st, m); save_db(); ctx["wait"] = "ab_time"
        edit_panel(uid, "Interval seconds type korun (e.g. 3600 = 1 hour):", kb([BACK(uid)])); return
    if wait == "ab_time" and m.content_type == "text":
        try:
            st["auto_bc"]["interval_seconds"] = int(txt); st["auto_bc"]["status"] = True; save_db()
            bot.send_message(uid, f"✅ Auto-BC ON (Interval: {int(txt)}s)")
        except Exception: bot.send_message(uid, "❌ Shohi number pathan.")
        ctx.pop("wait", None); render(uid); return

    if is_owner:
        if wait == "addseller":
            try:
                sid, tg, nm = txt.split(maxsplit=2); tg = int(tg)
                s = new_store(); s["tg_id"] = tg; s["broadcast_perm"] = False
                DB_STATE["sellers"][sid] = s; save_db()
                bot.send_message(uid, f"✅ Seller {sid} added successfully.")
            except Exception: bot.send_message(uid, "❌ Format: s1 123456789 Ramesh")
            ctx.pop("wait", None); render(uid); return
        if wait == "toggleperm" and txt.startswith("perm_"):
            sid = txt.split("_", 1)[1]
            if sid in DB_STATE["sellers"]:
                DB_STATE["sellers"][sid]["broadcast_perm"] = not DB_STATE["sellers"][sid].get("broadcast_perm", False)
                save_db(); bot.send_message(uid, f"✅ Perm {'ON' if DB_STATE['sellers'][sid]['broadcast_perm'] else 'OFF'}")
            ctx.pop("wait", None); render(uid); return
        if wait == "tkadd":
            try:
                sid, frm, to = txt.split()
                def hm(x): hh, mm = x.split(":"); return int(hh)*60 + int(mm)
                DB_STATE["takeovers"].append({"seller": sid, "from_min": hm(frm), "to_min": hm(to), "active": True})
                save_db(); bot.send_message(uid, f"✅ Takeover set for {sid}")
            except Exception: bot.send_message(uid, "❌ Format: s1 02:00 06:19")
            ctx.pop("wait", None); render(uid); return
        if wait == "tkmgmt":
            try:
                if txt.startswith("tog_"):
                    DB_STATE["takeovers"][int(txt.split("_")[1])]["active"] ^= True; save_db()
                elif txt.startswith("deltk_"):
                    DB_STATE["takeovers"].pop(int(txt.split("_")[1])); save_db()
                bot.send_message(uid, "✅ Done.")
            except Exception: bot.send_message(uid, "❌ Format: tog_0 / deltk_0")
            ctx.pop("wait", None); render(uid); return
        if wait == "ownbc" and txt.startswith("bct_"):
            sid = txt.split("_", 1)[1]
            ctx["_bc_key"] = sid; ctx["wait"] = "cb"
            edit_panel(uid, f"{sid} er users der jonno message pathan:", kb([BACK(uid)])); return

    # ========= PRODUCT COMMANDS HANDLER =========
    if wait == "prodcmd":
        st2 = store_of_key(key)
        
        # ADD PRODUCT
        if txt.startswith("ADD_") or txt.startswith("ADD "):
            nm = txt[4:].strip()
            if nm:
                pid = str(len(st2["products"]) + 1)
                st2["products"].append({
                    "id": pid, "name": nm, "desc": "", "videos": [],
                    "link": "https://example.com", "position": len(st2["products"]) + 1, "pay_msg": ""
                })
                save_db()
                bot.send_message(uid, f"✅ Product **{nm}** Added! (ID: `{pid}`)\n\nSet Link: `LINK_{pid}_https://...`", parse_mode="Markdown")
            else:
                bot.send_message(uid, "❌ Product name likhun. Example: `ADD_Netflix`")
            return

        # LINK PRODUCT
        elif txt.startswith("LINK_") or txt.startswith("LINK "):
            parts = txt.replace("LINK ", "LINK_").split("_", 2)
            if len(parts) == 3:
                pid, url = parts[1].strip(), parts[2].strip()
                p = next((x for x in st2["products"] if x["id"] == pid), None)
                if p:
                    p["link"] = url
                    save_db()
                    bot.send_message(uid, f"✅ Product `{pid}` er Link set hoyeche.")
                else:
                    bot.send_message(uid, f"❌ Product ID `{pid}` pawa jayni.")
            else:
                bot.send_message(uid, "❌ Format: `LINK_id_url`")
            return

        # DESC PRODUCT
        elif txt.startswith("DESC_") or txt.startswith("DESC "):
            parts = txt.replace("DESC ", "DESC_").split("_", 2)
            if len(parts) == 3:
                pid, desc = parts[1].strip(), parts[2].strip()
                p = next((x for x in st2["products"] if x["id"] == pid), None)
                if p:
                    p["desc"] = desc
                    save_db()
                    bot.send_message(uid, f"✅ Product `{pid}` er Description set hoyeche.")
                else:
                    bot.send_message(uid, f"❌ Product ID `{pid}` pawa jayni.")
            else:
                bot.send_message(uid, "❌ Format: `DESC_id_text`")
            return

        # VID PRODUCT
        elif txt.startswith("VID_") or txt.startswith("VID "):
            parts = txt.replace("VID ", "VID_").split("_", 1)
            if len(parts) == 2:
                pid = parts[1].strip()
                p = next((x for x in st2["products"] if x["id"] == pid), None)
                if p:
                    ctx["wait"] = "prodvid"
                    ctx["pid"] = pid
                    bot.send_message(uid, f"📹 Product `{pid}` ({p['name']}) er jonno video file pathan.")
                else:
                    bot.send_message(uid, f"❌ Product ID `{pid}` pawa jayni.")
            else:
                bot.send_message(uid, "❌ Format: `VID_id`")
            return

        # DEL PRODUCT
        elif txt.startswith("DEL_") or txt.startswith("DEL "):
            parts = txt.replace("DEL ", "DEL_").split("_", 1)
            if len(parts) == 2:
                pid = parts[1].strip()
                st2["products"] = [x for x in st2["products"] if x["id"] != pid]
                save_db()
                bot.send_message(uid, f"🗑️ Product `{pid}` Delete kora hoyeche.")
            else:
                bot.send_message(uid, "❌ Format: `DEL_id`")
            return

    # PRODUCT VIDEO INPUT
    if wait == "prodvid":
        pid = ctx.get("pid")
        if m.content_type == "video":
            p = next((x for x in store_of_key(key)["products"] if x["id"] == pid), None)
            if p:
                p.setdefault("videos", []).append(m.video.file_id)
                save_db()
                bot.send_message(uid, f"✅ Product `{pid}` e Video add hoyeche!")
            ctx.pop("wait", None)
        else:
            bot.send_message(uid, "❌ Shohi video file pathan othoba `/cancel` type korun.")
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
