import os, json, threading, time, datetime, re
from flask import Flask
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaVideo

TOKEN   = os.environ.get('BOT_TOKEN')
OWNER_ID = int(os.environ.get('OWNER_ID', '0'))
LOG_CHANNEL_ID = int(os.environ.get('LOG_CHANNEL_ID', '0'))

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

BOT_USERNAME = ""
try:
    BOT_USERNAME = bot.get_me().username
except Exception:
    pass

# ---------------- HELPERS ----------------
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
    if not expiry_time: return "Unlimited ♾️"
    rem = int(expiry_time - time.time())
    if rem <= 0: return "Expired ❌"
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
        "layout_style": "vertical", "products": [], "blocked_users": [],
        "users": [], "buyers": [],
        "auto_bc": {"status": False, "interval_seconds": 3600, "message_type": None, "file_id": None, "text": None},
        "expiry_time": None, "seller_name": "Seller"
    }

def fresh_state():
    return {"sellers": {}, "hub": new_store()}

DB_STATE = fresh_state()

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

def store_of_key(key):
    return DB_STATE["hub"] if key in ("hub", None) else DB_STATE["sellers"].get(key, new_store())

def key_of_seller_tg(tg):
    now = time.time()
    for k, s in DB_STATE["sellers"].items():
        if s.get("tg_id") == tg:
            exp = s.get("expiry_time")
            if exp and now > exp: return None
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

# ---------------- RENDER PANELS ----------------
def render(uid):
    ctx = user_states.get(uid, {})
    route = top(uid)
    key = ctx.get("key", "hub")
    st = store_of_key(key)

    if route in ("admin", "seller"):
        lp = "↔️ Horizontal" if st.get("layout_style") == "horizontal" else "↕️ Vertical"
        time_status = format_time_left(st.get("expiry_time")) if key != "hub" else "Owner Access 👑"
        sname = st.get("seller_name", "Seller")
        prod_link = f"https://t.me/{BOT_USERNAME}?start={key}" if key != "hub" else f"https://t.me/{BOT_USERNAME}"

        rows = [
            [InlineKeyboardButton("🔗 Get My Store Link", callback_data="ACT_getlink")],
            [InlineKeyboardButton("🛍️ Manage Product Buttons", callback_data="SUB_products")],
            [InlineKeyboardButton("📐 Change Layout: " + lp, callback_data="ACT_togglelayout")],
            [InlineKeyboardButton("🎞️ Manage Start Videos", callback_data="SUB_startvids")],
            [InlineKeyboardButton("📝 Edit Welcome Text", callback_data="SUB_welcome")],
            [InlineKeyboardButton("🎥 Set 'How To Use' Video", callback_data="SUB_howvid")],
            [InlineKeyboardButton("💳 Global Payment Config", callback_data="SUB_pay")],
            [InlineKeyboardButton("📦 View Buyers List", callback_data="GO_bl")],
            [InlineKeyboardButton("💾 Backup & Restore Settings", callback_data="GO_bk")]
        ]
        if uid == OWNER_ID:
            rows.insert(0, [InlineKeyboardButton("👑 Switch to Owner Panel", callback_data="GO_owner")])

        header = f"🛠️ **Admin Panel** ({sname})\n⌛ Time: `{time_status}`\n🔗 Store Link:\n`{prod_link}`"
        edit_panel(uid, header, kb(*rows))
        return

    if route == "owner":
        rows = [
            [InlineKeyboardButton("👥 Manage Admins (Owner)", callback_data="GO_sellers")],
            [InlineKeyboardButton("🚀 Send Custom Broadcast", callback_data="SUB_cb")],
            [InlineKeyboardButton("⏱️ Auto Timed Broadcast", callback_data="SUB_ab")],
            [InlineKeyboardButton("👑 Special Broadcast to Buyers", callback_data="SUB_bb")],
            [InlineKeyboardButton("💰 View All Sales", callback_data="GO_sales")],
            [InlineKeyboardButton("💾 Backup & Restore", callback_data="GO_bk")],
            [InlineKeyboardButton("🛠️ Switch to Admin Panel", callback_data="GO_admin")]
        ]
        edit_panel(uid, "👑 **Owner Panel**", kb(*rows))
        return

    if route == "sellers":
        s = "👥 **Sellers Management**\n\n"
        for k, st_item in DB_STATE["sellers"].items():
            name = st_item.get('seller_name', 'Seller')
            rem = format_time_left(st_item.get('expiry_time'))
            s += f"👤 **{name}** (`{k}`) | Time: `{rem}`\n"
        if not DB_STATE["sellers"]: s += "(No seller added yet)\n"

        edit_panel(uid, s, kb(
            [InlineKeyboardButton("➕ Add Seller", callback_data="W_addseller")],
            [InlineKeyboardButton("❌ Delete Seller", callback_data="GO_del_seller_menu")],
            [InlineKeyboardButton("⏳ Extend/Set Seller Time", callback_data="W_settime")],
            [BACK(uid)]))
        return

    if route == "del_seller_menu":
        if not DB_STATE["sellers"]:
            edit_panel(uid, "❌ No seller to delete.", kb([BACK(uid)]))
            return
        rows = [[InlineKeyboardButton(f"🗑️ Delete {st_item.get('seller_name', k)}", callback_data=f"ACT_delseller_{k}")] for k, st_item in DB_STATE["sellers"].items()]
        rows.append([BACK(uid)])
        edit_panel(uid, "🗑️ Select a seller to delete:", kb(*rows))
        return

    if route == "products":
        s = f"🛍️ **Product Button Management:**\n\n"
        s += "📌 **Add Product Method:**\n1. Send or Forward Video.\n2. Send Text: `Product Name https://link.com`\n\n"
        s += "📌 **Delete Product:** Select 'Delete Button' below or send `DEL_id`"

        rows = [
            [InlineKeyboardButton("❇️ Add New Button", callback_data="W_addbutton")],
            [InlineKeyboardButton("✏️ Edit Details / Link", callback_data="GO_edit_prod_list")],
            [InlineKeyboardButton("🔢 Change Position", callback_data="W_posbutton")],
            [InlineKeyboardButton("🎥 Add Videos", callback_data="W_addvidprod")],
            [InlineKeyboardButton("⚙️ Manage Videos", callback_data="GO_manvid_list")],
            [InlineKeyboardButton("🗑️ Delete Button", callback_data="GO_delbutton_list")],
            [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="NAV_BACK")]
        ]
        edit_panel(uid, s, kb(*rows))
        return

    if route == "edit_prod_list":
        rows = [[InlineKeyboardButton(f"✏️ {p['name']}", callback_data=f"ACT_editp_{p['id']}")] for p in st.get("products", [])]
        rows.append([BACK(uid)])
        edit_panel(uid, "Select button to edit:", kb(*rows))
        return

    if route.startswith("edit_prod_item_"):
        pid = route.replace("edit_prod_item_", "")
        p = next((x for x in st.get("products", []) if str(x["id"]) == str(pid)), None)
        if not p:
            edit_panel(uid, "❌ Product not found.", kb([BACK(uid)])); return
        txt = f"Editing ✏️ **{p['name']}**\nLink: `{p.get('link','')}`"
        rows = [
            [InlineKeyboardButton("✏️ Name", callback_data=f"W_editname_{pid}"), InlineKeyboardButton("🔗 Link", callback_data=f"W_editlink_{pid}")],
            [BACK(uid)]
        ]
        edit_panel(uid, txt, kb(*rows))
        return

    if route == "manvid_list":
        rows = [[InlineKeyboardButton(f"⚙️ ({len(p.get('videos',[]))}) {p['name']}", callback_data=f"ACT_manv_{p['id']}")] for p in st.get("products", [])]
        rows.append([BACK(uid)])
        edit_panel(uid, "Manage videos of:", kb(*rows))
        return

    if route.startswith("manvid_item_"):
        pid = route.replace("manvid_item_", "")
        p = next((x for x in st.get("products", []) if str(x["id"]) == str(pid)), None)
        if not p: edit_panel(uid, "❌ Product not found.", kb([BACK(uid)])); return
        vids = p.get("videos", [])
        rows = []
        for idx, _ in enumerate(vids):
            rows.append([
                InlineKeyboardButton(f"👀 {idx+1}", callback_data=f"ACT_viewv_{pid}_{idx}"),
                InlineKeyboardButton(f"🗑️ {idx+1}", callback_data=f"ACT_delv_{pid}_{idx}")
            ])
        rows.append([InlineKeyboardButton("💥 Delete All", callback_data=f"ACT_delallv_{pid}")])
        rows.append([BACK(uid)])
        edit_panel(uid, f"Videos of **{p['name']}** ({len(vids)} total):", kb(*rows))
        return

    if route == "delbutton_list":
        rows = [[InlineKeyboardButton(f"🗑️ {p['name']}", callback_data=f"ACT_delp_{p['id']}")] for p in st.get("products", [])]
        rows.append([BACK(uid)])
        edit_panel(uid, "Delete which button?", kb(*rows))
        return

    if route.startswith("SUB_"):
        sub = route[4:]
        if sub == "startvids":
            user_states[uid]["wait"] = "startvids"
            edit_panel(uid, f"📥 Send videos. Type `/done` when finished.\nTotal: {len(st.get('start_videos', []))}", kb([BACK(uid)]))
        elif sub == "welcome":
            user_states[uid]["wait"] = "welcome"
            edit_panel(uid, "📝 Send new Welcome text. You can use `{name}` placeholder.", kb([BACK(uid)]))
        elif sub == "howvid":
            user_states[uid]["wait"] = "howvid"
            edit_panel(uid, "🎥 Send 'How To Use' video.", kb([BACK(uid)]))
        elif sub == "pay":
            edit_panel(uid, "💳 **Global Payment Config**", kb(
                [InlineKeyboardButton("🖼️ Set QR Photo", callback_data="W_payphoto")],
                [InlineKeyboardButton("✏️ Set Payment Text", callback_data="W_paytext")],
                [BACK(uid)]))
        elif sub == "cb":
            user_states[uid]["wait"] = "cb"
            edit_panel(uid, "🚀 Send custom broadcast message.", kb([BACK(uid)]))
        elif sub == "ab":
            user_states[uid]["wait"] = "ab_msg"
            edit_panel(uid, "⏱️ Send Auto Timed Broadcast message.", kb([BACK(uid)]))
        elif sub == "bb":
            user_states[uid]["wait"] = "bb"
            edit_panel(uid, "👑 Send special broadcast to buyers.", kb([BACK(uid)]))
        return

    if route == "bk":
        edit_panel(uid, "💾 **Backup & Restore**", kb(
            [InlineKeyboardButton("⬇️ Download Backup File", callback_data="ACT_getbk")],
            [InlineKeyboardButton("📥 Restore Settings", callback_data="W_restore")],
            [BACK(uid)]))
        return

    if route == "bl":
        if not st.get("buyers"):
            edit_panel(uid, "📦 No buyers found.", kb([BACK(uid)])); return
        s = f"📦 **Buyers List** ({st.get('seller_name', key)})\n\n"
        for b in st["buyers"][-15:]:
            s += f"• {b.get('product')} | `{b.get('user_id')}` | {b.get('date')}\n"
        edit_panel(uid, s, kb([BACK(uid)]))
        return

    edit_panel(uid, "Menu", kb([BACK(uid)]))

# ---------------- CALLBACK HANDLER ----------------
@bot.callback_query_handler(func=lambda c: True)
def cb(c):
    try: bot.answer_callback_query(c.id)
    except Exception: pass
    uid = c.message.chat.id
    d = c.data
    ctx = user_states.setdefault(uid, {})
    ctx.setdefault("nav", [])
    key = ctx.get("key", "hub")
    st = store_of_key(key)

    if d == "HOME":
        send_menu(uid, st, (c.from_user.first_name or "User")); return

    if d == "HOW":
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
        p = next((x for x in st.get("products", []) if str(x["id"]) == str(pid)), None)
        if not p:
            bot.send_message(uid, "❌ Product not found.")
            return
        if p.get("videos"): send_videos(uid, p["videos"])
        cap = f"📌 **{p['name']}**"
        pay = p.get("pay_msg") or st.get("payment_msg", "")
        mk = kb([InlineKeyboardButton("I have paid ✅", callback_data=f"PAID_{pid}")],
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

    # PANEL NAVIGATION
    if d == "GO_owner": push(uid, "owner"); render(uid); return
    if d == "GO_admin": push(uid, "admin"); render(uid); return
    if d == "GO_sellers": push(uid, "sellers"); render(uid); return
    if d == "GO_del_seller_menu": push(uid, "del_seller_menu"); render(uid); return
    if d == "GO_bk": push(uid, "bk"); render(uid); return
    if d == "GO_bl": push(uid, "bl"); render(uid); return
    if d == "GO_edit_prod_list": push(uid, "edit_prod_list"); render(uid); return
    if d == "GO_manvid_list": push(uid, "manvid_list"); render(uid); return
    if d == "GO_delbutton_list": push(uid, "delbutton_list"); render(uid); return

    if d.startswith("SUB_"):
        push(uid, d); render(uid); return

    if d == "NAV_BACK":
        if not ctx["nav"]:
            push(uid, "admin")
        else:
            back(uid)
        render(uid)
        return

    if d.startswith("ACT_"):
        act = d[4:]
        if act == "getlink":
            lnk = f"https://t.me/{BOT_USERNAME}?start={key}" if key != "hub" else f"https://t.me/{BOT_USERNAME}"
            bot.send_message(uid, f"🔗 **Your Store Link:**\n`{lnk}`", parse_mode="Markdown")
        elif act == "togglelayout":
            st["layout_style"] = "vertical" if st.get("layout_style") != "vertical" else "horizontal"
            save_db(); render(uid)
        elif act.startswith("editp_"):
            pid = act.replace("editp_", "")
            push(uid, f"edit_prod_item_{pid}"); render(uid)
        elif act.startswith("manv_"):
            pid = act.replace("manv_", "")
            push(uid, f"manvid_item_{pid}"); render(uid)
        elif act.startswith("viewv_"):
            _, pid, idx = act.split("_")
            p = next((x for x in st.get("products", []) if str(x["id"]) == str(pid)), None)
            if p and len(p.get("videos", [])) > int(idx):
                bot.send_video(uid, p["videos"][int(idx)])
        elif act.startswith("delv_"):
            _, pid, idx = act.split("_")
            p = next((x for x in st.get("products", []) if str(x["id"]) == str(pid)), None)
            if p and len(p.get("videos", [])) > int(idx):
                p["videos"].pop(int(idx)); save_db(); render(uid)
        elif act.startswith("delallv_"):
            pid = act.replace("delallv_", "")
            p = next((x for x in st.get("products", []) if str(x["id"]) == str(pid)), None)
            if p:
                p["videos"] = []; save_db(); render(uid)
        elif act.startswith("delp_"):
            pid = act.replace("delp_", "")
            st["products"] = [x for x in st.get("products", []) if str(x["id"]) != str(pid)]
            save_db(); back(uid); render(uid)
        elif act.startswith("delseller_"):
            sid = act.replace("delseller_", "")
            if sid in DB_STATE["sellers"]:
                del DB_STATE["sellers"][sid]; save_db()
            back(uid); render(uid)
        elif act == "getbk":
            bot.send_document(uid, ("bot_backup.json", json.dumps(DB_STATE, indent=2).encode()))
        return

    if d.startswith("W_"):
        w = d[2:]
        if w == "addbutton":
            ctx["wait"] = "addbutton"
            edit_panel(uid, "❇️ Enter new Button Name & Link:\nFormat: `Product Name https://link.com`", kb([BACK(uid)]))
        elif w == "posbutton":
            ctx["wait"] = "posbutton"
            edit_panel(uid, "🔢 Type position:\nFormat: `ID Position` (e.g., `1 2`)", kb([BACK(uid)]))
        elif w == "addvidprod":
            ctx["wait"] = "addvidprod"
            edit_panel(uid, "🎥 Send or Forward Video now.", kb([BACK(uid)]))
        elif w.startswith("editname_"):
            pid = w.replace("editname_", "")
            ctx["wait"] = f"editname_{pid}"
            edit_panel(uid, "✏️ Enter new name:", kb([BACK(uid)]))
        elif w.startswith("editlink_"):
            pid = w.replace("editlink_", "")
            ctx["wait"] = f"editlink_{pid}"
            edit_panel(uid, "🔗 Enter new link:", kb([BACK(uid)]))
        elif w == "addseller":
            ctx["wait"] = "addseller"
            edit_panel(uid, "Format: `123456789 1d`", kb([BACK(uid)]))
        elif w == "settime":
            ctx["wait"] = "settime"
            edit_panel(uid, "Format: `s1 2d` or `s1 +1d`", kb([BACK(uid)]))
        elif w in ("payphoto", "paytext", "restore"):
            ctx["wait"] = w
            edit_panel(uid, f"Send input for {w}:", kb([BACK(uid)]))

# ---------------- USER MENU RENDER ----------------
def send_menu(uid, store, name):
    if store.get("start_videos"): send_videos(uid, store["start_videos"])
    welcome = store.get("welcome_msg", "{name}").format(name=name)
    mk = InlineKeyboardMarkup()
    prods = sorted(store.get("products", []), key=lambda x: x.get("position", 999))
    if store.get("layout_style") == "horizontal":
        row = []
        for p in prods:
            row.append(InlineKeyboardButton(p["name"], callback_data=f"BUY_{p['id']}"))
            if len(row) == 2: mk.row(*row); row = []
        if row: mk.row(*row)
    else:
        for p in prods: mk.row(InlineKeyboardButton(p["name"], callback_data=f"BUY_{p['id']}"))

    mk.row(InlineKeyboardButton("How to use ❓", callback_data="HOW"),
           InlineKeyboardButton("Report 📩", callback_data="REP"))
    try: bot.send_message(uid, welcome, reply_markup=mk, parse_mode="Markdown")
    except Exception: pass

@bot.message_handler(commands=['start'])
def start_cmd(m):
    uid = m.chat.id; name = m.from_user.first_name or "User"
    txt = m.text or ""
    payload = txt.split(" ", 1)[1] if " " in txt else ""
    payload = payload.split("?")[0].replace("/start", "").strip()
    ctx = user_states.setdefault(uid, {})

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

# ---------------- INPUT MESSAGES & AUTO-DELETE ----------------
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
        delete_msg_safe(uid, m.message_id)
        render(uid); return

    if wait == "report":
        ctx.pop("wait", None)
        dest = OWNER_ID
        if key != "hub":
            s = DB_STATE["sellers"].get(key)
            dest = (s or {}).get("tg_id") or OWNER_ID
        bot.send_message(dest, f"📩 Report from `{uid}`:\n\n{txt}", parse_mode="Markdown")
        bot.send_message(uid, "✅ Report sent successfully.")
        return

    if wait == "shot" and m.content_type == "photo":
        ctx.pop("wait", None)
        pid = ctx.get("pid")
        p = next((x for x in st.get("products", []) if str(x["id"]) == str(pid)), None)
        nm = p["name"] if p else "Product"
        adm = OWNER_ID if key == "hub" else (DB_STATE["sellers"].get(key, {}).get("tg_id") or OWNER_ID)
        mk = kb([InlineKeyboardButton("CONFIRM ✅", callback_data=f"adm_confirm_{key}_{pid}_{uid}"),
                 InlineKeyboardButton("REJECT ❌", callback_data=f"adm_reject_{uid}")])
        bot.send_message(uid, "⏳ Checking payment...")
        try: bot.send_photo(adm, m.photo[-1].file_id, caption=f"📸 Payment for {nm}\nUser: `{uid}`", reply_markup=mk)
        except Exception: pass
        return

    is_owner = (uid == OWNER_ID)
    seller_key = key_of_seller_tg(uid)
    if not is_owner and not seller_key: return

    # PRODUCT ADD & EDIT FLOW WITH AUTO DELETE
    if wait == "addbutton" and txt:
        parts = txt.split()
        link = next((x for x in parts if x.startswith("http://") or x.startswith("https://")), "https://example.com")
        pname = " ".join([x for x in parts if x != link]) if link in parts else txt
        pid = str(len(st.get("products", [])) + 1)
        st["products"].append({"id": pid, "name": pname, "link": link, "videos": [], "position": len(st["products"]) + 1})
        save_db(); ctx.pop("wait", None)
        delete_msg_safe(uid, m.message_id)
        render(uid); return

    if wait and wait.startswith("editname_"):
        pid = wait.replace("editname_", "")
        p = next((x for x in st.get("products", []) if str(x["id"]) == str(pid)), None)
        if p: p["name"] = txt; save_db()
        ctx.pop("wait", None)
        delete_msg_safe(uid, m.message_id)
        render(uid); return

    if wait and wait.startswith("editlink_"):
        pid = wait.replace("editlink_", "")
        p = next((x for x in st.get("products", []) if str(x["id"]) == str(pid)), None)
        if p: p["link"] = txt; save_db()
        ctx.pop("wait", None)
        delete_msg_safe(uid, m.message_id)
        render(uid); return

    if wait == "posbutton" and txt:
        try:
            parts = txt.split()
            pid, pos = parts[0], int(parts[1])
            for p in st.get("products", []):
                if str(p["id"]) == str(pid): p["position"] = pos
            save_db()
        except Exception: pass
        ctx.pop("wait", None)
        delete_msg_safe(uid, m.message_id)
        render(uid); return

    if (wait == "addvidprod" or m.content_type == "video") and m.content_type == "video":
        vid_id = m.video.file_id
        if not st.get("products"):
            st["products"].append({"id": "1", "name": "New Product", "videos": [vid_id], "link": "https://example.com", "position": 1})
        else:
            st["products"][-1].setdefault("videos", []).append(vid_id)
        save_db()
        delete_msg_safe(uid, m.message_id)
        render(uid); return

    if wait == "startvids" and m.content_type == "video":
        st.setdefault("start_videos", []).append(m.video.file_id); save_db()
        delete_msg_safe(uid, m.message_id)
        render(uid); return

    if wait == "welcome" and txt:
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

    if wait == "paytext" and txt:
        st["payment_msg"] = txt; save_db(); ctx.pop("wait", None)
        delete_msg_safe(uid, m.message_id)
        render(uid); return

    if txt.startswith("DEL_"):
        pid = txt.replace("DEL_", "").strip()
        st["products"] = [x for x in st.get("products", []) if str(x["id"]) != str(pid)]
        save_db(); delete_msg_safe(uid, m.message_id)
        render(uid); return

@app.route('/')
def home(): return "Bot running!"

if __name__ == "__main__":
    threading.Thread(target=lambda: bot.infinity_polling(), daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
