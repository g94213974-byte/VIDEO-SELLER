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
# panel: uid -> {"msg_id": int}
# ctx:   uid -> {"key": store_key, "nav": [routes], "mode": "owner"/"seller"}
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
            except Exception:
                pass  # caption etc -> resend
        msg = bot.send_message(uid, text, reply_markup=markup, parse_mode="Markdown", disable_web_page_preview=True)
        panel[uid] = {"msg_id": msg.message_id}
    except Exception:
        # fallback: koi purana media bana ho to message bhejo
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

# ---- bar "back" button text by route ----
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
    if ctx.get("mode") == "seller": key = ctx.get("key")
    else: key = ctx.get("key", "hub")

    if route == "owner":
        edit_panel(uid, "👑 **OWNER PANEL**", kb(
            [InlineKeyboardButton("🏠 Manage My Store", callback_data="GO_cfg")],
            [InlineKeyboardButton("👥 Manage Sellers", callback_data="GO_sellers")],
            [InlineKeyboardButton("⏱️ Takeover Schedule", callback_data="GO_tk")],
            [InlineKeyboardButton("💰 All Sales", callback_data="GO_sales")],
            [InlineKeyboardButton("📣 Send BC to Seller Users", callback_data="GO_ownbc")],
            [InlineKeyboardButton("💾 Backup & Restore", callback_data="GO_bk")]))
        return

    if route == "seller":
        s = store_of_key(key)
        rows = [[InlineKeyboardButton("🛍️ Manage My Store", callback_data="GO_cfg")]]
        if s.get("broadcast_perm"):
            rows.append([InlineKeyboardButton("📣 Broadcast", callback_data="GO_bc")])
        else:
            rows.append([InlineKeyboardButton("🔒 Broadcast locked (owner on karega)", callback_data="none")])
        rows.append([InlineKeyboardButton("📦 My Buyers", callback_data="GO_bl")])
        edit_panel(uid, f"🏪 **Seller Panel**\n\nStore: `{key}`", kb(*rows))
        return

    if route == "cfg":
        store = store_of_key(key)
        lp = "↔️ Horizontal" if store["layout_style"] == "horizontal" else "↕️ Vertical"
        rows = [
            [InlineKeyboardButton("🎞️ Start Videos", callback_data="SUB_startvids")],
            [InlineKeyboardButton("🛍️ Products", callback_data="SUB_products")],
            [InlineKeyboardButton("📝 Welcome Text", callback_data="SUB_welcome")],
            [InlineKeyboardButton("📐 Layout: " + lp, callback_data="ACT_togglelayout")],
            [InlineKeyboardButton("🎥 How-To-Use Video", callback_data="SUB_howvid")],
            [InlineKeyboardButton("💳 Payment Config", callback_data="SUB_pay")],
            [InlineKeyboardButton("🚀 Custom BC", callback_data="SUB_cb")],
            [InlineKeyboardButton("⏱️ Auto BC", callback_data="SUB_ab")],
            [InlineKeyboardButton("👑 BC to Buyers", callback_data="SUB_bb")],
            [InlineKeyboardButton("📦 Buyers", callback_data="GO_bl")],
            [InlineKeyboardButton("💾 Backup this Store", callback_data="ACT_bkstore")],
        ]
        edit_panel(uid, f"🛠️ **Store Settings** — `{key}`", kb(*rows))
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
            global tot
            c = len(st.get("buyers", [])); tot += c
            return f"• {nm}: {c}\n"
        s += ln("HUB", DB_STATE["hub"])
        for k, st in DB_STATE["sellers"].items(): s += ln(k, st)
        s += f"\n**Total: {tot}**"
        edit_panel(uid, s, kb([BACK(uid)]))
        return

    if route == "ownbc":
        s = "📣 BC ko kaunse seller ke users?\n"
        for k, st in DB_STATE["sellers"].items():
            s += f"`bct_{k}` → {k} ({len(st.get('users', []))} users)\n"
        s += "\nBhejo: `bct_s1` phir message."
        edit_panel(uid, s, kb([BACK(uid)]))
        return

    if route == "bk":
        edit_panel(uid, "💾 **Backup & Restore**", kb(
            [InlineKeyboardButton("⬇️ Download Backup Code", callback_data="ACT_getbk")],
            [InlineKeyboardButton("📥 Restore (code/file bhejo)", callback_data="W_restore")],
            [BACK(uid)]))
        return

    if route == "bl":
        st = store_of_key(key)
        if not st.get("buyers"):
            edit_panel(uid, "📦 Koi buyer nahi hai.", kb([BACK(uid)])); return
        s = f"📦 **Buyers** ({key})\n"
        for b in st["buyers"][-15:]:
            s += f"• {b.get('product')} | `{b.get('user_id')}` | {b.get('date')}\n"
        edit_panel(uid, s, kb([BACK(uid)]))
        return

    # store sub-setup menus (text input needed)
    if route.startswith("SUB_"):
        sub = route[4:]
        if sub == "startvids":
            user_states[uid]["wait"] = "startvids"
            edit_panel(uid, f"📥 Video bhejo (ek ek karke). Done ke liye `/done`.\nTotal: {len(store_of_key(key)['start_videos'])}", kb([BACK(uid)]))
        elif sub == "welcome":
            user_states[uid]["wait"] = "welcome"
            edit_panel(uid, "📝 Naya Welcome text bhejo. `{name}` use karo.", kb([BACK(uid)]))
        elif sub == "howvid":
            user_states[uid]["wait"] = "howvid"
            edit_panel(uid, "🎥 'How To Use' video bhejo.", kb([BACK(uid)]))
        elif sub == "pay":
            st = store_of_key(key)
            edit_panel(uid, "💳 **Payment Config**", kb(
                [InlineKeyboardButton("🖼️ QR Photo Set", callback_data="W_payphoto")],
                [InlineKeyboardButton("✏️ Payment Text Set", callback_data="W_paytext")],
                [BACK(uid)]))
        elif sub == "cb":
            user_states[uid]["wait"] = "cb"
            edit_panel(uid, "🚀 Broadcast message bhejo (text/photo/video).", kb([BACK(uid)]))
        elif sub == "ab":
            user_states[uid]["wait"] = "ab_msg"
            edit_panel(uid, "⏱️ Auto-BC ke liye message bhejo (text/photo/video). Phir interval seconds bataunga.", kb([BACK(uid)]))
        elif sub == "bb":
            user_states[uid]["wait"] = "bb"
            edit_panel(uid, "👑 Sirf buyers ko broadcast message bhejo.", kb([BACK(uid)]))
        elif sub == "products":
            st = store_of_key(key)
            s = f"🛍️ **Products** ({key})\n"
            for p in st["products"]:
                s += f"`{p['id']}` {p['name']} | del:{p['id']}\n"
            s += "\nNaya add: `ADD_<name>`\nLink set: `LINK_<id>_<url>`\nDesc: `DESC_<id>_<text>`\nVideo add: `VID_<id>` phir video\nDelete: `DEL_<id>`"
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

    # ---------- user-side (store purchase) ----------
    if d.startswith("home_"):
        st = store_of_key(key)
        send_menu(uid, st, (c.message.from_user.first_name or "User")); return
    if d == "none": return

    # ---------- panel navigation ----------
    if d == "GO_cfg":
        push(uid, "cfg"); render(uid); return
    if d == "GO_sellers": push(uid, "sellers"); render(uid); return
    if d == "GO_tk": push(uid, "tk"); render(uid); return
    if d == "GO_sales": push(uid, "sales"); render(uid); return
    if d == "GO_ownbc": push(uid, "ownbc"); render(uid); return
    if d == "GO_bk": push(uid, "bk"); render(uid); return
    if d == "GO_bl": push(uid, "bl"); render(uid); return
    if d == "GO_bc": push(uid, "cfg"); render(uid); return

    if d.startswith("SUB_"):
        push(uid, d); render(uid); return

    if d == "NAV_BACK":
        if not ctx["nav"]:
            # root par hai -> seller ya owner main dikhao
            if ctx.get("mode") == "seller": push(uid, "seller")
            else: push(uid, "owner")
            render(uid)
        else:
            back(uid)
            render(uid)
        return

    if d.startswith("W_"):
        ctx["wait"] = d[2:].lower()
        if ctx["wait"] == "addseller":
            edit_panel(uid, "Format bhejo:\n`s1 123456789 Ramesh`", kb([BACK(uid)]))
        elif ctx["wait"] == "toggleperm":
            s = "BC permission toggle ke liye bhejo `perm_s1`:\n"
            for k, st in DB_STATE["sellers"].items():
                s += f"`perm_{k}` → {k} (ab {'ON' if st.get('broadcast_perm') else 'OFF'})\n"
            edit_panel(uid, s or "Koi seller nahi.", kb([BACK(uid)]))
        elif ctx["wait"] == "tkadd":
            edit_panel(uid, "Format:\n`s1 02:00 06:19` (server time)", kb([BACK(uid)]))
        elif ctx["wait"] == "tkmgmt":
            s = "Toggle/Delete:\n`tog_0` (0-index), `deltk_0`\n"
            edit_panel(uid, s, kb([BACK(uid)]))
        elif ctx["wait"] == "payphoto":
            edit_panel(uid, "QR photo bhejo.", kb([BACK(uid)]))
        elif ctx["wait"] == "paytext":
            edit_panel(uid, "Naya payment text bhejo.", kb([BACK(uid)]))
        elif ctx["wait"] == "restore":
            edit_panel(uid, "Backup JSON **text** chhodo ya `.json` file bhejo. Tab main restore kar dunga.", kb([BACK(uid)]))
        return

    if d.startswith("ACT_"):
        act = d[4:]
        if act == "togglelayout":
            st = store_of_key(key)
            st["layout_style"] = "vertical" if st["layout_style"] != "vertical" else "horizontal"
            save_db(); render(uid)
        elif act == "bkstore":
            bot.send_document(uid, (f"backup_{key}.json", json.dumps(DB_STATE, indent=2).encode()))
            edit_panel(uid, "✅ Backup file bheja gaya upar. Restore: code/file bhejo.", kb([BACK(uid)]))
            user_states[uid]["wait"] = "restore"
        elif act == "getbk":
            bot.send_document(uid, ("bot_backup_full.json", json.dumps(DB_STATE, indent=2).encode()))
            edit_panel(uid, "✅ Poora backup file bheja gaya. Ye code/file jise bhi paas karo wo restore kar sakta hai.\n\n📥 Restore karne ke liye niche button dabao.", kb(
                [InlineKeyboardButton("📥 Restore Now", callback_data="W_restore")],
                [BACK(uid)]))
        return

    # owner-only special go-to
    # (handled at render routes already)

# ---------------- MAIN USER MENU ----------------
def send_menu(uid, store, name):
    if store["start_videos"]: send_videos(uid, store["start_videos"])
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

# /start
@bot.message_handler(commands=['start'])
def start_cmd(m):
    uid = m.chat.id; name = m.from_user.first_name or "User"
    txt = m.text or ""
    payload = txt.split(" ", 1)[1] if " " in txt else ""
    payload = payload.split("?")[0].replace("/start", "").strip()
    ctx = user_states.setdefault(uid, {})

    # owner
    if uid == OWNER_ID:
        ctx.clear(); ctx["mode"] = "owner"; ctx["key"] = "hub"; ctx["nav"] = ["owner"]
        render(uid); return
    # seller (jo store owner ne register kiya)
    sk = key_of_seller_tg(uid)
    if sk:
        ctx.clear(); ctx["mode"] = "seller"; ctx["key"] = sk; ctx["nav"] = ["seller"]
        render(uid); return

    # normal user: seller deep-link ?
    if payload.startswith("s") and payload[1:].isdigit():
        key = payload
        st = DB_STATE["sellers"].get(key)
        if not st:
            bot.send_message(uid, "❌ Yeh store link valid nahi hai."); return
        ctx["mode"] = "user"; ctx["key"] = key; ctx["nav"] = []
        if takeover_of(key):
            ctx["_src"] = key
            ctx["key"] = "hub"
            h = DB_STATE["hub"]
            if uid not in h["users"]: h["users"].append(uid); save_db()
            bot.send_message(uid, "🛒 Is waqt ye store **owner hub** par khul raha hai.")
            send_menu(uid, h, name)
        else:
            if uid not in st["users"]: st["users"].append(uid); save_db()
            send_menu(uid, st, name)
        return

    # normal user -> hub
    ctx["mode"] = "user"; ctx["key"] = "hub"; ctx["nav"] = []
    h = DB_STATE["hub"]
    if uid not in h["users"]: h["users"].append(uid); save_db()
    send_menu(uid, h, name)

# buyer flow callbacks
@bot.callback_query_handler(func=lambda c: c.data.startswith(("BUY_", "HOW", "REP")), )
def user_cb(c):
    try: bot.answer_callback_query(c.id)
    except Exception: pass
    uid = c.message.chat.id; d = c.data
    ctx = user_states.setdefault(uid, {})
    key = ctx.get("key", "hub"); st = store_of_key(key)
    if d == "HOW":
        v = st.get("how_to_use_video")
        if v: bot.send_video(uid, v)
        else: bot.send_message(uid, "ℹ️ Video set nahi hai.")
        return
    if d == "REP":
        ctx["wait"] = "report"; bot.send_message(uid, "📝 Apni problem likho:"); return
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
        bot.send_message(uid, "📸 Payment screenshot bhejo.")

# screenshot confirm buttons (owner / seller receive)
@bot.callback_query_handler(func=lambda c: c.data.startswith(("adm_confirm_", "adm_reject_", "adm_block_")))
def confirm_cb(c):
    try: bot.answer_callback_query(c.id)
    except Exception: pass
    uid = c.message.chat.id; d = c.data
    try:
        if d.startswith("adm_confirm_"):
            parts = d.split("_")  # adm_confirm_<key>_<pid>_<uid>
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
            # find which store this seller manages / owner hub
            k = key_of_seller_tg(uid) or "hub"
            st = store_of_key(k)
            if tu not in st["blocked_users"]: st["blocked_users"].append(tu); save_db()
    except Exception as e: print("confirm err", e)
    try: bot.delete_message(uid, c.message.message_id)
    except Exception: pass

# ---------------- TEXT / MEDIA INPUT ----------------
@bot.message_handler(func=lambda m: True, content_types=['text','photo','video','document'])
def inp(m):
    uid = m.chat.id
    txt = m.text or m.caption or ""
    ctx = user_states.setdefault(uid, {})
    ctx.setdefault("nav", [])
    key = ctx.get("key", "hub")
    st = store_of_key(key)
    wait = ctx.get("wait")

    # /cancel ya /done handles command reset
    if txt in ("/done", "/cancel"):
        ctx.pop("wait", None)
        bot.send_message(uid, "✅ Cancel/Complete."); return

    # ---- report input (normal user) ----
    if wait == "report":
        ctx.pop("wait", None)
        sk = ctx.get("_src")
        # report goes to owner always (or seller if not takeover)
        dest = OWNER_ID
        if key != "hub" and not ctx.get("_src"):
            s = DB_STATE["sellers"].get(key)
            dest = (s or {}).get("tg_id") or OWNER_ID
        tag = "@" + m.from_user.username if m.from_user.username else "NoUser"
        bot.send_message(dest, f"📩 Report from {tag} (`{uid}`):\n\n{txt}\n\n*Reply se jawab do.*", parse_mode="Markdown")
        bot.send_message(uid, "✅ Report admin ko chala gaya.")
        return

    # ---- payment screenshot ----
    if wait == "shot" and m.content_type == "photo":
        ctx.pop("wait", None)
        pid = ctx.get("pid")
        p = next((x for x in st["products"] if x["id"] == pid), None)
        nm = p["name"] if p else "Product"
        # routing: hub sale -> owner; unless takeover source -> attribute src
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
        bot.send_message(uid, "⏳ Payment check ho raha hai... 5-10 min ruko.")
        try:
            bot.send_photo(adm, m.photo[-1].file_id,
                caption=f"📸 **New Payment!**\nStore: {label}\nProduct: {nm}\nUser: {tag}\nID: `{uid}`",
                reply_markup=mk, parse_mode="Markdown")
        except Exception as e: print("shot err", e)
        return

    # ---- admin-only: text commands for owner/seller ----
    is_owner = (uid == OWNER_ID)
    seller_key = key_of_seller_tg(uid)
    if not is_owner and not seller_key:
        return  # normal user ka free text -> ignore
    if is_owner: mode = "owner"
    else: mode = "seller"; key = seller_key; ctx["key"] = key
    st = store_of_key(key)

    # ========= RESTORE (owner ya seller) =========
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
            bot.send_message(uid, "✅ Restore ho gaya! /start dabao.")
        except Exception as e:
            bot.send_message(uid, f"❌ Galat code/file. Error: {e}")
        return

    # ========= input-driven waits =========
    if wait == "startvids":
        if m.content_type == "video":
            st["start_videos"].append(m.video.file_id); save_db()
            edit_panel(uid, f"✅ Video add. Total {len(st['start_videos'])}. Aur bhejo ya `/done`", kb([BACK(uid)]))
        return
    if wait == "welcome":
        st["welcome_msg"] = txt; save_db(); ctx.pop("wait", None); render(uid); return
    if wait == "howvid":
        if m.content_type == "video":
            st["how_to_use_video"] = m.video.file_id; save_db(); ctx.pop("wait", None); render(uid)
        return
    if wait == "payphoto":
        if m.content_type == "photo":
            st["payment_photo"] = m.photo[-1].file_id; save_db(); ctx.pop("wait", None); render(uid)
        return
    if wait == "paytext":
        st["payment_msg"] = txt; save_db(); ctx.pop("wait", None); render(uid); return

    # broadcast
    if wait == "cb":
        do_bc(uid, st.get("users", []), m); ctx.pop("wait", None); render(uid); return
    if wait == "bb":
        do_bc(uid, [b.get("user_id") for b in st.get("buyers", [])], m); ctx.pop("wait", None); render(uid); return
    if wait == "ab_msg":
        store_ab(st, m); save_db(); ctx["wait"] = "ab_time"
        edit_panel(uid, "Interval seconds bhejo (3600 = 1 ghanta):", kb([BACK(uid)])); return
    if wait == "ab_time" and m.content_type == "text":
        try:
            st["auto_bc"]["interval_seconds"] = int(txt); st["auto_bc"]["status"] = True; save_db()
            bot.send_message(uid, f"✅ Auto-BC ON, har {int(txt)} sec.")
        except Exception: bot.send_message(uid, "❌ Sirf number bhejo.")
        ctx.pop("wait", None); render(uid); return

    # owner specific waits
    if is_owner:
        if wait == "addseller":
            try:
                sid, tg, nm = txt.split(); tg = int(tg)
                s = new_store(); s["tg_id"] = tg; s["broadcast_perm"] = False
                DB_STATE["sellers"][sid] = s; save_db()
                bot.send_message(uid, f"✅ Seller {sid} add. Wo /start karke store chalayega.")
            except Exception: bot.send_message(uid, "❌ Format: s1 123456789 Ramesh")
            ctx.pop("wait", None); render(uid); return
        if wait == "toggleperm" and txt.startswith("perm_"):
            sid = txt.split("_", 1)[1]
            if sid in DB_STATE["sellers"]:
                DB_STATE["sellers"][sid]["broadcast_perm"] = not DB_STATE["sellers"][sid].get("broadcast_perm", False)
                save_db(); bot.send_message(uid, f"✅ {sid} perm {'ON' if DB_STATE['sellers'][sid]['broadcast_perm'] else 'OFF'}")
            ctx.pop("wait", None); render(uid); return
        if wait == "tkadd":
            try:
                sid, frm, to = txt.split()
                def hm(x):
                    hh, mm = x.split(":"); return int(hh)*60 + int(mm)
                DB_STATE["takeovers"].append({"seller": sid, "from_min": hm(frm), "to_min": hm(to), "active": True})
                save_db(); bot.send_message(uid, f"✅ Takeover set {sid} {frm}-{to} (daily repeat).")
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
            edit_panel(uid, f"{sid} ke users ko broadcast message bhejo:", kb([BACK(uid)])); return

    # product commands (owner/seller both manage own store)
    if wait == "prodcmd":
        st2 = store_of_key(key)
        try:
            if txt.startswith("ADD_"):
                nm = txt[4:].strip()
                pid = str(len(st2["products"]) + 1)
                st2["products"].append({"id": pid, "name": nm, "desc": "", "videos": [], "link": "https://example.com", "position": len(st2["products"])+1, "pay_msg": ""})
                save_db(); bot.send_message(uid, f"✅ Product add. Link: `LINK_{pid}_https://...`")
            elif txt.startswith("LINK_"):
                _, pid, url = txt.split(" ", 2) if " " in txt else txt.split("_", 2)
                p = next((x for x in st2["products"] if x["id"] == pid), None)
                if p: p["link"] = url; save_db(); bot.send_message(uid, "✅ Link set.")
            elif txt.startswith("DESC_"):
                # DESC_<id>_<text space separated>
                pid = txt.split("_")[1]; desc = txt.split("_", 2)[2]
                p = next((x for x in st2["products"] if x["id"] == pid), None)
                if p: p["desc"] = desc; save_db(); bot.send_message(uid, "✅ Desc set.")
            elif txt.startswith("DEL_"):
                pid = txt.split("_")[1]
                st2["products"] = [x for x in st2["products"] if x["id"] != pid]; save_db()
                bot.send_message(uid, "✅ Deleted.")
            else:
                bot.send_message(uid, "Commands:\n`ADD_name`, `LINK_id_url`, `DESC_id_text`, `DEL_id`, `VID_id` phir video")
        except Exception: bot.send_message(uid, "Command galat.")
        return
    if wait == "prodvid":
        pid = ctx.get("pid")
        if m.content_type == "video":
            p = next((x for x in store_of_key(key)["products"] if x["id"] == pid), None)
            if p:
                p.setdefault("videos", []).append(m.video.file_id); save_db()
                bot.send_message(uid, "✅ Video add. Aur ya `/done`")
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
    bot.send_message(uid, f"✅ Bheja: {ok}   Fail: {fail}")

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
def home(): return "Bot running!"

if __name__ == "__main__":
    threading.Thread(target=lambda: bot.infinity_polling(), daemon=True).start()
    threading.Thread(target=worker, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
