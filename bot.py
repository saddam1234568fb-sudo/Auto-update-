import os
import re
import requests
import sqlite3
import asyncio
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatAction
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# 🌐 ২৪ ঘণ্টা সার্ভার সজাগ রাখার জন্য
from keep_alive import keep_alive

# গুগল API এর জন্য
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# --- কনফিগারেশন ---
BOT_TOKEN = "8115651258:AAEvXtr3Yg1rqZ5CYP0V5Fq547Y0CFfhEww"  
BLOG_ID = "703905313056903698"     

SCOPES = ['https://www.googleapis.com/auth/blogger']

# --- গুগল ব্লগার এপিআই অথেনটিকেশন ---
def get_blogger_service():
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=8080, open_browser=False)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    return build('blogger', 'v3', credentials=creds)

# --- ওয়েব স্ক্র্যাপার (ভিডিও এবং ছবি ডিটেক্টর) ---
def scrape_post(url):
    try:
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
        soup = BeautifulSoup(r.text, 'html.parser')
        
        # টাইটেল খোঁজা
        title_tag = soup.find('h3', class_='post-title') or soup.find('h1') or soup.find('title')
        title = title_tag.text.strip() if title_tag else "Auto Copied Post"

        # মেইন বডি খোঁজা
        post_body = soup.find('div', class_='post-body')
        if not post_body:
            return None, None, None, None, None

        # 🚫 শুধু অ্যাড এবং ফালতু স্ক্রিপ্ট রিমুভ (iframe রিমুভ হবে না, কারণ ওগুলোতে ভিডিও থাকে)
        for tag in post_body(['script', 'ins', 'style']):
            tag.decompose()
            
        for a_tag in post_body.find_all('a'):
            a_tag.unwrap() # লিংক সরিয়ে শুধু টেক্সট রাখবে

        # 🖼️ ছবি কালেক্ট করা
        images = [img['src'] for img in post_body.find_all('img') if 'src' in img.attrs]
        
        # 🎬 ভিডিও কালেক্ট করা (Direct Video এবং Embed Iframe)
        videos = []
        for vid in post_body.find_all('video'):
            if 'src' in vid.attrs: videos.append(vid['src'])
        for source in post_body.find_all('source'):
            if 'src' in source.attrs and source['src'] not in videos: videos.append(source['src'])
        for iframe in post_body.find_all('iframe'):
            if 'src' in iframe.attrs: videos.append(iframe['src']) # ইউটিউব/টেরাবক্স এমবেড লিংক

        # ক্লিন HTML তৈরি (নিজের সিগনেচার সহ)
        clean_html = str(post_body)
        clean_html += "<br><br><hr><i>✅ Published by Auto-Scraper Bot</i>"
        
        text_content = post_body.get_text(separator="\n", strip=True)

        return title, clean_html, text_content, images, videos
    except Exception as e:
        print(e)
        return None, None, None, None, None

# --- টেলিগ্রাম কমান্ড ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    txt = f"🎉 <b>হ্যালো {user.first_name}!</b>\n\n🤖 আমি <b>Smart Scraper & Auto-Blogger Bot!</b>\n\n🔗 <b>কাজ:</b> যেকোনো ব্লগারের পোস্ট লিংক আমাকে দিন। আমি সেখান থেকে ভিডিও, ছবি এবং লেখা কপি করে সরাসরি আপনার ব্লগে আপলোড করে দেব!"
    await update.message.reply_text(txt, parse_mode=ParseMode.HTML)

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    if not url.startswith("http"):
        await update.message.reply_text("❌ এটি কোনো সঠিক লিংক নয়। দয়া করে ওয়েবসাইটের লিংক দিন।")
        return

    msg = await update.message.reply_text("⏳ <b>লিংকটি স্ক্যান করা হচ্ছে... ভিডিও এবং ছবি খোঁজা হচ্ছে!</b>", parse_mode=ParseMode.HTML)
    
    # ব্যাকগ্রাউন্ডে স্ক্র্যাপিং
    loop = asyncio.get_event_loop()
    title, clean_html, text_content, images, videos = await loop.run_in_executor(None, scrape_post, url)
    
    if not title or not clean_html:
        await msg.edit_text("❌ <b>পোস্ট কপি করা সম্ভব হয়নি!</b> সাইটটি সিকিউরড হতে পারে।", parse_mode=ParseMode.HTML)
        return

    # মেমোরিতে সেভ
    context.user_data['scraped_title'] = title
    context.user_data['scraped_html'] = clean_html
    context.user_data['scraped_text'] = text_content
    context.user_data['scraped_images'] = images
    context.user_data['scraped_videos'] = videos

    # বাটন দেওয়া
    kb = [
        [InlineKeyboardButton("📥 ডাউনলোড মিডিয়া (টেলিগ্রামে)", callback_data="dl_media")],
        [InlineKeyboardButton("🌐 আমার ব্লগারে আপলোড করুন", callback_data="up_blogger")]
    ]
    await msg.edit_text(
        f"✅ <b>পোস্ট সফলভাবে কপি হয়েছে!</b>\n\n📌 <b>টাইটেল:</b> {title}\n🖼️ <b>ছবি পাওয়া গেছে:</b> {len(images)} টি\n🎬 <b>ভিডিও পাওয়া গেছে:</b> {len(videos)} টি\n\n👇 <i>কী করতে চান তা সিলেক্ট করুন:</i>", 
        reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML
    )

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    title = context.user_data.get('scraped_title')
    html_content = context.user_data.get('scraped_html')
    text_content = context.user_data.get('scraped_text')
    images = context.user_data.get('scraped_images', [])
    videos = context.user_data.get('scraped_videos', [])

    if data == "dl_media":
        await query.edit_message_text("⏳ <b>ছবি, ভিডিও ও লেখা আপনার ইনবক্সে পাঠানো হচ্ছে...</b>", parse_mode=ParseMode.HTML)
        await context.bot.send_message(chat_id=query.message.chat.id, text=f"📌 <b>{title}</b>\n\n{text_content[:3000]}", parse_mode=ParseMode.HTML)
        
        # ছবি পাঠানো
        for img in images[:5]: 
            try: await context.bot.send_photo(chat_id=query.message.chat.id, photo=img)
            except: pass
            
        # ভিডিও পাঠানো
        if videos:
            await context.bot.send_message(chat_id=query.message.chat.id, text="🎬 <b>ভিডিও লিংক/ফাইল প্রসেস করা হচ্ছে...</b>", parse_mode=ParseMode.HTML)
            for vid in videos[:3]:
                try: 
                    if vid.endswith('.mp4'):
                        await context.bot.send_video(chat_id=query.message.chat.id, video=vid)
                    else:
                        await context.bot.send_message(chat_id=query.message.chat.id, text=f"🎥 <b>ভিডিও লিংক:</b> {vid}", parse_mode=ParseMode.HTML)
                except: pass
                
        await context.bot.send_message(chat_id=query.message.chat.id, text="✅ <b>সব ডাটা পাঠানো সম্পন্ন!</b>", parse_mode=ParseMode.HTML)

    elif data == "up_blogger":
        await query.edit_message_text("🚀 <b>আপনার ব্লগারে পোস্টটি আপলোড করা হচ্ছে...</b>", parse_mode=ParseMode.HTML)
        try:
            loop = asyncio.get_event_loop()
            service = await loop.run_in_executor(None, get_blogger_service)
            
            post_data = {
                'kind': 'blogger#post',
                'title': title,
                'content': html_content  # ⚠️ এখানে ভিডিওর <iframe> এবং ছবির কোড অটোমেটিক থাকবে
            }
            # ব্লগারে আপলোড
            request = service.posts().insert(blogId=BLOG_ID, body=post_data, isDraft=False)
            response = await loop.run_in_executor(None, request.execute)
            
            post_url = response.get('url')
            await query.message.reply_text(f"🎉 <b>আলহামদুলিল্লাহ! আপনার ব্লগে ভিডিও ও ছবিসহ পোস্টটি সফলভাবে পাবলিশ হয়েছে!</b>\n\n🔗 <b>পোস্টের লিংক:</b>\n👉 {post_url}", parse_mode=ParseMode.HTML)
        except Exception as e:
            await query.message.reply_text(f"❌ <b>আপলোড ব্যর্থ হয়েছে!</b> কারণ: {e}", parse_mode=ParseMode.HTML)

def main():
    # 🌐 ২৪ ঘণ্টা জাগিয়ে রাখার জন্য
    keep_alive()
    
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    app.add_handler(CallbackQueryHandler(button_click))
    
    print("🚀 Auto-Blogger Scraper Bot is running 24/7...")
    app.run_polling()

if __name__ == '__main__':
    main()
