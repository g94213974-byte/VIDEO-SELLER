import os
import json
import threading
from flask import Flask
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaVideo
from apscheduler.schedulers.background import BackgroundScheduler

# --- ENVIRONMENT VARIABLES ---
TOKEN = os.environ.get('BOT_TOKEN')
ADMIN_ID = int(os.environ.get('ADMIN_ID', '0'))
LOG_CHANNEL_ID = int(os.environ.get('LOG_CHANNEL_ID', '0')) # Private Channel ID for DB

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
    "broadcast_msg": "",
    "broadcast_minutes": 3, 
    "broadcast_enabled": True, 
    "layout_style": "vertical", 
    "products": [], # Each prod: {id, name, desc, videos, link, position, pay_msg}
    "blocked_users": [],
    "users": []
}

# --- TELEGRAM CHANNEL DATABASE LOGIC ---
def load_db():
    global DB_STATE
    try:
        chat = bot.get_chat(LOG_CHANNEL_ID)
        if chat.pinned_message and chat.pinned_message.text:
            loaded_data = json.loads(chat.pinned_message.text)
            DB_STATE.update(loaded_data)
            if "broadcast_enabled" not in DB_STATE: DB_STATE["broadcast_enabled"] = True
            print("✅ Data loaded from Telegram Channel!")
    except Exception as e:
        print(f"⚠️ Saving initial DB: {e}")
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
        print("💾 DB saved to Telegram Channel.")
    except Exception as e:
        print(f"❌ Error saving DB: {e}")

load_db()

user_states = {}
admin_panel_msgs = {} 

# --- HELPER FUNCTION: SEND VIDEOS AS ALBUM GRID ---
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

# ==========================================
# 🔄 AUTO BROADCAST SYSTEM
# ==========================================
def send_auto_broadcast():
    if not DB_STATE.get("broadcast_enabled", True):
        return
    msg_text = DB_STATE.get("broadcast_msg", "")
    if not msg_text:
        return
    for u_id in DB_STATE.get("users", []):
        if u_id in DB_STATE.get("blocked_users", []):
            continue
        try: bot.send_message(u_id, msg_text)
        except: pass

scheduler = BackgroundScheduler()

def reset_broadcast_job():
    try: scheduler.remove_job('bc_job')
    except: pass
    mins = int(DB_STATE.get("broadcast_minutes", 3))
    if mins < 1: mins = 1
    scheduler.add_job(send_auto_broadcast, 'interval', minutes=mins, id='bc_job')

scheduler.start()
reset_broadcast_job()

# ==========================================
# 🚀 USER & ADMIN /START COMMAND
# ==========================================
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

    if user_id == ADMIN_ID:
        markup.row(InlineKeyboardButton("⚙️ Open Admin Panel ⚙️", callback_data="adm_open_panel"))

    products = sorted(DB_STATE.get("products", []), key=lambda x: x.get("position", 999))
    layout = DB_STATE.get("layout_style", "vertical")

    if layout == "horizontal":
        row_btns = []
        for p in products:
            row_btns.append(InlineKeyboardButton(p["name"], callback_data=f"prod_{p['id']}"))
            if len(row_btns) == 2:
                markup.row(*row_btns)
                row_btns = []
        if row_btns:
            markup.row(*row_btns)
    else:
        for p in products:
            markup.row(InlineKeyboardButton(p["name"], callback_data=f"prod_{p['id']}"))

    markup.row(
        InlineKeyboardButton("How to use ❓", callback_data="how_to_use"),
        InlineKeyboardButton("Report Issue 📩", callback_data="report_issue")
    )

    bot.send_message(user_id, welcome_text, reply_markup=markup, parse_mode="Markdown")

# ==========================================
# 🛠️ ADMIN IN-PLACE PANEL SYSTEM
# ==========================================
def update_admin_panel(chat_id, text, markup):
    msg_id = admin_panel_msgs.get(chat_id)
    if msg_id:
        try:
            bot.edit_message_text(text, chat_id, msg_id, reply_markup=markup, parse_mode="Markdown")
            return
        except Exception:
            pass
    msg = bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")
    admin_panel_msgs[chat_id] = msg.message_id

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
    
    markup.row(InlineKeyboardButton("🚀 Send Custom Broadcast", callback_data="adm_send_custom_bc"))
    
    bc_mins = DB_STATE.get("broadcast_minutes", 3)
    bc_status = "🟢 ON" if DB_STATE.get("broadcast_enabled", True) else "🔴 OFF"
    markup.row(
        InlineKeyboardButton(f"💬 Auto-BC Text", callback_data="adm_set_bc_text"),
        InlineKeyboardButton(f"⏱️ Time ({bc_mins}m)", callback_data="adm_set_bc_time")
    )
    markup.row(InlineKeyboardButton(f"📢 Auto-Broadcast Switch: {bc_status}", callback_data="adm_toggle_bc_status"))
    
    blocked_count = len(DB_STATE.get("blocked_users", []))
    if blocked_count > 0:
        markup.row(InlineKeyboardButton(f"🔓 Unblock Users ({blocked_count})", callback_data="adm_unblock_menu"))

    text = "👑 **Admin Control Panel**\n\nChoose an option below to customize your bot completely:"
    update_admin_panel(chat_id, text, markup)

# ==========================================
# 🔄 CALLBACK QUERY HANDLER
# ==========================================
@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    # ⚡ FIX 1: This makes buttons respond INSTANTLY instead of loading/spinning ⚡
    try:
        bot.answer_callback_query(call.id)
    except:
        pass

    user_id = call.message.chat.id
    data = call.data
    msg_id = call.message.message_id

    if data == "adm_open_panel" and user_id == ADMIN_ID:
        try: bot.delete_message(user_id, msg_id)
        except: pass
        show_main_admin_menu(ADMIN_ID)
        return

    if data == "del_msg":
        try: bot.delete_message(user_id, msg_id)
        except: pass
        return

    # --- USER CALLBACKS ---
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
        prod = next((p for p in DB_STATE["products"] if p["id"] == prod_id), None)
        if prod:
            p_videos = prod.get("videos", [])
            if p_videos: send_videos_as_album(user_id, p_videos)
            caption = f"📌 **{prod['name']}**\n\n{prod.get('desc', 'Product details')}"
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton("Buy Now 🛒", callback_data=f"buynow_{prod_id}"))
            markup.row(InlineKeyboardButton("Back 🔙", callback_data="back_home"))
            bot.send_message(user_id, caption, reply_markup=markup, parse_mode="Markdown")

    elif data.startswith("buynow_"):
        prod_id = data.split("_")[1]
        prod = next((p for p in DB_STATE["products"] if p["id"] == prod_id), None)
        markup = InlineKeyboardMarkup()
        markup.row(InlineKeyboardButton("I have paid ✅", callback_data=f"paid_{prod_id}"))
        markup.row(InlineKeyboardButton("Cancel ❌", callback_data="back_home"))

        if prod and prod.get("pay_msg"):
            pay_msg = prod["pay_msg"]
        else:
            pay_msg = DB_STATE.get("payment_msg", "💳 **Payment Instructions**\n\nPlease scan the QR and pay, then click 'I have paid'.")
            
        pay_photo = DB_STATE.get("payment_photo", "")
        
        if pay_photo: 
            bot.send_photo(user_id, pay_photo, caption=pay_msg, reply_markup=markup, parse_mode="Markdown")
        else: 
            bot.send_message(user_id, pay_msg, reply_markup=markup, parse_mode="Markdown")

    elif data.startswith("paid_"):
        prod_id = data.split("_")[1]
        bot.send_message(user_id, "📸 Please send your payment screenshot.")
        user_states[user_id] = f"WAITING_SCREENSHOT_{prod_id}"

    # --- ADMIN ACTIONS ---
    if user_id == ADMIN_ID:
        
        if data == "adm_start_vids_menu":
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton("➕ Add Start Videos", callback_data="adm_add_start_vid"))
            markup.row(InlineKeyboardButton("⚙️ Manage / Delete Videos", callback_data="adm_del_start_vid_list"))
            markup.row(InlineKeyboardButton("🔙 Back to Main Menu", callback_data="adm_back_panel"))
            v_count = len(DB_STATE.get("start_videos", []))
            update_admin_panel(ADMIN_ID, f"🎞️ **Start Videos Management**\n\nTotal Saved Videos: {v_count}", markup)

        elif data == "adm_add_start_vid":
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton("✅ Done Adding Videos", callback_data="adm_finish_start_vids"))
            markup.row(InlineKeyboardButton("🔙 Cancel & Back", callback_data="adm_start_vids_menu"))
            update_admin_panel(ADMIN_ID, "📥 **Send or Forward all your videos one by one or as an album.**\n\nWhen you are finished, click the 'Done' button below:", markup)
            user_states[ADMIN_ID] = "ADM_ADD_START_VID_MULTIPLE"

        elif data == "adm_finish_start_vids":
            show_main_admin_menu(ADMIN_ID)

        elif data == "adm_del_start_vid_list":
            markup = InlineKeyboardMarkup()
            vids = DB_STATE.get("start_videos", [])
            for idx, v_id in enumerate(vids):
                markup.row(
                    InlineKeyboardButton(f"👀 Play Vid {idx+1}", callback_data=f"sv_see_{idx}"),
                    InlineKeyboardButton(f"🗑️ Delete Vid {idx+1}", callback_data=f"sv_del_{idx}")
                )
            if vids:
                markup.row(InlineKeyboardButton("💥 Delete All Start Videos", callback_data="sv_del_all"))
            markup.row(InlineKeyboardButton("🔙 Back to Videos Menu", callback_data="adm_start_vids_menu"))
            update_admin_panel(ADMIN_ID, "⚙️ **Manage Start Videos**\nSelect an action:", markup)

        elif data.startswith("sv_see_"):
            idx = int(data.replace("sv_see_", ""))
            vids = DB_STATE.get("start_videos", [])
            if 0 <= idx < len(vids):
                m = InlineKeyboardMarkup()
                m.row(InlineKeyboardButton("❌ Close Media", callback_data="del_msg"))
                bot.send_video(ADMIN_ID, vids[idx], caption=f"🎥 Start Video {idx+1}", reply_markup=m)

        elif data.startswith("sv_del_"):
            if data == "sv_del_all":
                DB_STATE["start_videos"] = []
            else:
                idx = int(data.replace("sv_del_", ""))
                vids = DB_STATE.get("start_videos", [])
                if 0 <= idx < len(vids):
                    vids.pop(idx)
            save_db()
            call.data = "adm_del_start_vid_list"
            handle_callbacks(call)

        elif data == "adm_prod_menu":
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton("❇️ Add New Button", callback_data="adm_add_prod"))
            markup.row(InlineKeyboardButton("✏️ Edit Details / Link", callback_data="adm_prod_edit_list"))
            markup.row(InlineKeyboardButton("🔢 Change Position", callback_data="adm_prod_pos_list"))
            markup.row(InlineKeyboardButton("🎦 Add Videos", callback_data="adm_prod_add_vid_list"))
            markup.row(InlineKeyboardButton("⚙️ Manage Videos", callback_data="adm_prod_del_vid_list"))
            markup.row(InlineKeyboardButton("🗑️ Delete Button", callback_data="adm_del_prod_list"))
            markup.row(InlineKeyboardButton("🔙 Back to Main Menu", callback_data="adm_back_panel"))
            update_admin_panel(ADMIN_ID, "🛍️ **Product Button Management**\nSelect what you want to modify:", markup)

        elif data == "adm_add_prod":
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton("🔙 Cancel & Back", callback_data="adm_prod_menu"))
            update_admin_panel(ADMIN_ID, "✍️ **Enter New Button Name** (e.g., VIP Plan):", markup)
            user_states[ADMIN_ID] = "ADM_ADD_PROD_NAME"

        elif data == "adm_prod_edit_list":
            markup = InlineKeyboardMarkup()
            for p in DB_STATE.get("products", []):
                markup.row(InlineKeyboardButton(f"✏️ Edit: {p['name']}", callback_data=f"adm_p_edit_{p['id']}"))
            markup.row(InlineKeyboardButton("🔙 Back to Button Menu", callback_data="adm_prod_menu"))
            update_admin_panel(ADMIN_ID, "📌 Select a button to edit its Details, Link or Payment text:", markup)

        elif data.startswith("adm_p_edit_"):
            p_id = data.replace("adm_p_edit_", "")
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton("✏️ Edit Name", callback_data=f"adm_ped_name_{p_id}"))
            markup.row(InlineKeyboardButton("✏️ Edit Description", callback_data=f"adm_ped_desc_{p_id}"))
            markup.row(InlineKeyboardButton("🔗 Edit Link", callback_data=f"adm_ped_link_{p_id}"))
            markup.row(InlineKeyboardButton("💳 Edit Payment Text", callback_data=f"adm_ped_paym_{p_id}")) 
            markup.row(InlineKeyboardButton("🔙 Back", callback_data="adm_prod_edit_list"))
            
            prod = next((p for p in DB_STATE["products"] if p["id"] == p_id), None)
            if prod:
                custom_pay = prod.get('pay_msg', 'Using Global Default Message')
                update_admin_panel(ADMIN_ID, f"✏️ **Editing Button:** `{prod['name']}`\n\n- Current Desc: {prod.get('desc', '')}\n- Current Link: {prod.get('link', '')}\n- Payment Text: {custom_pay}\n\nChoose what to change:", markup)

        elif data.startswith("adm_ped_name_"):
            p_id = data.replace("adm_ped_name_", "")
            user_states[ADMIN_ID] = f"EDIT_P_NAME_{p_id}"
            update_admin_panel(ADMIN_ID, "✍️ Send new name for this button:", InlineKeyboardMarkup().row(InlineKeyboardButton("🔙 Back", callback_data=f"adm_p_edit_{p_id}")))

        elif data.startswith("adm_ped_desc_"):
            p_id = data.replace("adm_ped_desc_", "")
            user_states[ADMIN_ID] = f"EDIT_P_DESC_{p_id}"
            update_admin_panel(ADMIN_ID, "✍️ Send new Product Details / Description text:", InlineKeyboardMarkup().row(InlineKeyboardButton("🔙 Back", callback_data=f"adm_p_edit_{p_id}")))

        elif data.startswith("adm_ped_link_"):
            p_id = data.replace("adm_ped_link_", "")
            user_states[ADMIN_ID] = f"EDIT_P_LINK_{p_id}"
            update_admin_panel(ADMIN_ID, "🔗 Send new delivery link:", InlineKeyboardMarkup().row(InlineKeyboardButton("🔙 Back", callback_data=f"adm_p_edit_{p_id}")))

        elif data.startswith("adm_ped_paym_"):
            p_id = data.replace("adm_ped_paym_", "")
            user_states[ADMIN_ID] = f"EDIT_P_PAYM_{p_id}"
            update_admin_panel(ADMIN_ID, "💳 **Send new Payment Instructions specifically for this button:**\n(Send any text you want user to see when they click Buy Now)", InlineKeyboardMarkup().row(InlineKeyboardButton("🔙 Back", callback_data=f"adm_p_edit_{p_id}")))

        elif data == "adm_prod_pos_list":
            markup = InlineKeyboardMarkup()
            for idx, p in enumerate(sorted(DB_STATE.get("products", []), key=lambda x: x.get("position", 999))):
                markup.row(InlineKeyboardButton(f"Position #{idx+1} ➡️ {p['name']}", callback_data=f"adm_p_pos_{p['id']}"))
            markup.row(InlineKeyboardButton("🔙 Back to Button Menu", callback_data="adm_prod_menu"))
            update_admin_panel(ADMIN_ID, "🔢 **Change Button Position/Order**\nClick a button to change its number position:", markup)

        elif data.startswith("adm_p_pos_"):
            p_id = data.replace("adm_p_pos_", "")
            user_states[ADMIN_ID] = f"EDIT_P_POS_{p_id}"
            update_admin_panel(ADMIN_ID, "🔢 Enter the new serial/position number (e.g. 1, 2, 3...):", InlineKeyboardMarkup().row(InlineKeyboardButton("🔙 Back", callback_data="adm_prod_pos_list")))

        elif data == "adm_prod_add_vid_list":
            markup = InlineKeyboardMarkup()
            for p in DB_STATE.get("products", []):
                markup.row(InlineKeyboardButton(f"🎦 Add Videos to: {p['name']}", callback_data=f"adm_p_addvid_{p['id']}"))
            markup.row(InlineKeyboardButton("🔙 Back to Button Menu", callback_data="adm_prod_menu"))
            update_admin_panel(ADMIN_ID, "📌 Select a button to add videos to:", markup)

        elif data.startswith("adm_p_addvid_"):
            p_id = data.replace("adm_p_addvid_", "")
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton("✅ Done Adding Videos", callback_data=f"adm_p_finish_{p_id}"))
            markup.row(InlineKeyboardButton("🔙 Cancel & Back", callback_data="adm_prod_add_vid_list"))
            update_admin_panel(ADMIN_ID, "📥 **Send or forward all videos for this button.**\nWhen finished, click the button below:", markup)
            user_states[ADMIN_ID] = f"ADM_UPL_PROD_VID_MULTIPLE_{p_id}"

        elif data.startswith("adm_p_finish_"):
            show_main_admin_menu(ADMIN_ID)

        elif data == "adm_prod_del_vid_list":
            markup = InlineKeyboardMarkup()
            for p in DB_STATE.get("products", []):
                v_count = len(p.get("videos", []))
                markup.row(InlineKeyboardButton(f"⚙️ Manage Videos ({v_count}): {p['name']}", callback_data=f"adm_p_mngv_{p['id']}"))
            markup.row(InlineKeyboardButton("🔙 Back to Button Menu", callback_data="adm_prod_menu"))
            update_admin_panel(ADMIN_ID, "📌 Select a button to view/delete its videos:", markup)

        elif data.startswith("adm_p_mngv_"):
            p_id = data.replace("adm_p_mngv_", "")
            prod = next((p for p in DB_STATE["products"] if p["id"] == p_id), None)
            if prod:
                markup = InlineKeyboardMarkup()
                vids = prod.get("videos", [])
                for idx, v_id in enumerate(vids):
                    markup.row(
                        InlineKeyboardButton(f"👀 Play Vid {idx+1}", callback_data=f"pv_see_{p_id}_{idx}"),
                        InlineKeyboardButton(f"🗑️ Delete Vid {idx+1}", callback_data=f"pv_del_{p_id}_{idx}")
                    )
                if vids:
                    markup.row(InlineKeyboardButton("💥 Delete All Videos", callback_data=f"pv_dall_{p_id}"))
                markup.row(InlineKeyboardButton("🔙 Back to Button Selection", callback_data="adm_prod_del_vid_list"))
                update_admin_panel(ADMIN_ID, f"🎦 **Manage videos for '{prod['name']}'**:", markup)

        elif data.startswith("pv_see_"):
            _, _, p_id, idx_str = data.split("_")
            idx = int(idx_str)
            prod = next((p for p in DB_STATE["products"] if p["id"] == p_id), None)
            if prod and "videos" in prod and 0 <= idx < len(prod["videos"]):
                m = InlineKeyboardMarkup()
                m.row(InlineKeyboardButton("❌ Close Media", callback_data="del_msg"))
                bot.send_video(ADMIN_ID, prod["videos"][idx], caption=f"🎥 Video {idx+1} of '{prod['name']}'", reply_markup=m)

        elif data.startswith("pv_del_"):
            _, _, p_id, idx_str = data.split("_")
            idx = int(idx_str)
            prod = next((p for p in DB_STATE["products"] if p["id"] == p_id), None)
            if prod and "videos" in prod and 0 <= idx < len(prod["videos"]):
                prod["videos"].pop(idx)
                save_db()
            call.data = f"adm_p_mngv_{p_id}"
            handle_callbacks(call)

        elif data.startswith("pv_dall_"):
            p_id = data.replace("pv_dall_", "")
            prod = next((p for p in DB_STATE["products"] if p["id"] == p_id), None)
            if prod:
                prod["videos"] = []
                save_db()
            call.data = f"adm_p_mngv_{p_id}"
            handle_callbacks(call)

        elif data == "adm_del_prod_list":
            markup = InlineKeyboardMarkup()
            for p in DB_STATE.get("products", []):
                markup.row(InlineKeyboardButton(f"🗑️ Delete: {p['name']}", callback_data=f"adm_del_p_{p['id']}"))
            markup.row(InlineKeyboardButton("🔙 Back to Button Menu", callback_data="adm_prod_menu"))
            update_admin_panel(ADMIN_ID, "⚠️ Click a button to delete it completely:", markup)

        elif data.startswith("adm_del_p_"):
            p_id = data.replace("adm_del_p_", "")
            DB_STATE["products"] = [p for p in DB_STATE["products"] if p["id"] != p_id]
            save_db()
            call.data = "adm_del_prod_list"
            handle_callbacks(call)

        elif data == "adm_pay_config_menu":
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton("💳 Set Global Payment QR/Photo", callback_data="adm_set_pay_photo"))
            markup.row(InlineKeyboardButton("✏️ Edit Global Payment Text", callback_data="adm_edit_pay_msg"))
            markup.row(InlineKeyboardButton("🔙 Back to Main Menu", callback_data="adm_back_panel"))
            update_admin_panel(ADMIN_ID, "💳 **Global Payment Configuration**\n(This will be used if a button does not have its own specific payment text)", markup)

        elif data == "adm_edit_pay_msg":
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton("🔙 Cancel & Back", callback_data="adm_pay_config_menu"))
            update_admin_panel(ADMIN_ID, f"✍️ **Current Global Payment Instructions:**\n\n`{DB_STATE.get('payment_msg', '')}`\n\nSend new payment instructions text:", markup)
            user_states[ADMIN_ID] = "ADM_SET_PAY_MSG_TEXT"

        elif data == "adm_edit_welcome":
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton("🔙 Cancel & Back", callback_data="adm_back_panel"))
            update_admin_panel(ADMIN_ID, "📝 **Send new Welcome Text.**\nUse `{name}` for user name variable:", markup)
            user_states[ADMIN_ID] = "ADM_SET_WELCOME"

        elif data == "adm_toggle_layout":
            curr = DB_STATE.get("layout_style", "vertical")
            DB_STATE["layout_style"] = "horizontal" if curr == "vertical" else "vertical"
            save_db()
            show_main_admin_menu(ADMIN_ID)

        elif data == "adm_set_how_vid":
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton("🔙 Cancel & Back", callback_data="adm_back_panel"))
            update_admin_panel(ADMIN_ID, "🎥 **Please send/upload 'How To Use' Video:**", markup)
            user_states[ADMIN_ID] = "ADM_SET_HOW_VID"

        elif data == "adm_set_pay_photo":
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton("🔙 Cancel & Back", callback_data="adm_pay_config_menu"))
            update_admin_panel(ADMIN_ID, "💳 **Please send Payment QR Code Photo:**", markup)
            user_states[ADMIN_ID] = "ADM_SET_PAY_PHOTO"

        elif data == "adm_send_custom_bc":
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton("🔙 Cancel & Back", callback_data="adm_back_panel"))
            update_admin_panel(ADMIN_ID, "🚀 **Send the message (Text, Photo, or Video)** that you want to broadcast to all users right now:", markup)
            user_states[ADMIN_ID] = "WAITING_CUSTOM_BROADCAST"

        elif data == "adm_set_bc_text":
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton("🔙 Cancel & Back", callback_data="adm_back_panel"))
            update_admin_panel(ADMIN_ID, "💬 **Enter text for Auto-Broadcast message:**", markup)
            user_states[ADMIN_ID] = "ADM_SET_BC_TEXT"

        elif data == "adm_set_bc_time":
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton("🔙 Cancel & Back", callback_data="adm_back_panel"))
            update_admin_panel(ADMIN_ID, "⏱️ **Enter Auto-Broadcast interval in MINUTES** (e.g. 3, 5, 10):", markup)
            user_states[ADMIN_ID] = "ADM_SET_BC_TIME"

        elif data == "adm_toggle_bc_status":
            curr_status = DB_STATE.get("broadcast_enabled", True)
            DB_STATE["broadcast_enabled"] = not curr_status
            save_db()
            show_main_admin_menu(ADMIN_ID)

        elif data == "adm_unblock_menu":
            markup = InlineKeyboardMarkup()
            blocked_users = DB_STATE.get("blocked_users", [])
            for b_id in blocked_users:
                markup.row(InlineKeyboardButton(f"🔓 Unblock ID: {b_id}", callback_data=f"adm_unblock_exec_{b_id}"))
            markup.row(InlineKeyboardButton("🔙 Back to Main Menu", callback_data="adm_back_panel"))
            update_admin_panel(ADMIN_ID, "🛡️ Select a user to unblock:", markup)

        elif data.startswith("adm_unblock_exec_"):
            b_id = int(data.replace("adm_unblock_exec_", ""))
            if b_id in DB_STATE.get("blocked_users", []):
                DB_STATE["blocked_users"].remove(b_id)
                save_db()
            call.data = "adm_unblock_menu"
            handle_callbacks(call)

        elif data == "adm_back_panel":
            show_main_admin_menu(ADMIN_ID)

        # Payment Validation Actions
        elif data.startswith("adm_confirm_"):
            try:
                parts = data.split("_")
                prod_id = parts[2]
                target_user = int(parts[3])
                prod = next((p for p in DB_STATE["products"] if p["id"] == prod_id), None)
                link = prod.get("link", "No link") if prod else "No link"
                bot.send_message(target_user, f"✅ **Payment Confirmed!**\n\nLink:\n🔗 {link}", parse_mode="Markdown")
                
                try: bot.edit_message_caption(caption=f"{call.message.caption}\n\n✅ **Status:** Confirmed & Link Sent!", chat_id=user_id, message_id=msg_id, parse_mode="Markdown")
                except: pass
            except Exception as e:
                print(f"Error confirm: {e}")

        elif data.startswith("adm_reject_"):
            try:
                target_user = int(data.split("_")[2])
                bot.send_message(target_user, "❌ 𝗣𝗮𝘆𝗺𝗲𝗻𝘁 𝗻𝗼𝘁 𝗿𝗲𝗰𝗶𝘃𝗲. 𝗣𝗹𝗲𝗮𝘀𝗲 𝘁𝗿𝘆 𝗮𝗴𝗮𝗶𝗻...")
                try: bot.edit_message_caption(caption=f"{call.message.caption}\n\n❌ **Status:** Rejected by Admin", chat_id=user_id, message_id=msg_id, parse_mode="Markdown")
                except: pass
            except Exception as e:
                print(f"Error reject: {e}")

        elif data.startswith("adm_block_"):
            try:
                target_user = int(data.split("_")[2])
                if target_user not in DB_STATE["blocked_users"]:
                    DB_STATE["blocked_users"].append(target_user)
                    save_db()
                try: bot.edit_message_caption(caption=f"{call.message.caption}\n\n🚫 **Status:** User Blocked!", chat_id=user_id, message_id=msg_id, parse_mode="Markdown")
                except: pass
            except Exception as e:
                print(f"Error block: {e}")

# ==========================================
# 📩 INPUT & MEDIA HANDLER
# ==========================================
@bot.message_handler(content_types=['photo', 'video', 'text', 'document'])
def handle_all_inputs(message):
    user_id = message.chat.id

    if user_id in DB_STATE.get("blocked_users", []):
        return

    state = user_states.get(user_id, "")

    if user_id == ADMIN_ID:
        if state:
            try: bot.delete_message(ADMIN_ID, message.message_id)
            except: pass

        if state == "ADM_ADD_START_VID_MULTIPLE" and message.content_type == 'video':
            if "start_videos" not in DB_STATE:
                DB_STATE["start_videos"] = []
            DB_STATE["start_videos"].append(message.video.file_id)
            save_db()
            
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton("✅ Done Adding Videos", callback_data="adm_finish_start_vids"))
            markup.row(InlineKeyboardButton("🔙 Cancel & Back", callback_data="adm_start_vids_menu"))
            update_admin_panel(ADMIN_ID, f"📥 **Send or Forward more videos!**\n\n✅ Currently added: {len(DB_STATE['start_videos'])} videos.", markup)
            return

        elif state.startswith("ADM_UPL_PROD_VID_MULTIPLE_") and message.content_type == 'video':
            p_id = state.replace("ADM_UPL_PROD_VID_MULTIPLE_", "")
            prod = next((p for p in DB_STATE["products"] if p["id"] == p_id), None)
            if prod:
                if "videos" not in prod:
                    prod["videos"] = []
                prod["videos"].append(message.video.file_id)
                save_db()
                markup = InlineKeyboardMarkup()
                markup.row(InlineKeyboardButton("✅ Done Adding Videos", callback_data=f"adm_p_finish_{p_id}"))
                markup.row(InlineKeyboardButton("🔙 Back to Button Menu", callback_data="adm_prod_menu"))
                update_admin_panel(ADMIN_ID, f"📥 **Send more videos for '{prod['name']}'!**\n\n✅ Total added: {len(prod['videos'])}", markup)
            return

        elif state.startswith("EDIT_P_NAME_") and message.text:
            p_id = state.replace("EDIT_P_NAME_", "")
            prod = next((p for p in DB_STATE["products"] if p["id"] == p_id), None)
            if prod:
                prod["name"] = message.text
                save_db()
            user_states.pop(user_id, None)
            show_main_admin_menu(ADMIN_ID)
            return

        elif state.startswith("EDIT_P_DESC_") and message.text:
            p_id = state.replace("EDIT_P_DESC_", "")
            prod = next((p for p in DB_STATE["products"] if p["id"] == p_id), None)
            if prod:
                prod["desc"] = message.text
                save_db()
            user_states.pop(user_id, None)
            show_main_admin_menu(ADMIN_ID)
            return

        elif state.startswith("EDIT_P_LINK_") and message.text:
            p_id = state.replace("EDIT_P_LINK_", "")
            prod = next((p for p in DB_STATE["products"] if p["id"] == p_id), None)
            if prod:
                prod["link"] = message.text
                save_db()
            user_states.pop(user_id, None)
            show_main_admin_menu(ADMIN_ID)
            return
            
        elif state.startswith("EDIT_P_PAYM_") and message.text:
            p_id = state.replace("EDIT_P_PAYM_", "")
            prod = next((p for p in DB_STATE["products"] if p["id"] == p_id), None)
            if prod:
                prod["pay_msg"] = message.text
                save_db()
            user_states.pop(user_id, None)
            show_main_admin_menu(ADMIN_ID)
            return

        elif state.startswith("EDIT_P_POS_") and message.text:
            p_id = state.replace("EDIT_P_POS_", "")
            try:
                new_pos = int(message.text)
                prod = next((p for p in DB_STATE["products"] if p["id"] == p_id), None)
                if prod:
                    prod["position"] = new_pos
                    save_db()
            except ValueError:
                pass
            user_states.pop(user_id, None)
            show_main_admin_menu(ADMIN_ID)
            return

        elif state == "WAITING_CUSTOM_BROADCAST":
            user_states.pop(user_id, None)
            update_admin_panel(ADMIN_ID, "🚀 Broadcasting message to all users... Please wait.", None)
            success_count = 0
            fail_count = 0
            for u_id in DB_STATE.get("users", []):
                if u_id in DB_STATE.get("blocked_users", []): continue
                try:
                    bot.copy_message(chat_id=u_id, from_chat_id=ADMIN_ID, message_id=message.message_id)
                    success_count += 1
                except: fail_count += 1
            
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton("🔙 Back to Main Menu", callback_data="adm_back_panel"))
            update_admin_panel(ADMIN_ID, f"✅ **Broadcast Completed!**\n\n- Successfully sent: {success_count}\n- Failed: {fail_count}", markup)
            return

        elif state == "ADM_SET_WELCOME" and message.text:
            DB_STATE["welcome_msg"] = message.text
            save_db()
            show_main_admin_menu(ADMIN_ID)
            return

        elif state == "ADM_SET_HOW_VID" and message.content_type == 'video':
            DB_STATE["how_to_use_video"] = message.video.file_id
            save_db()
            show_main_admin_menu(ADMIN_ID)
            return

        elif state == "ADM_SET_PAY_PHOTO" and message.content_type == 'photo':
            DB_STATE["payment_photo"] = message.photo[-1].file_id
            save_db()
            show_main_admin_menu(ADMIN_ID)
            return

        elif state == "ADM_SET_PAY_MSG_TEXT" and message.text:
            DB_STATE["payment_msg"] = message.text
            save_db()
            show_main_admin_menu(ADMIN_ID)
            return

        elif state == "ADM_SET_BC_TEXT" and message.text:
            DB_STATE["broadcast_msg"] = message.text
            save_db()
            show_main_admin_menu(ADMIN_ID)
            return

        elif state == "ADM_SET_BC_TIME" and message.text:
            try:
                mins = int(message.text)
                DB_STATE["broadcast_minutes"] = mins
                save_db()
                reset_broadcast_job()
            except ValueError:
                pass
            show_main_admin_menu(ADMIN_ID)
            return

        elif state == "ADM_ADD_PROD_NAME" and message.text:
            new_id = str(len(DB_STATE["products"]) + 1)
            new_pos = len(DB_STATE["products"]) + 1
            DB_STATE["products"].append({
                "id": new_id, 
                "name": message.text, 
                "desc": "Product details", 
                "videos": [], 
                "link": "https://example.com",
                "position": new_pos,
                "pay_msg": "" 
            })
            save_db()
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton("🔙 Cancel & Back", callback_data="adm_prod_menu"))
            update_admin_panel(ADMIN_ID, f"✅ Button `{message.text}` created!\n\n🔗 **Now send the Product Link to deliver after payment:**", markup)
            user_states[user_id] = f"ADM_ADD_PROD_LINK_{new_id}"
            return

        elif state.startswith("ADM_ADD_PROD_LINK_") and message.text:
            p_id = state.replace("ADM_ADD_PROD_LINK_", "")
            prod = next((p for p in DB_STATE["products"] if p["id"] == p_id), None)
            if prod:
                prod["link"] = message.text
                save_db()
            user_states[user_id] = f"ADM_ADD_PROD_DESC_{p_id}"
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton("🔙 Cancel & Back", callback_data="adm_prod_menu"))
            update_admin_panel(ADMIN_ID, f"✅ Link saved!\n\n✍️ **Now send the Product Details / Description text:**", markup)
            return

        elif state.startswith("ADM_ADD_PROD_DESC_") and message.text:
            p_id = state.replace("ADM_ADD_PROD_DESC_", "")
            prod = next((p for p in DB_STATE["products"] if p["id"] == p_id), None)
            if prod:
                prod["desc"] = message.text
                save_db()
            show_main_admin_menu(ADMIN_ID)
            return

    # User Inputs
    if state == "WAITING_REPORT":
        user_states.pop(user_id, None)
        bot.send_message(user_id, "✅ Your report has been sent to admin.")
        bot.send_message(ADMIN_ID, f"📩 **Report from @{message.from_user.username} (`{user_id}`):**\n\n{message.text}", parse_mode="Markdown")

    elif state.startswith("WAITING_SCREENSHOT_"):
        prod_id = state.replace("WAITING_SCREENSHOT_", "")
        if message.content_type == 'photo':
            user_states.pop(user_id, None)
            bot.send_message(user_id, "⏳𝗖𝗵𝗲𝗰𝗸𝗶𝗻𝗴 𝘆𝗼𝘂𝗿 𝗽𝗮𝘆𝗺𝗲𝗻𝘁....   𝗣𝗹𝗲𝗮𝘀𝗲 𝘄𝗮𝗶𝘁 𝟱-𝟭𝟬 𝗺𝗶𝗻. ")

            photo_id = message.photo[-1].file_id
            adm_markup = InlineKeyboardMarkup()
            adm_markup.row(
                InlineKeyboardButton("CONFIRM ✅", callback_data=f"adm_confirm_{prod_id}_{user_id}"),
                InlineKeyboardButton("REJECT ❌", callback_data=f"adm_reject_{user_id}"),
                InlineKeyboardButton("BLOCK 🚫", callback_data=f"adm_block_{user_id}")
            )
            
            # ⚡ FIX 2: GETTING PRODUCT NAME FOR ADMIN NOTIFICATION ⚡
            prod = next((p for p in DB_STATE.get("products", []) if p["id"] == prod_id), None)
            prod_name = prod["name"] if prod else "Unknown Product"

            username = message.from_user.username
            user_tag = f"@{username}" if username else "No Username"
            user_name = message.from_user.first_name or "User"

            try:
                bot.send_photo(
                    ADMIN_ID, 
                    photo_id, 
                    caption=f"📸 **New Payment Screenshot!**\n\n🛍️ **Product:** {prod_name}\n👤 **User:** {user_tag}\n📛 **Name:** {user_name}\n🆔 **ID:** `{user_id}`", 
                    reply_markup=adm_markup,
                    parse_mode="Markdown"
                )
            except Exception as e:
                print(f"Error sending to admin: {e}")

# ==========================================
# 🌐 FLASK KEEP-ALIVE SERVER
# ==========================================
@app.route('/')
def home():
    return "Bot is running on Render!"

def run_bot():
    bot.infinity_polling()

if __name__ == "__main__":
    threading.Thread(target=run_bot).start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
