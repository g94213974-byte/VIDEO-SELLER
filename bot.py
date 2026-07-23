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
    "start_videos": [], # List of video file_ids
    "how_to_use_video": "",
    "payment_photo": "",
    "payment_msg": "💳 **Payment Instructions**\n\nPlease scan the QR and pay, then click 'I have paid'.",
    "reject_msg": "❌ Your payment couldn't be verified. Please try again.",
    "broadcast_msg": "",
    "broadcast_minutes": 3, # Interval in minutes
    "layout_style": "vertical", # 'vertical' (=) or 'horizontal' (--)
    "products": [], # [{"id": "1", "name": "Plan 1", "desc": "Details", "videos": ["vid1"], "link": "https://..."}]
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

# --- HELPER FUNCTION: SEND VIDEOS AS ALBUM GRID ---
def send_videos_as_album(chat_id, video_list):
    if not video_list:
        return
    if len(video_list) == 1:
        try:
            bot.send_video(chat_id, video_list[0])
        except Exception as e:
            print(f"Error sending video: {e}")
    else:
        # Send videos grouped together (Max 10 per album batch)
        for i in range(0, len(video_list), 10):
            chunk = video_list[i:i+10]
            media_group = [InputMediaVideo(v) for v in chunk]
            try:
                bot.send_media_group(chat_id, media_group)
            except Exception as e:
                print(f"Error sending media group, sending individually: {e}")
                for v in chunk:
                    try:
                        bot.send_video(chat_id, v)
                    except Exception:
                        pass

# ==========================================
# 🔄 AUTO BROADCAST SYSTEM
# ==========================================
def send_auto_broadcast():
    msg_text = DB_STATE.get("broadcast_msg", "")
    if not msg_text:
        return
    for u_id in DB_STATE.get("users", []):
        if u_id in DB_STATE.get("blocked_users", []):
            continue
        try:
            bot.send_message(u_id, msg_text)
        except Exception:
            pass

scheduler = BackgroundScheduler()

def reset_broadcast_job():
    try:
        scheduler.remove_job('bc_job')
    except Exception:
        pass
    mins = int(DB_STATE.get("broadcast_minutes", 3))
    if mins < 1: mins = 1
    scheduler.add_job(send_auto_broadcast, 'interval', minutes=mins, id='bc_job')

scheduler.start()
reset_broadcast_job()

# ==========================================
# 🚀 USER SIDE LOGIC
# ==========================================
@bot.message_handler(commands=['start'])
def start_command(message):
    user_id = message.chat.id
    name = message.from_user.first_name

    if user_id not in DB_STATE["users"]:
        DB_STATE["users"].append(user_id)
        save_db()

    # Send All Start Videos as Album
    start_vids = DB_STATE.get("start_videos", [])
    if start_vids:
        send_videos_as_album(user_id, start_vids)

    welcome_text = DB_STATE["welcome_msg"].format(name=name)
    markup = InlineKeyboardMarkup()

    # Dynamic Buttons Layout
    products = DB_STATE.get("products", [])
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
    else: # Vertical
        for p in products:
            markup.row(InlineKeyboardButton(p["name"], callback_data=f"prod_{p['id']}"))

    # Bottom Fixed Buttons
    markup.row(
        InlineKeyboardButton("How to use ❓", callback_data="how_to_use"),
        InlineKeyboardButton("Report Issue 📩", callback_data="report_issue")
    )

    bot.send_message(user_id, welcome_text, reply_markup=markup, parse_mode="Markdown")

# ==========================================
# 🛠️ ADMIN CONTROL PANEL
# ==========================================
@bot.message_handler(commands=['admin'])
def admin_menu(message):
    if message.chat.id == ADMIN_ID:
        send_admin_panel(message.chat.id)

def send_admin_panel(chat_id):
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("🎬 Manage Start Videos", callback_data="adm_start_vids_menu"))
    markup.row(InlineKeyboardButton("📦 Manage Product Buttons & Videos", callback_data="adm_prod_menu"))
    markup.row(InlineKeyboardButton("✏️ Edit Welcome Text", callback_data="adm_edit_welcome"))
    
    current_layout = DB_STATE.get("layout_style", "vertical")
    layout_icon = "↕️ Vertical (=)" if current_layout == "vertical" else "↔️ Horizontal (--)"
    markup.row(InlineKeyboardButton(f"Change Layout: {layout_icon}", callback_data="adm_toggle_layout"))
    
    markup.row(InlineKeyboardButton("🎥 Set How To Use Video", callback_data="adm_set_how_vid"))
    markup.row(InlineKeyboardButton("💳 Set Payment QR / Photo", callback_data="adm_set_pay_photo"))
    
    bc_mins = DB_STATE.get("broadcast_minutes", 3)
    markup.row(InlineKeyboardButton("📢 Set Broadcast Text", callback_data="adm_set_bc_text"))
    markup.row(InlineKeyboardButton(f"⏱️ Set Broadcast Time ({bc_mins} min)", callback_data="adm_set_bc_time"))
    
    blocked_count = len(DB_STATE.get("blocked_users", []))
    markup.row(InlineKeyboardButton(f"🚫 Unblock Users ({blocked_count})", callback_data="adm_unblock_menu"))

    bot.send_message(chat_id, "⚙️ **Admin Customization Panel**\nChoose an option to configure:", reply_markup=markup, parse_mode="Markdown")

# ==========================================
# 🔄 CALLBACK QUERY HANDLER
# ==========================================
@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    user_id = call.message.chat.id
    data = call.data

    # --- USER CALLBACKS ---
    if data == "back_home":
        bot.delete_message(user_id, call.message.message_id)
        start_command(call.message)

    elif data == "how_to_use":
        vid = DB_STATE.get("how_to_use_video", "")
        if vid:
            bot.send_video(user_id, vid, caption="🎥 Here is how to use the bot!")
        else:
            bot.send_message(user_id, "ℹ️ Instructions video not set yet.")

    elif data == "report_issue":
        bot.send_message(user_id, "📝 Please type your issue below. Admin will reply soon:")
        user_states[user_id] = "WAITING_REPORT"

    elif data.startswith("prod_"):
        prod_id = data.split("_")[1]
        prod = next((p for p in DB_STATE["products"] if p["id"] == prod_id), None)
        if prod:
            # Send all videos assigned to this button as an ALBUM GRID
            p_videos = prod.get("videos", [])
            if p_videos:
                send_videos_as_album(user_id, p_videos)

            caption = f"📌 **{prod['name']}**\n\n{prod.get('desc', '')}"
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton("Buy Now 🛒", callback_data=f"buynow_{prod_id}"))
            markup.row(InlineKeyboardButton("Back 🔙", callback_data="back_home"))
            bot.send_message(user_id, caption, reply_markup=markup, parse_mode="Markdown")

    elif data.startswith("buynow_"):
        prod_id = data.split("_")[1]
        markup = InlineKeyboardMarkup()
        markup.row(InlineKeyboardButton("I have paid ✅", callback_data=f"paid_{prod_id}"))
        markup.row(InlineKeyboardButton("Cancel ❌", callback_data="back_home"))

        pay_msg = DB_STATE.get("payment_msg", "Please pay and submit screenshot.")
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
        # Start Videos Management
        if data == "adm_start_vids_menu":
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton("➕ Add Start Video", callback_data="adm_add_start_vid"))
            markup.row(InlineKeyboardButton("🗑️ Manage / Delete Start Videos", callback_data="adm_del_start_vid_list"))
            markup.row(InlineKeyboardButton("🔙 Back", callback_data="adm_back_panel"))
            v_count = len(DB_STATE.get("start_videos", []))
            bot.send_message(ADMIN_ID, f"🎬 **Start Videos Management**\nTotal Videos: {v_count}", reply_markup=markup)

        elif data == "adm_add_start_vid":
            bot.send_message(ADMIN_ID, "Please upload/send a Video to add to Start Videos:")
            user_states[ADMIN_ID] = "ADM_ADD_START_VID"

        elif data == "adm_del_start_vid_list":
            markup = InlineKeyboardMarkup()
            vids = DB_STATE.get("start_videos", [])
            for idx, v_id in enumerate(vids):
                markup.row(
                    InlineKeyboardButton(f"👀 See Vid {idx+1}", callback_data=f"sv_see_{idx}"),
                    InlineKeyboardButton(f"❌ Delete Vid {idx+1}", callback_data=f"sv_del_{idx}")
                )
            if vids:
                markup.row(InlineKeyboardButton("💥 Delete All Start Videos", callback_data="sv_del_all"))
            markup.row(InlineKeyboardButton("🔙 Back", callback_data="adm_start_vids_menu"))
            bot.send_message(ADMIN_ID, "Select an action for Start Videos:", reply_markup=markup)

        elif data.startswith("sv_see_"):
            idx = int(data.replace("sv_see_", ""))
            vids = DB_STATE.get("start_videos", [])
            if 0 <= idx < len(vids):
                bot.send_video(ADMIN_ID, vids[idx], caption=f"🎥 Start Video {idx+1}")

        elif data.startswith("sv_del_"):
            if data == "sv_del_all":
                DB_STATE["start_videos"] = []
                save_db()
                bot.answer_callback_query(call.id, "All Start Videos Deleted!")
            else:
                idx = int(data.replace("sv_del_", ""))
                vids = DB_STATE.get("start_videos", [])
                if 0 <= idx < len(vids):
                    DB_STATE["start_videos"].pop(idx)
                    save_db()
                    bot.answer_callback_query(call.id, "Video Deleted!")
            send_admin_panel(ADMIN_ID)

        # Product Buttons & Videos Management
        elif data == "adm_prod_menu":
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton("➕ Add New Button", callback_data="adm_add_prod"))
            markup.row(InlineKeyboardButton("🎥 Add Video to a Button", callback_data="adm_prod_add_vid_list"))
            markup.row(InlineKeyboardButton("🗑️ Manage / Delete Videos of Button", callback_data="adm_prod_del_vid_list"))
            markup.row(InlineKeyboardButton("❌ Delete Button Completely", callback_data="adm_del_prod_list"))
            markup.row(InlineKeyboardButton("🔙 Back", callback_data="adm_back_panel"))
            bot.send_message(ADMIN_ID, "📦 **Product Button Management**", reply_markup=markup)

        elif data == "adm_add_prod":
            bot.send_message(ADMIN_ID, "Enter New Button Name (e.g., Plan A):")
            user_states[ADMIN_ID] = "ADM_ADD_PROD_NAME"

        elif data == "adm_prod_add_vid_list":
            markup = InlineKeyboardMarkup()
            for p in DB_STATE.get("products", []):
                markup.row(InlineKeyboardButton(f"🎥 Add Video to: {p['name']}", callback_data=f"adm_p_addvid_{p['id']}"))
            bot.send_message(ADMIN_ID, "Select a button to add video to:", reply_markup=markup)

        elif data.startswith("adm_p_addvid_"):
            p_id = data.replace("adm_p_addvid_", "")
            bot.send_message(ADMIN_ID, "Please send/upload a Video for this button:")
            user_states[ADMIN_ID] = f"ADM_UPL_PROD_VID_{p_id}"

        elif data == "adm_prod_del_vid_list":
            markup = InlineKeyboardMarkup()
            for p in DB_STATE.get("products", []):
                v_count = len(p.get("videos", []))
                markup.row(InlineKeyboardButton(f"⚙️ Manage Videos ({v_count}): {p['name']}", callback_data=f"adm_p_mngv_{p['id']}"))
            bot.send_message(ADMIN_ID, "Select a button to view/delete its videos:", reply_markup=markup)

        elif data.startswith("adm_p_mngv_"):
            p_id = data.replace("adm_p_mngv_", "")
            prod = next((p for p in DB_STATE["products"] if p["id"] == p_id), None)
            if prod:
                markup = InlineKeyboardMarkup()
                vids = prod.get("videos", [])
                for idx, v_id in enumerate(vids):
                    markup.row(
                        InlineKeyboardButton(f"👀 See Vid {idx+1}", callback_data=f"pv_see_{p_id}_{idx}"),
                        InlineKeyboardButton(f"❌ Delete Vid {idx+1}", callback_data=f"pv_del_{p_id}_{idx}")
                    )
                if vids:
                    markup.row(InlineKeyboardButton("💥 Delete All Videos", callback_data=f"pv_dall_{p_id}"))
                markup.row(InlineKeyboardButton("🔙 Back", callback_data="adm_prod_menu"))
                bot.send_message(ADMIN_ID, f"🎥 **Manage videos for button '{prod['name']}'**:", reply_markup=markup)

        elif data.startswith("pv_see_"):
            _, _, p_id, idx_str = data.split("_")
            idx = int(idx_str)
            prod = next((p for p in DB_STATE["products"] if p["id"] == p_id), None)
            if prod and "videos" in prod and 0 <= idx < len(prod["videos"]):
                bot.send_video(ADMIN_ID, prod["videos"][idx], caption=f"🎥 Video {idx+1} of '{prod['name']}'")

        elif data.startswith("pv_del_"):
            _, _, p_id, idx_str = data.split("_")
            idx = int(idx_str)
            prod = next((p for p in DB_STATE["products"] if p["id"] == p_id), None)
            if prod and "videos" in prod and 0 <= idx < len(prod["videos"]):
                prod["videos"].pop(idx)
                save_db()
                bot.answer_callback_query(call.id, "Video Deleted!")
            send_admin_panel(ADMIN_ID)

        elif data.startswith("pv_dall_"):
            p_id = data.replace("pv_dall_", "")
            prod = next((p for p in DB_STATE["products"] if p["id"] == p_id), None)
            if prod:
                prod["videos"] = []
                save_db()
                bot.answer_callback_query(call.id, "All Videos Deleted!")
            send_admin_panel(ADMIN_ID)

        elif data == "adm_del_prod_list":
            markup = InlineKeyboardMarkup()
            for p in DB_STATE.get("products", []):
                markup.row(InlineKeyboardButton(f"❌ Delete Button: {p['name']}", callback_data=f"adm_del_p_{p['id']}"))
            bot.send_message(ADMIN_ID, "Click a button to delete it completely:", reply_markup=markup)

        elif data.startswith("adm_del_p_"):
            p_id = data.replace("adm_del_p_", "")
            DB_STATE["products"] = [p for p in DB_STATE["products"] if p["id"] != p_id]
            save_db()
            bot.answer_callback_query(call.id, "Button Deleted!")
            send_admin_panel(ADMIN_ID)

        elif data == "adm_edit_welcome":
            bot.send_message(ADMIN_ID, "Send new Welcome Text.\nUse `{name}` for user name variable:")
            user_states[ADMIN_ID] = "ADM_SET_WELCOME"

        elif data == "adm_toggle_layout":
            curr = DB_STATE.get("layout_style", "vertical")
            DB_STATE["layout_style"] = "horizontal" if curr == "vertical" else "vertical"
            save_db()
            bot.answer_callback_query(call.id, "Layout Updated!")
            send_admin_panel(ADMIN_ID)

        elif data == "adm_set_how_vid":
            bot.send_message(ADMIN_ID, "Please send/upload 'How To Use' Video:")
            user_states[ADMIN_ID] = "ADM_SET_HOW_VID"

        elif data == "adm_set_pay_photo":
            bot.send_message(ADMIN_ID, "Please send Payment QR Code Photo:")
            user_states[ADMIN_ID] = "ADM_SET_PAY_PHOTO"

        elif data == "adm_set_bc_text":
            bot.send_message(ADMIN_ID, "Enter text for Auto-Broadcast message:")
            user_states[ADMIN_ID] = "ADM_SET_BC_TEXT"

        elif data == "adm_set_bc_time":
            bot.send_message(ADMIN_ID, "Enter Auto-Broadcast interval in MINUTES (e.g. 3, 5, 10):")
            user_states[ADMIN_ID] = "ADM_SET_BC_TIME"

        elif data == "adm_unblock_menu":
            markup = InlineKeyboardMarkup()
            blocked_users = DB_STATE.get("blocked_users", [])
            for b_id in blocked_users:
                markup.row(InlineKeyboardButton(f"🔓 Unblock ID: {b_id}", callback_data=f"adm_unblock_exec_{b_id}"))
            markup.row(InlineKeyboardButton("🔙 Back", callback_data="adm_back_panel"))
            bot.send_message(ADMIN_ID, "Select a user to unblock:", reply_markup=markup)

        elif data.startswith("adm_unblock_exec_"):
            b_id = int(data.replace("adm_unblock_exec_", ""))
            if b_id in DB_STATE.get("blocked_users", []):
                DB_STATE["blocked_users"].remove(b_id)
                save_db()
                bot.answer_callback_query(call.id, "User Unblocked!")
            send_admin_panel(ADMIN_ID)

        elif data == "adm_back_panel":
            send_admin_panel(ADMIN_ID)

        elif data.startswith("adm_confirm_"):
            _, _, prod_id, target_user = data.split("_")
            prod = next((p for p in DB_STATE["products"] if p["id"] == prod_id), None)
            link = prod.get("link", "No link") if prod else "No link"
            bot.send_message(int(target_user), f"✅ **Payment Confirmed!**\n\nLink:\n🔗 {link}", parse_mode="Markdown")
            bot.answer_callback_query(call.id, "Link sent to user!")

        elif data.startswith("adm_reject_"):
            target_user = data.split("_")[2]
            bot.send_message(int(target_user), DB_STATE.get("reject_msg", "Payment Rejected."))
            bot.answer_callback_query(call.id, "Rejection sent.")

        elif data.startswith("adm_block_"):
            target_user = int(data.split("_")[2])
            if target_user not in DB_STATE["blocked_users"]:
                DB_STATE["blocked_users"].append(target_user)
                save_db()
            bot.answer_callback_query(call.id, "User Blocked.")

# ==========================================
# 📩 INPUT & MEDIA HANDLER
# ==========================================
@bot.message_handler(content_types=['photo', 'video', 'text'])
def handle_all_inputs(message):
    user_id = message.chat.id

    if user_id in DB_STATE.get("blocked_users", []):
        return

    state = user_states.get(user_id, "")

    # Admin Inputs
    if user_id == ADMIN_ID:
        if state == "ADM_ADD_START_VID" and message.content_type == 'video':
            if "start_videos" not in DB_STATE:
                DB_STATE["start_videos"] = []
            DB_STATE["start_videos"].append(message.video.file_id)
            save_db()
            user_states.pop(user_id, None)
            bot.send_message(ADMIN_ID, "✅ New Start Video added!")

        elif state.startswith("ADM_UPL_PROD_VID_") and message.content_type == 'video':
            p_id = state.replace("ADM_UPL_PROD_VID_", "")
            prod = next((p for p in DB_STATE["products"] if p["id"] == p_id), None)
            if prod:
                if "videos" not in prod:
                    prod["videos"] = []
                prod["videos"].append(message.video.file_id)
                save_db()
                bot.send_message(ADMIN_ID, f"✅ Video added to button '{prod['name']}'!")
            user_states.pop(user_id, None)

        elif state == "ADM_SET_WELCOME" and message.text:
            DB_STATE["welcome_msg"] = message.text
            save_db()
            user_states.pop(user_id, None)
            bot.send_message(ADMIN_ID, "✅ Welcome Text updated!")

        elif state == "ADM_SET_HOW_VID" and message.content_type == 'video':
            DB_STATE["how_to_use_video"] = message.video.file_id
            save_db()
            user_states.pop(user_id, None)
            bot.send_message(ADMIN_ID, "✅ How-To-Use Video saved!")

        elif state == "ADM_SET_PAY_PHOTO" and message.content_type == 'photo':
            DB_STATE["payment_photo"] = message.photo[-1].file_id
            save_db()
            user_states.pop(user_id, None)
            bot.send_message(ADMIN_ID, "✅ Payment QR photo saved!")

        elif state == "ADM_SET_BC_TEXT" and message.text:
            DB_STATE["broadcast_msg"] = message.text
            save_db()
            user_states.pop(user_id, None)
            bot.send_message(ADMIN_ID, "✅ Auto Broadcast message saved!")

        elif state == "ADM_SET_BC_TIME" and message.text:
            try:
                mins = int(message.text)
                DB_STATE["broadcast_minutes"] = mins
                save_db()
                reset_broadcast_job()
                bot.send_message(ADMIN_ID, f"✅ Auto-Broadcast interval updated to {mins} Minutes!")
            except ValueError:
                bot.send_message(ADMIN_ID, "❌ Invalid number! Please enter a valid integer for minutes.")
            user_states.pop(user_id, None)

        elif state == "ADM_ADD_PROD_NAME" and message.text:
            new_id = str(len(DB_STATE["products"]) + 1)
            DB_STATE["products"].append({"id": new_id, "name": message.text, "desc": "Product details", "videos": [], "link": "https://example.com"})
            save_db()
            bot.send_message(ADMIN_ID, "Enter Product Link to deliver after payment:")
            user_states[user_id] = f"ADM_ADD_PROD_LINK_{new_id}"

        elif state.startswith("ADM_ADD_PROD_LINK_") and message.text:
            p_id = state.replace("ADM_ADD_PROD_LINK_", "")
            prod = next((p for p in DB_STATE["products"] if p["id"] == p_id), None)
            if prod:
                prod["link"] = message.text
                save_db()
            user_states.pop(user_id, None)
            bot.send_message(ADMIN_ID, "✅ New Button & Link added successfully!")

    # User Inputs
    if state == "WAITING_REPORT":
        user_states.pop(user_id, None)
        bot.send_message(user_id, "✅ Your report has been sent to admin.")
        bot.send_message(ADMIN_ID, f"📩 **Report from @{message.from_user.username} (`{user_id}`):**\n\n{message.text}", parse_mode="Markdown")

    elif state.startswith("WAITING_SCREENSHOT_"):
        prod_id = state.replace("WAITING_SCREENSHOT_", "")
        if message.content_type == 'photo':
            user_states.pop(user_id, None)
            bot.send_message(user_id, "⏳ Checking your payment... This may take a moment.")

            photo_id = message.photo[-1].file_id
            adm_markup = InlineKeyboardMarkup()
            adm_markup.row(
                InlineKeyboardButton("CONFIRM ✅", callback_data=f"adm_confirm_{prod_id}_{user_id}"),
                InlineKeyboardButton("REJECT ❌", callback_data=f"adm_reject_{user_id}"),
                InlineKeyboardButton("BLOCK 🚫", callback_data=f"adm_block_{user_id}")
            )
            
            username = message.from_user.username
            user_tag = f"@{username}" if username else "No Username"
            user_name = message.from_user.first_name or "User"

            try:
                bot.send_photo(
                    ADMIN_ID, 
                    photo_id, 
                    caption=f"📸 New Payment Screenshot Received!\n\nUser: {user_tag}\nName: {user_name}\nID: {user_id}", 
                    reply_markup=adm_markup
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
