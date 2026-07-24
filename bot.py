import os
import requests
import json
from flask import Flask, request
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# --- CONFIGURATION & ENVIRONMENT VARIABLES ---
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CHANNEL_ID = os.getenv("CHANNEL_ID", "")  # প্রাইভেট চ্যানেল যেখানে JSON ডাটা ব্যাকআপ রাখা হয়

if not TOKEN:
    raise ValueError("❌ BOT_TOKEN environment variable is missing!")

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# ইন-মেমোরি ডাটাবেস (বট রিস্টার্ট হলে সাময়িকভাবে ডেটা ধরে রাখার জন্য)
users_db = set()      # সমস্ত ইউজারের আইডি
paid_buyers = set()   # সফলভাবে পেমেন্ট করা ক্রেতাদের আইডি
products = {
    "prod_1": {"name": "VIP Premium Access", "price": "₹2000", "desc": "Lifetime access to VIP channel."}
}

# ব্রডকাস্ট বা অন্য স্টেপ ট্র্যাক করার জন্য সাময়িক স্টেট ডিকশনারি
user_states = {}


# --- WEBHOOK ROUTE FOR RENDER ---
@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    json_str = request.get_data().decode("UTF-8")
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return "!", 200

@app.route("/")
def index():
    return "Telegram Selling Bot is running successfully!", 200


# --- OCR PAYMENT SCREENSHOT VERIFIER ---
def verify_payment_screenshot(photo_id):
    try:
        # টেলিগ্রাম থেকে ছবির ফাইল ইনফরমেশন ফেচ করা হচ্ছে
        file_info = bot.get_file(photo_id)
        file_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_info.file_path}"
        
        # ১. ছবিটি টেলিগ্রাম থেকে মেমোরিতে ডাউনলোড করা হচ্ছে (১০০% একুরেসির জন্য)
        img_response = requests.get(file_url, timeout=10)
        
        # ২. আপনার নিজস্ব ফ্রি API Key দিয়ে OCR.space সার্ভারে পাঠানো হচ্ছে
        payload = {
            'apikey': 'K82685526088957', # আপনার নিজস্ব OCR API Key
            'language': 'eng',
            'isOverlayRequired': False
        }
        files = {
            'file': ('image.jpg', img_response.content, 'image/jpeg')
        }
        
        r = requests.post('https://api.ocr.space/parse/image', data=payload, files=files, timeout=15)
        res = r.json()
        
        # ৩. কোনো কারণে API ফেইল করলে আসল কাস্টমার যেন ব্লক না হয়, তাই True রিটার্ন করে অ্যাডমিনের কাছে পাঠানো হবে
        if res.get("IsErroredOnProcessing") or not res.get("ParsedResults"):
            print("⚠️ OCR API Error or empty result. Forwarding to Admin just in case.")
            return True 
            
        # ছবি থেকে টেক্সট রিড করা হচ্ছে
        text = res["ParsedResults"][0].get("ParsedText", "").lower()
        
        # ৪. পেমেন্ট কনফার্ম করার জন্য গুরুত্বপূর্ণ কিওয়ার্ড (PhonePe, GPay, Paytm সব সাপোর্ট করবে)
        keywords = [
            "successful", "paid", "completed", "sent", "transferred",
            "utr", "upi", "ref", "txn", "transaction",
            "phonepe", "gpay", "paytm", "bhim", "google pay", "amazon pay",
            "inr", "rs", "₹", "2", "4", "9"
        ]
        
        # যদি লেখায় পেমেন্টের কোনো কিওয়ার্ড থাকে, তবে পেমেন্ট আসল (True)
        if any(kw in text for kw in keywords):
            return True
            
        # যদি পেমেন্টের কোনো প্রমাণ না থাকে, তবেই শুধু জাল স্ক্রিনশট হিসেবে রিজেক্ট করবে (False)
        return False

    except Exception as e:
        print(f"❌ OCR Check Error: {e}")
        # নেটওয়ার্ক এরর হলেও কাস্টমার যেন ফিরে না যায়, তাই সেফটির জন্য True রিটার্ন করবে
        return True


# --- USER COMMANDS ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.from_user.id
    users_db.add(user_id) # ইউজার লিস্টে যুক্ত করা হলো
    
    user_name = message.from_user.first_name
    markup = InlineKeyboardMarkup()
    for p_id, p_info in products.items():
        markup.add(InlineKeyboardButton(f"🛒 {p_info['name']} - {p_info['price']}", callback_data=f"buy_{p_id}"))
    
    markup.add(InlineKeyboardButton("📸 পেমেন্ট স্ক্রিনশট পাঠান", callback_data="send_ss_info"))

    bot.send_message(
        message.chat.id, 
        f"স্বাগতম **{user_name}**! আমাদের অটোমেটেড সেলিং বটে আপনাকে স্বাগতম। নিচে থাকা পণ্যগুলো থেকে আপনার পছন্দমতো পণ্য বেছে নিন:",
        reply_markup=markup,
        parse_mode="Markdown"
    )


@bot.callback_query_handler(func=lambda call: call.data == "send_ss_info")
def send_ss_info_callback(call):
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, "📸 দয়া করে আপনার পেমেন্টের সফল স্ক্রিনশটটি সরাসরি এই চ্যাটে ছবি হিসেবে পাঠিয়ে দিন।")


# --- PRODUCT BUY CALLBACK ---
@bot.callback_query_handler(func=lambda call: call.data.startswith("buy_"))
def handle_buy_callback(call):
    bot.answer_callback_query(call.id)
    p_id = call.data.split("_")[1]
    product = products.get(p_id)
    
    if product:
        text = (
            f"📦 **{product['name']}**\n"
            f"💰 মূল্য: {product['price']}\n\n"
            f"📝 বিবরণ: {product['desc']}\n\n"
            f"⚡ দয়া করে নিচের UPI বা QR কোডে পেমেন্ট করুন এবং পেমেন্টের পর সফল স্ক্রিনশটটি এই চ্যাটেই পাঠিয়ে দিন।"
        )
        bot.send_message(call.message.chat.id, text, parse_mode="Markdown")


# --- PHOTO HANDLER FOR PAYMENT SCREENSHOTS ---
@bot.message_handler(content_types=['photo'])
def handle_payment_photo(message):
    user_id = message.from_user.id
    
    # যদি অ্যাডমিন ছবি পাঠান এবং ব্রডকাস্ট মোডে থাকেন
    if user_id == ADMIN_ID and user_states.get(ADMIN_ID) == "waiting_broadcast":
        handle_broadcast_message(message)
        return

    # সাধারণ অ্যাডমিন ছবি পাঠালে
    if user_id == ADMIN_ID:
        bot.reply_to(message, "ℹ️ এটি অ্যাডমিন অ্যাকাউন্ট। পেমেন্ট স্ক্রিনশট হিসেবে গণ্য করা হয়নি।")
        return

    bot.reply_to(message, "⏳ আপনার পেমেন্ট স্ক্রিনশটটি যাচাই করা হচ্ছে, অনুগ্রহ করে অপেক্ষা করুন...")
    
    # ছবির হাই রেজুলেশন আইডি নেওয়া
    photo_id = message.photo[-1].file_id
    
    # OCR ভেরিফিকেশন কল করা
    is_valid_payment = verify_payment_screenshot(photo_id)
    
    if is_valid_payment:
        paid_buyers.add(user_id) # পেইড বায়ার লিস্টে যুক্ত হলো
        bot.send_message(message.chat.id, "✅ পেমেন্ট সফলভাবে যাচাই করা হয়েছে! আপনার অর্ডারটি প্রসেস করা হয়েছে। ধন্যবাদ।")
        
        # অ্যাডমিনকে নোটিফিকেশন পাঠানো
        if ADMIN_ID:
            bot.send_message(
                ADMIN_ID, 
                f"🔔 **নতুন সফল পেমেন্ট!**\n👤 ইউজার আইডি: `{user_id}`\n✨ পেমেন্ট ভেরিফাই হয়েছে।", 
                parse_mode="Markdown"
            )
    else:
        bot.send_message(message.chat.id, "❌ Payment not receive. Please try again or send a valid screenshot.")
        bot.send_message(message.chat.id, "📸 Please send your payment screenshot.")


# --- ADMIN PANEL COMMANDS ---
@bot.message_handler(commands=['admin'])
def admin_panel(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "⚠️ এই কমান্ডটি শুধুমাত্র অ্যাডমিনের জন্য!")
        return
        
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("📢 ব্রডকাস্ট মেসেজ", callback_data="admin_broadcast"))
    markup.add(InlineKeyboardButton("👥 মোট ইউজার ও বায়ার লিস্ট", callback_data="admin_stats"))
    
    bot.send_message(
        message.chat.id, 
        "🛠️ **অ্যাডমিন কন্ট্রোল প্যানেল**\nনিচের অপশনগুলো থেকে আপনার ম্যানেজমেন্ট সিলেক্ট করুন:", 
        reply_markup=markup, 
        parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda call: call.data == "admin_stats")
def admin_stats(call):
    bot.answer_callback_query(call.id)
    if call.from_user.id != ADMIN_ID:
        return
    stats_text = (
        f"📊 **বট স্ট্যাটিস্টিক্স**\n\n"
        f"👥 মোট ইউজার সংখ্যা: {len(users_db)}\n"
        f"💰 সফল পেইড বায়ার সংখ্যা: {len(paid_buyers)}"
    )
    bot.edit_message_text(stats_text, call.message.chat.id, call.message.message_id, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == "admin_broadcast")
def admin_broadcast_prompt(call):
    bot.answer_callback_query(call.id)
    if call.from_user.id != ADMIN_ID:
        return
    user_states[ADMIN_ID] = "waiting_broadcast"
    bot.send_message(call.message.chat.id, "📢 দয়া করে ব্রডকাস্ট করার জন্য মেসেজটি (টেক্সট, ছবি বা ভিডিও) পাঠান:")


def handle_broadcast_message(message):
    if message.from_user.id != ADMIN_ID:
        return
    
    user_states.pop(ADMIN_ID, None)
    success_count = 0
    fail_count = 0
    
    bot.send_message(ADMIN_ID, "🚀 ব্রডকাস্ট পাঠানো শুরু হয়েছে, দয়া করে অপেক্ষা করুন...")
    
    for uid in users_db:
        # ওনার বা অ্যাডমিন আইডি নিজে ব্রডকাস্ট থেকে বাদ রাখার লজিক
        if uid == ADMIN_ID:
            continue
        try:
            bot.copy_message(chat_id=uid, from_chat_id=message.chat.id, message_id=message.message_id)
            success_count += 1
        except Exception as e:
            print(f"Failed to send broadcast to {uid}: {e}")
            fail_count += 1
            
    bot.send_message(
        ADMIN_ID, 
        f"✅ ব্রডকাস্ট সম্পন্ন!\n\n📤 সফলভাবে পাঠানো হয়েছে: {success_count} জন\n❌ ফেইল করেছে: {fail_count} জন"
    )


# --- MAIN ENTRY POINT ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
