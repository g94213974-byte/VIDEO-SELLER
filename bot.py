import os, json, threading, time, datetime, re
from flask import Flask
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaVideo

TOKEN   = os.environ.get('BOT_TOKEN')
OWNER_ID = int(os.environ.get('OWNER_ID', '0'))
LOG_CHANNEL_ID = int(os.environ.get('LOG_CHANNEL_ID', '0'))

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# ================= DEFAULT STORE FACTORY =================
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
        "broadcast_perm": True,   # kya seller apne users ko broadcast kar sakta hai
    }

def fresh_state():
    return {
        "sellers": {},      # "s<id>" -> store dict (+ meta: tg_id, name)
        "hub": new_store(), # owner ka default customer store
        "takeovers": [],    # [{seller, from_min, to_min, active}]
    }

DB_STATE = fresh_state()
user_states = {}

# ================= FILE-BASED DB IN CHANNEL (no 4096 limit) =================
def save_db():
    try:
        raw = json.dumps(DB_STATE).encode("utf-8")
        doc = bot.send_document(LOG_CHANNEL_ID, ("bot_backup.json", raw))
        try: bot.pin_chat_message(LOG_CHANNEL_ID, doc.message_id)
        except Exception: pass
    except Exception as e:
        print("DB save ERROR ->", e)

def load_db():
    global DB_STATE
    try:
        chat = bot.get_chat(LOG_CHANNEL_ID)
        pm = chat.pinned_message
        if pm and pm.document:
            fi = bot.get_file(pm.document.file_id)
            data = json.loads(bot.download_file(fi.file_path).decode("utf-8"))
            d = fresh_state(); d.update(data)
            for k in ("hub",):
                d.setdefault(k, new_store())
            d.setdefault("sellers", {}); d.setdefault("takeovers", [])
            DB_STATE = d
    except Exception as e:
        print("No DB yet ->", e); save_db()

load_db()

# ================= HELPERS =================
def seller_store(key):            # key like "s1"
    if key not in DB_STATE["sellers"]:
        DB_STATE["sellers"][key] = new_store()
        DB_STATE["sellers"][key]["tg_id"] = None
    return DB_STATE["sellers"][key]

def store_of_seller_id(tg_id):
    for k, s in DB_STATE["sellers"].items():
        if s.get("tg_id") == tg_id: return k, s
    return None, None

def now_minutes():
    n = datetime.datetime.now()
    return n.hour*60 + n.minute

def takeover_active(seller_key):
    m = now_minutes()
    for t in DB_STATE["takeovers"]:
        if t.get("seller") == seller_key and t.get("active"):
            f, to = t["from_min"], t["to_min"]
            if f <= m < to: return t
    return None

def send_videos_as_album(chat_id, video_list):
    if not video_list: return
    if len(video_list) == 1:
        try: bot.send_video(chat_id, video_list[0])
        except Exception: pass
    else:
        for i in range(0, len(video_list), 10):
            chunk = video_list[i:i+10]
            try: bot.send_media_group(chat_id, [InputMediaVideo(v) for v in chunk])
            except Exception:
                for v in chunk:
                    try: bot.send_video(chat_id, v)
                    except Exception: pass

def send_store_menu(user_id, store, name):
    """User ko wo store ka main menu dikhao. Return: routing context."""
    if store["start_videos"]:
        send_videos_as_album(user_id, store["start_videos"])
    welcome = store.get("welcome_msg", "{name}").format(name=name)
    markup = InlineKeyboardMarkup()
    products = sorted(store.get("products", []), key=lambda x: x.get("position", 999))
    if store.get("layout_style") == "horizontal":
        row = []
        for p in products:
            row.append(InlineKeyboardButton(p["name"], callback_data="prod_{}_{}".format(store["_key"], p["id"])))
            if len(row) == 2: markup.row(*row); row = []
        if row: markup.row(*row)
    else:
        for p in products:
            markup.row(InlineKeyboardButton(p["name"], callback_data="prod_{}_{}".format(store["_key"], p["id"])))
    markup.row(InlineKeyboardButton("How to use ❓", callback_data="how_{}".format(store["_key"])),
               InlineKeyboardButton("Report Issue 📩", callback_data="rep_{}".format(store["_key"])))
    bot.send_message(user_id, welcome, reply_markup=markup, parse_mode="Markdown")

# store me "_key" inject karna
def tag(store, key):
    store["_key"] = key
    for p in store.get("products", []):
        p["_skey"] = key
    return store

# ================= /start =================
@bot.message_handler(commands=['start'])
def start_cmd(message):
    uid, name = message.chat.id, (message.from_user.first_name or "User")
    args = (message.text or "").split()
    payload = args[1] if len(args) > 1 and " " in message.text else ""
    # payload nikalna
    txt = message.text or ""
    payload = txt.split(" ",1)[1].split("?")[0] if " " in txt else ""
    payload = payload.replace("/start","").strip()

    if payload.startswith("s") and payload[1:].isdigit():
        # seller deep link
        key = payload
        seller = DB_STATE["sellers"].get(key)
        if not seller:
            bot.send_message(uid, "❌ This store link is invalid or no longer exists."); return
        if takeover_active(key):
            # owner ne isko apne hub par redirect kiya hai is waqt
            store = DB_STATE["hub"]; store = tag(store, "hub")
            user_states[uid] = {"_mode":"store", "_key":"hub", "_src":key}
            if uid not in DB_STATE["hub"]["users"]: DB_STATE["hub"]["users"].append(uid); save_db()
            bot.send_message(uid, "🛒 You have been redirected to the main store.")
            send_store_menu(uid, store, name)
            return
        store = tag(seller, key)
        user_states[uid] = {"_mode":"store", "_key":key}
        if uid not in seller["users"]: seller["users"].append(uid); save_db()
        send_store_menu(uid, store, name); return

    # ---- no seller link ----
    # seller / owner khud aa raha hai?
    owner_key, owner_store = store_of_seller_id(uid)
    if uid == OWNER_ID:
        user_states[uid] = {"_mode":"owner"}
        show_owner_panel(uid); return
    if owner_key:
        user_states[uid] = {"_mode":"seller", "_key":owner_key}
        show_seller_panel(uid, owner_key); return

    # normal user -> owner hub menu
    user_states[uid] = {"_mode":"store", "_key":"hub"}
    store = tag(DB_STATE["hub"], "hub")
    if uid not in DB_STATE["hub"]["users"]: DB_STATE["hub"]["users"].append(uid); save_db()
    send_store_menu(uid, store, name)

# ---------------- OWNER PANEL ----------------
def show_owner_panel(uid):
    user_states.pop(uid, None)
    user_states[uid] = {"_mode":"owner"}
    m = InlineKeyboardMarkup()
    m.row(InlineKeyboardButton("🏠 My Hub Store (Default)", callback_data="own_hub"))
    m.row(InlineKeyboardButton("👥 Manage Sellers", callback_data="own_sellers"))
    m.row(InlineKeyboardButton("⏱️ Takeover Schedule", callback_data="own_takeover"))
    m.row(InlineKeyboardButton("💰 All Sellers Sales", callback_data="own_sales"))
    m.row(InlineKeyboardButton("📣 Send Broadcast to a Seller's Users", callback_data="own_bc_choose"))
    m.row(InlineKeyboardButton("💾 Backup & Restore", callback_data="backup_menu"))
    m.row(InlineKeyboardButton("🔓 Unblock Users", callback_data="unblock_owner_menu"))
    bot.send_message(uid, "👑 **OWNER PANEL**", reply_markup=m, parse_mode="Markdown")

def show_seller_panel(uid, key):
    user_states.pop(uid, None)
    user_states[uid] = {"_mode":"seller", "_key":key}
    store = seller_store(key)
    m = InlineKeyboardMarkup()
    m.row(InlineKeyboardButton("🛍️ Manage My Store", callback_data="panel_store_"+key))
    if store.get("broadcast_perm"):
        m.row(InlineKeyboardButton("📣 Broadcast Center", callback_data="panel_bc_"+key))
    else:
        m.row(InlineKeyboardButton("🔒 Broadcast locked (ask owner)", callback_data="none"))
    m.row(InlineKeyboardButton("📦 My Buyers", callback_data="mybuyers_"+key))
    bot.send_message(uid, "🏪 **Seller Panel**\n\nManage your own store here.", reply_markup=m, parse_mode="Markdown")

# Full store-config panel (reused by seller for own store)
def store_config_menu(uid, key):
    store = seller_store(key) if key != "hub" else DB_STATE["hub"]
    m = InlineKeyboardMarkup()
    m.row(InlineKeyboardButton("🎞️ Start Videos", callback_data="sv_"+key))
    m.row(InlineKeyboardButton("🛍️ Product Buttons", callback_data="pp_"+key))
    m.row(InlineKeyboardButton("📝 Welcome Text", callback_data="ew_"+key))
    curr = store.get("layout_style")
    m.row(InlineKeyboardButton("📐 Layout: "+("↔️" if curr=="horizontal" else "↕️"), callback_data="tl_"+key))
    m.row(InlineKeyboardButton("🎥 How-To-Use Video", callback_data="hv_"+key))
    m.row(InlineKeyboardButton("💳 Payment Config", callback_data="payc_"+key))
    m.row(InlineKeyboardButton("🚀 Custom Broadcast", callback_data="cb_"+key))
    m.row(InlineKeyboardButton("⏱️ Auto Broadcast", callback_data="ab_"+key))
    m.row(InlineKeyboardButton("👑 Broadcast to Buyers", callback_data="bb_"+key))
    m.row(InlineKeyboardButton("📦 Buyers List", callback_data="bl_"+key))
    m.row(InlineKeyboardButton("💾 Backup", callback_data="backup_store_"+key))
    if key == "hub":
        m.row(InlineKeyboardButton("🔙 Owner Panel", callback_data="to_owner"))
    else:
        m.row(InlineKeyboardButton("🔙 Back", callback_data="to_seller"))
    bot.send_message(uid, "🛠️ **Store Settings** ("+key+")", reply_markup=m, parse_mode="Markdown")
    user_states[uid] = {"_mode": "owner" if uid==OWNER_ID else "seller", "_key": key, "_cfg": True}

# ----------------- CALLBACKS -----------------
@bot.callback_query_handler(func=lambda c: True)
def cb(c):
    try: bot.answer_callback_query(c.id)
    except Exception: pass
    uid = c.message.chat.id
    d = c.data
    ctx = user_states.get(uid, {})
    # ---- user-side store callbacks ----
    if d.startswith("prod_"):
        # prod_<storekey>_<prodid>
        parts = d.split("_"); key, pid = parts[1], parts[2]
        store = seller_store(key) if key!="hub" else DB_STATE["hub"]
        prod = next((p for p in store["products"] if p["id"]==pid), None)
        if not prod: return
        if prod.get("videos"): send_videos_as_album(uid, prod["videos"])
        cap = "📌 **"+prod["name"]+"**"
        if prod.get("desc"): cap += "\n\n"+prod["desc"]
        pay = prod.get("pay_msg") or store.get("payment_msg")
        mk = InlineKeyboardMarkup()
        mk.row(InlineKeyboardButton("I have paid ✅", callback_data="paid_"+key+"_"+pid))
        mk.row(InlineKeyboardButton("Back 🔙", callback_data="home_"+key))
        if store.get("payment_photo"):
            bot.send_photo(uid, store["payment_photo"], caption=cap+"\n\n"+pay, reply_markup=mk, parse_mode="Markdown")
        else:
            bot.send_message(uid, cap+"\n\n"+pay, reply_markup=mk, parse_mode="Markdown")
        return
    if d.startswith("paid_"):
        parts = d.split("_"); key, pid = parts[1], parts[2]
        bot.send_message(uid, "📸 Please send your payment screenshot.")
        user_states[uid] = {"_mode":"store","_wait":"shot","_key":key,"_pid":pid}
        return
    if d.startswith("home_"):
        key = d.split("_",1)[1]
        store = seller_store(key) if key!="hub" else DB_STATE["hub"]
        send_store_menu(uid, store, c.message.from_user.first_name or "User"); return
    if d.startswith("how_"):
        key = d.split("_",1)[1]
        store = seller_store(key) if key!="hub" else DB_STATE["hub"]
        v = store.get("how_to_use_video")
        if v: bot.send_video(uid, v)
        else: bot.send_message(uid,"ℹ️ Instructions video not set yet.")
        return
    if d.startswith("rep_"):
        key = d.split("_",1)[1]
        user_states[uid] = {"_mode":"store","_wait":"report"}
        bot.send_message(uid,"📝 Please type your issue. Admin will reply soon:"); return
    # report reply target -> seller of that store (hub->owner)
    if ctx.get("_wait")=="report":
        pass  # handled in messages

    # ================= ADMIN / SELLER / OWNER callbacks =================
    if uid != OWNER_ID:
        owner_key,_ = store_of_seller_id(uid)
        if not owner_key: return   # normal user has no admin rights
    # ---- seller-level store management ----
    if d.startswith("panel_store_"):
        key = d.split("_",2)[2]; store_config_menu(uid, key); return
    if d.startswith("panel_bc_"):
        key = d.split("_",2)[2]; user_states[uid]={"_mode":"seller","_key":key,"_bc":"custom"}
        bot.send_message(uid,"🚀 Send the message to broadcast to your users:"); return
    if d.startswith("mybuyers_"):
        key = d.split("_",1)[1]; show_buyers(uid,key); return
    if d == "to_owner": show_owner_panel(uid); return
    if d == "to_seller":
        key = store_of_seller_id(uid)[0]; show_seller_panel(uid,key); return
    if d == "none": return

    # generic store-config entry for hub (owner) via owner panel
    if d == "own_hub":
        store_config_menu(uid, "hub"); return

    # unify: many actions are "act_<key>"
    if d.startswith("sv_"):
        key = d.split("_",1)[1]
        store = seller_store(key) if key!="hub" else DB_STATE["hub"]
        user_states[uid]={"_mode":"owner","_key":key,"_wait":"startvids"}
        bot.send_message(uid,"📥 Send videos. When done send /done"); return
    if d.startswith("pp_"):
        key = d.split("_",1)[1]
        store = seller_store(key) if key!="hub" else DB_STATE["hub"]
        user_states[uid]={"_mode":"owner","_key":key,"_wait":"prod"}
        bot.send_message(uid,"🛍️ Enter new product name (or /done):"); return
    if d.startswith("ew_"):
        key = d.split("_",1)[1]
        user_states[uid]={"_mode":"owner","_key":key,"_wait":"welcome"}
        bot.send_message(uid,"📝 Send new Welcome text (use {name}):"); return
    if d.startswith("tl_"):
        key = d.split("_",1)[1]
        st = seller_store(key) if key!="hub" else DB_STATE["hub"]
        st["layout_style"] = "horizontal" if st["layout_style"]!="horizontal" else "vertical"
        save_db(); store_config_menu(uid,key); return
    if d.startswith("hv_"):
        key = d.split("_",1)[1]
        user_states[uid]={"_mode":"owner","_key":key,"_wait":"howvid"}
        bot.send_message(uid,"🎥 Send How-To-Use video:"); return
    if d.startswith("payc_"):
        key = d.split("_",1)[1]
        st = seller_store(key) if key!="hub" else DB_STATE["hub"]
        m=InlineKeyboardMarkup()
        m.row(InlineKeyboardButton("💳 Set QR Photo", callback_data="payphoto_"+key))
        m.row(InlineKeyboardButton("✏️ Set Payment Text", callback_data="paytext_"+key))
        m.row(InlineKeyboardButton("🔙", callback_data="to_cfg_"+key))
        bot.send_message(uid,"💳 **Payment Config**",reply_markup=m,parse_mode="Markdown"); return
    if d.startswith("payphoto_"):
        key=d.split("_",1)[1]; user_states[uid]={"_mode":"owner","_key":key,"_wait":"payphoto"}
        bot.send_message(uid,"Send QR photo:"); return
    if d.startswith("paytext_"):
        key=d.split("_",1)[1]; user_states[uid]={"_mode":"owner","_key":key,"_wait":"paytext"}
        bot.send_message(uid,"Send new payment text:"); return
    if d.startswith("cb_"):
        key=d.split("_",1)[1]; user_states[uid]={"_mode":"owner","_key":key,"_wait":"cb"}
        bot.send_message(uid,"🚀 Send message to broadcast to all users of this store:"); return
    if d.startswith("ab_"):
        key=d.split("_",1)[1]; user_states[uid]={"_mode":"owner","_key":key,"_wait":"ab_set"}
        bot.send_message(uid,"Send the auto-broadcast message, then I'll ask time:"); return
    if d.startswith("bb_"):
        key=d.split("_",1)[1]; user_states[uid]={"_mode":"owner","_key":key,"_wait":"bb"}
        bot.send_message(uid,"👑 Send message to broadcast only to buyers:"); return
    if d.startswith("bl_"):
        key=d.split("_",1)[1]; show_buyers(uid,key); return
    if d.startswith("to_cfg_"):
        key=d.split("_",2)[2]; store_config_menu(uid,key); return
    if d.startswith("backup_store_"):
        key=d.split("_",2)[2]
        bot.send_document(uid, ("backup_{}.json".format(key), json.dumps(DB_STATE, indent=2).encode()))
        bot.send_message(uid,"✅ Backup sent. To restore send the file back."); return
    if d.startswith("adm_confirm_"):
        # adm_confirm_<storekey>_<prodid>_<userid>_<src?>
        parts=d.split("_"); key,pid,tu = parts[2],parts[3],int(parts[4])
        st = seller_store(key) if key!="hub" else DB_STATE["hub"]
        prod = next((p for p in st["products"] if p["id"]==pid),None)
        link = prod["link"] if prod else "No link"
        nm = prod["name"] if prod else "Product"
        st["buyers"].append({"user_id":tu,"name":c.message.from_user.first_name or "User",
            "username":"?","product":nm,"date":datetime.datetime.now().strftime("%Y-%m-%d %H:%M")})
        save_db()
        bot.send_message(tu,"✅ **Payment Confirmed!**\n\n🔗 "+link,parse_mode="Markdown")
        try: bot.delete_message(uid,c.message.message_id)
        except Exception: pass
        return
    if d.startswith("adm_reject_"):
        tu=int(d.split("_")[2]); bot.send_message(tu,"❌ Payment not received. Please try again...")
        try: bot.delete_message(uid,c.message.message_id)
        except Exception: pass
        return
    if d.startswith("adm_block_"):
        tu=int(d.split("_")[2])
        key = ctx.get("_key","hub")
        st = seller_store(key) if key!="hub" else DB_STATE["hub"]
        if tu not in st["blocked_users"]: st["blocked_users"].append(tu); save_db()
        try: bot.delete_message(uid,c.message.message_id)
        except Exception: pass
        return

    # ============= OWNER-only extra =============
    if uid != OWNER_ID: return
    if d=="own_sellers":
        m=InlineKeyboardMarkup()
        m.row(InlineKeyboardButton("➕ Add Seller", callback_data="add_seller"))
        m.row(InlineKeyboardButton("🔓 Give/Revoke Broadcast Permission", callback_data="seller_perm"))
        m.row(InlineKeyboardButton("🔙 Back", callback_data="to_owner"))
        bot.send_message(uid,"👥 **Manage Sellers**",reply_markup=m,parse_mode="Markdown"); return
    if d=="add_seller":
        user_states[uid]={"_mode":"owner","_wait":"addseller"}
        bot.send_message(uid,"Send:  seller-id  tg_id  name\nExample:  s1  123456789  Ramesh"); return
    if d=="seller_perm":
        s="📋 Sellers:\n"
        for k,st in DB_STATE["sellers"].items():
            s+="`{}` tg={} broadcast={} | ID to toggle: perm_{}\n".format(k,st.get("tg_id"),st.get("broadcast_perm"),k)
        user_states[uid]={"_mode":"owner","_wait":"perm"}
        bot.send_message(uid,s+"\n\nSend `perm_<sellerid>` to toggle permission."); return
    if d=="own_takeover":
        m=InlineKeyboardMarkup()
        m.row(InlineKeyboardButton("➕ New Takeover Window", callback_data="tk_add"))
        m.row(InlineKeyboardButton("📋 List / Toggle / Delete", callback_data="tk_list"))
        m.row(InlineKeyboardButton("🔙 Back", callback_data="to_owner"))
        bot.send_message(uid,"⏱️ **Takeover Scheduler**\n\nTakeover = us seller ka deep-link us window mein aapke hub par khulega aur payments aapke paas jayenge.",reply_markup=m,parse_mode="Markdown"); return
    if d=="tk_add":
        user_states[uid]={"_mode":"owner","_wait":"tk_add"}
        bot.send_message(uid,"Send:  sellerid  fromHH:MM  toHH:MM\nExample:  s1  02:00  06:19\n(24h format, server time)"); return
    if d=="tk_list":
        s="⏱️ Takeovers:\n"
        for i,t in enumerate(DB_STATE["takeovers"]):
            s+="`{}` {} {}-{} active={} | del_tk_{}\n".format(i,t["seller"],t["from_min"]//60,t["to_min"]//60,t["active"],i)
        s+="\nToggle: `tog_<index>`   Delete: `del_tk_<index>`"
        user_states[uid]={"_mode":"owner","_wait":"tk_mgmt"}
        bot.send_message(uid,s); return
    if d=="own_sales":
        s="💰 **Sales overview (hub + all sellers):**\n"
        total=0
        def line(name,st):
            nonlocal total
            n=len(st.get("buyers",[])); total+=n
            return "• {}: {} sales\n".format(name,n)
        s+=line("HUB(owner)",DB_STATE["hub"])
        for k,st in DB_STATE["sellers"].items(): s+=line(k,st)
        s+="\n**Total: {}**".format(total)
        bot.send_message(uid,s,parse_mode="Markdown"); return
    if d=="own_bc_choose":
        s="📣 Broadcast to a seller's users:\n"
        for k,st in DB_STATE["sellers"].items():
            s+="`bc_sel_{}` → {} ({} users)\n".format(k,k,len(st.get("users",[])))
        s+="\nSend `bc_sel_<sellerid>` then your message."
        user_states[uid]={"_mode":"owner","_wait":"own_bc"}
        bot.send_message(uid,s); return
    if d=="unblock_owner_menu":
        s="🔓 Unblock from hub:\n"
        for b in DB_STATE["hub"].get("blocked_users",[]): s+="`unblock_hub_{}`\n".format(b)
        user_states[uid]={"_mode":"owner","_wait":"unblock"}
        bot.send_message(uid,s or "No blocked users."); return

# ---------- buyers list ----------
def show_buyers(uid,key):
    st = seller_store(key) if key!="hub" else DB_STATE["hub"]
    if not st.get("buyers"):
        bot.send_message(uid,"📦 No buyers yet."); return
    s="📦 **Buyers** ({})\n".format(key)
    for b in st["buyers"][-20:]:
        s+="• {} | ID `{}` | {} | {}\n".format(b.get("product"),b.get("user_id"),b.get("name"),b.get("date"))
    bot.send_message(uid,s,parse_mode="Markdown")

# ---------- Text / Media input ----------
@bot.message_handler(func=lambda m: True, content_types=['text','photo','video','document'])
def inputs(m):
    uid=m.chat.id
    ctx=user_states.get(uid)
    txt=m.text or m.caption or ""

    # admin reply to a user (report)
    if ctx and ctx.get("_wait")=="report":
        del user_states[uid]
        bot.send_message(uid,"✅ Report sent to admin.")
        # route report: key->seller tg or owner
        key=ctx.get("_key","hub")
        if key=="hub": adm=OWNER_ID
        else:
            st=seller_store(key); adm=st.get("tg_id") or OWNER_ID
        tag=f"@{m.from_user.username}" if m.from_user.username else "NoUser"
        bot.send_message(adm,f"📩 Report from {tag} (`{uid}`):\n\n{m.text}\n\n*Reply to send answer.*",parse_mode="Markdown")
        return

    # screenshot upload
    if ctx and ctx.get("_wait")=="shot" and m.content_type=="photo":
        del user_states[uid]
        key=ctx["_key"]; pid=ctx["_pid"]
        st=seller_store(key) if key!="hub" else DB_STATE["hub"]
        prod=next((p for p in st["products"] if p["id"]==pid),None)
        prod_name=prod["name"] if prod else "Product"
        # routing: hub purchases -> owner; if from takeover source, attribute to src seller but still to owner
        src = ctx.get("_src")  # deep-link seller during takeover
        mk=InlineKeyboardMarkup()
        if key=="hub":
            adm=OWNER_ID
            mk.row(InlineKeyboardButton("CONFIRM ✅",callback_data="adm_confirm_hub_"+pid+"_"+str(uid)),
                   InlineKeyboardButton("REJECT ❌",callback_data="adm_reject_"+str(uid)),
                   InlineKeyboardButton("BLOCK 🚫",callback_data="adm_block_"+str(uid)))
            label = "MAIN/OWNER" + (" (via takeover from {})".format(src) if src else "")
        else:
            adm=st.get("tg_id") or OWNER_ID
            mk.row(InlineKeyboardButton("CONFIRM ✅",callback_data="adm_confirm_"+key+"_"+pid+"_"+str(uid)),
                   InlineKeyboardButton("REJECT ❌",callback_data="adm_reject_"+str(uid)),
                   InlineKeyboardButton("BLOCK 🚫",callback_data="adm_block_"+str(uid)))
            label=key
        tag=f"@{m.from_user.username}" if m.from_user.username else "NoUser"
        bot.send_message(uid,"⏳ Checking your payment... wait 5-10 min.")
        try:
            bot.send_photo(adm,m.photo[-1].file_id,
                caption=f"📸 **New Payment!**\nStore:{label}\nProduct:{prod_name}\nUser:{tag}\nID:`{uid}`",
                reply_markup=mk,parse_mode="Markdown")
        except Exception as e: print("shot send err",e)
        return

    # ==== OWNER / SELLER admin text handling ====
    is_owner = (uid==OWNER_ID)
    owner_key, _ = store_of_seller_id(uid)
    if not is_owner and not owner_key: 
        # normal user free text -> ignore
        return
    key = ctx.get("_key","hub") if ctx else "hub"
    st = seller_store(key) if key!="hub" else DB_STATE["hub"]
    w = ctx.get("_wait") if ctx else None

    if m.content_type=="text" and txt=="/done":
        del user_states[uid]; bot.send_message(uid,"✅ Saved."); return
    if m.content_type=="text" and txt=="/cancel":
        del user_states[uid]; bot.send_message(uid,"Cancelled."); return

    # add seller
    if is_owner and w=="addseller":
        try:
            sid,tg,nm = txt.split()
            tg=int(tg)
            s=new_store(); s["tg_id"]=tg; s["_name"]=nm; s["broadcast_perm"]=False
            DB_STATE["sellers"][sid]=s; save_db()
            bot.send_message(uid,f"✅ Seller {sid} added (tg {tg}). They can press /start to manage store.")
        except Exception: bot.send_message(uid,"❌ Format:  seller-id  tg_id  name")
        del user_states[uid]; return

    if is_owner and w=="perm" and txt.startswith("perm_"):
        sid=txt.split("_",1)[1]
        if sid in DB_STATE["sellers"]:
            DB_STATE["sellers"][sid]["broadcast_perm"] = not DB_STATE["sellers"][sid].get("broadcast_perm",False)
            save_db(); bot.send_message(uid,f"✅ Permission toggled for {sid}")
        del user_states[uid]; return

    if is_owner and w=="tk_add":
        try:
            sid,frm,to=txt.split()
            def hm(x):
                h,mi=x.split(":"); return int(h)*60+int(mi)
            DB_STATE["takeovers"].append({"seller":sid,"from_min":hm(frm),"to_min":hm(to),"active":True})
            save_db(); bot.send_message(uid,f"✅ Takeover window set for {sid}. It auto-applies daily {frm}-{to}.")
        except Exception: bot.send_message(uid,"❌ Format: s1 02:00 06:19")
        del user_states[uid]; return

    if is_owner and w=="tk_mgmt":
        if txt.startswith("tog_"):
            DB_STATE["takeovers"][int(txt.split("_")[1])]["active"] ^= True; save_db()
        elif txt.startswith("del_tk_"):
            DB_STATE["takeovers"].pop(int(txt.split("_")[2])); save_db()
        bot.send_message(uid,"Done."); del user_states[uid]; return

    if is_owner and w=="own_bc" and txt.startswith("bc_sel_"):
        sid=txt.split("_",2)[2]
        user_states[uid]={"_mode":"owner","_bc_target":sid,"_wait":"cb"}
        bot.send_message(uid,f"Now send your broadcast message for {sid}'s users:"); return

    # start videos collect
    if w=="startvids":
        if m.content_type=="video":
            st["start_videos"].append(m.video.file_id); save_db()
            bot.send_message(uid,f"✅ Added. Total {len(st['start_videos'])}. Send more or /done")
        return
    # add product (simple: name then link then desc)
    if w=="prod":
        if m.content_type=="text":
            pid=str(len(st["products"])+1)
            st["products"].append({"id":pid,"name":txt,"desc":"","videos":[],"link":"https://example.com","position":len(st["products"])+1,"pay_msg":""})
            save_db()
            bot.send_message(uid,f"✅ Button added. Send delivery link for it (or /skip):")
            user_states[uid]={"_mode":"owner","_key":key,"_wait":"prod_link","_pid":pid}
        return
    if w=="prod_link":
        if m.content_type=="text" and txt!="/skip":
            p=next((x for x in st["products"] if x["id"]==ctx["_pid"]),None)
            if p: p["link"]=txt; save_db()
        bot.send_message(uid,"✅ Saved. Now send description or /skip");
        user_states[uid]={"_mode":"owner","_key":key,"_wait":"prod_desc","_pid":ctx["_pid"]}
        return
    if w=="prod_desc":
        if m.content_type=="text" and txt!="/skip":
            p=next((x for x in st["products"] if x["id"]==ctx["_pid"]),None)
            if p: p["desc"]=txt; save_db()
        del user_states[uid]; bot.send_message(uid,"✅ Product complete."); return

    if w=="welcome" and m.content_type=="text":
        st["welcome_msg"]=txt; save_db(); del user_states[uid]; bot.send_message(uid,"✅ Welcome updated."); return
    if w=="howvid" and m.content_type=="video":
        st["how_to_use_video"]=m.video.file_id; save_db(); del user_states[uid]; bot.send_message(uid,"✅ Saved."); return
    if w=="payphoto" and m.content_type=="photo":
        st["payment_photo"]=m.photo[-1].file_id; save_db(); del user_states[uid]; bot.send_message(uid,"✅ QR set."); return
    if w=="paytext" and m.content_type=="text":
        st["payment_msg"]=txt; save_db(); del user_states[uid]; bot.send_message(uid,"✅ Payment text set."); return

    # ---- broadcast send ----
    if w=="cb":
        do_broadcast(uid,st.get("users",[]),m); del user_states[uid]; return
    if w=="bb":
        usrs=[b.get("user_id") for b in st.get("buyers",[])]
        do_broadcast(uid,usrs,m); del user_states[uid]; return
    if w=="ab_set":
        # store auto msg, then ask interval
        store_auto_msg(st,m)
        user_states[uid]={"_mode":"owner","_key":key,"_wait":"ab_time"}
        bot.send_message(uid,"Interval seconds (e.g. 3600 = 1hr):"); return
    if w=="ab_time" and m.content_type=="text":
        try:
            st["auto_bc"]["interval_seconds"]=int(txt); st["auto_bc"]["status"]=True; save_db()
            bot.send_message(uid,f"✅ Auto-broadcast ON every {int(txt)} sec for {key}.")
        except Exception: bot.send_message(uid,"❌ Invalid number.")
        del user_states[uid]; return

def store_auto_msg(st,m):
    if m.content_type=="text":
        st["auto_bc"]={"status":True,"interval_seconds":3600,"message_type":"text","file_id":None,"text":m.text}
    elif m.content_type=="photo":
        st["auto_bc"]={"status":True,"interval_seconds":3600,"message_type":"photo","file_id":m.photo[-1].file_id,"text":m.caption or ""}
    elif m.content_type=="video":
        st["auto_bc"]={"status":True,"interval_seconds":3600,"message_type":"video","file_id":m.video.file_id,"text":m.caption or ""}
    save_db()

def do_broadcast(uid,usrs,m):
    ok=fail=0
    for u in usrs:
        try:
            if m.content_type=="text": bot.send_message(u,m.text,parse_mode="Markdown")
            elif m.content_type=="photo": bot.send_photo(u,m.photo[-1].file_id,caption=m.caption,parse_mode="Markdown")
            elif m.content_type=="video": bot.send_video(u,m.video.file_id,caption=m.caption,parse_mode="Markdown")
            elif m.content_type=="document": bot.send_document(u,m.document.file_id,caption=m.caption,parse_mode="Markdown")
            ok+=1
        except Exception: fail+=1
    bot.send_message(uid,f"✅ Sent: {ok}   Failed: {fail}")

# ---------- Auto-broadcast worker ----------
def worker():
    while True:
        try:
            for key,st in DB_STATE["sellers"].items():
                run_auto(key,st)
            run_auto("hub",DB_STATE["hub"])
        except Exception: pass
        time.sleep(2)

def run_auto(key,st):
    bc=st.get("auto_bc",{})
    if not bc.get("status"): return
    now=int(time.time())
    if bc.get("last") and now-bc["last"] < bc.get("interval_seconds",3600): return
    bc["last"]=now; save_db()
    for u in list(st.get("users",[])):
        if u in st.get("blocked_users",[]): continue
        try:
            t=bc.get("message_type")
            if t=="photo": bot.send_photo(u,bc["file_id"],caption=bc.get("text"),parse_mode="Markdown")
            elif t=="video": bot.send_video(u,bc["file_id"],caption=bc.get("text"),parse_mode="Markdown")
            elif t=="document": bot.send_document(u,bc["file_id"],caption=bc.get("text"),parse_mode="Markdown")
            else: bot.send_message(u,bc.get("text"),parse_mode="Markdown")
        except Exception: pass

@app.route('/')
def home(): return "Bot running!"

if __name__ == "__main__":
    threading.Thread(target=lambda: bot.infinity_polling(), daemon=True).start()
    threading.Thread(target=worker, daemon=True).start()
    port=int(os.environ.get("PORT",5000))
    app.run(host="0.0.0.0",port=port)
