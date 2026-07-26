import os
import re
import time
import requests
import sqlite3
import asyncio
from bs4 import BeautifulSoup
import yt_dlp
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

# --- ⚙️ কনফিগারেশন ---
BOT_TOKEN = "8115651258:AAE9yTHft6BVp8QUeCpmIwITJei8OGmQ0W4"  
BLOG_ID = "703905313056903698"     

# ⚠️ আপনার যেসব টেলিগ্রাম চ্যানেলে অটো-পোস্ট হবে, সেগুলোর ID দিন
AUTO_POST_CHANNELS = ["-1003529992505", "-1003847092759"]

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

# --- পাওয়ারফুল ভিডিও ডাউনলোডার (yt-dlp + Requests) ---
def download_video(url, user_id):
    filename = f"vid_{user_id}_{int(time.time())}.mp4"
    ydl_opts = {
        'format': 'best[ext=mp4][filesize<50M]/best',
        'outtmpl': filename,
        'quiet': True,
        'noplaylist': True,
        'no_warnings': True
    }
    try:
        # চেষ্টা ১: yt-dlp দিয়ে ডাউনলোড
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        if os.path.exists(filename): return filename
    except:
        pass
        
    # চেষ্টা ২: যদি ডাইরেক্ট mp4 লিংক হয়, তবে ম্যানুয়াল ডাউনলোড
    if '.mp4' in url:
        try:
            r = requests.get(url, stream=True, headers={'User-Agent': 'Mozilla/5.0'})
            with open(filename, 'wb') as f:
                for chunk in r.iter_content(chunk_size=1024*1024):
                    if chunk: f.write(chunk)
            if os.path.exists(filename): return filename
        except:
            pass
    return None

# --- ইমেজ হোস্টিং (Telegraph API) ম্যানুয়াল পোস্টের জন্য ---
def upload_image_to_telegraph(file_path):
    try:
        with open(file_path, 'rb') as f:
            r = requests.post('https://telegra.ph/upload', files={'file': ('image.jpg', f, 'image/jpeg')})
            return "https://telegra.ph" + r.json()[0]['src']
    except:
        return None

# --- ওয়েব স্ক্র্যাপার (স্মার্ট ডিটেক্টর) ---
def scrape_post(url):
    try:
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        soup = BeautifulSoup(r.text, 'html.parser')
        
        # টাইটেল খোঁজা
        title_tag = soup.find('h3', class_=re.compile(r'post-title')) or soup.find('h1') or soup.find('title')
        title = title_tag.text.strip() if title_tag else "Auto Copied Post"

        # মেইন বডি খোঁজা (সব ধরনের থিমের জন্য)
        post_body = soup.find('div', class_=re.compile(r'post-body|entry-content|post-content')) or soup.find('article')
        if not post_body: 
            post_body = soup.find('body') # ফলব্যাক
            if not post_body: return None, None, None, None, None

        for tag in post_body(['script', 'ins', 'style']):
            tag.decompose()
        for a_tag in post_body.find_all('a'):
            a_tag.unwrap()

        images = [img['src'] for img in post_body.find_all('img') if 'src' in img.attrs]
        
        videos = []
        for vid in post_body.find_all(['video', 'source', 'iframe']):
            src = vid.get('src') or vid.get('data-src')
            if src and src not in videos:
                videos.append(src)

        clean_html = str(post_body)
        text_content = post_body.get_text(separator="\n", strip=True)

        return title, clean_html, text_content, images, videos
    except:
        return None, None, None, None, None

# --- চ্যানেলে অটো ব্রডকাস্ট ---
async def broadcast_to_channels(context, post_url, image_url=None):
    msg_text = f"ফুল ভিডিও দেখতে নিচের লিংকে অথবা ফুল ভিডিও দেখুন বাটনে ক্লিক করে দেখে আসুন 👇\n\n🔗 <b>লিংক:</b> {post_url}"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🎬 ফুল ভিডিও দেখুন", url=post_url)]])
    
    for ch_id in AUTO_POST_CHANNELS:
        try:
            if image_url:
                await context.bot.send_photo(chat_id=ch_id, photo=image_url, caption=msg_text, reply_markup=kb, parse_mode=ParseMode.HTML)
            else:
                await context.bot.send_message(chat_id=ch_id, text=msg_text, reply_markup=kb, parse_mode=ParseMode.HTML)
        except Exception as e:
            print(f"Broadcast Error on {ch_id}: {e}")

# --- টেলিগ্রাম কমান্ড ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    txt = f"🎉 <b>হ্যালো {user.first_name}!</b>\n\n🤖 আমি <b>Smart Scraper & Auto-Blogger Bot!</b>\n\n🔗 <b>অটো পোস্ট:</b> ব্লগারের লিংক দিন।\n📝 <b>ম্যানুয়াল পোস্ট:</b> /blog লিখে সেন্ড করুন।"
    await update.message.reply_text(txt, parse_mode=ParseMode.HTML)

# --- ম্যানুয়াল পোস্ট কমান্ড (/blog) ---
async def manual_blog_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['state'] = 'WAITING_MANUAL_POST'
    await update.message.reply_text("📝 <b>ম্যানুয়াল পোস্ট মোড:</b>\n\nদয়া করে একটি ছবি (Photo) এবং সাথে আপনার টাইটেল/ক্যাপশন লিখে সেন্ড করুন। আমি এটি ব্লগে আপলোড করে চ্যানেলে শেয়ার করে দেব!", parse_mode=ParseMode.HTML)

# --- ইনপুট হ্যান্ডলার (Smart State Resolver) ---
async def handle_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get('state')
    text = update.message.text or update.message.caption or ""

    # 💡 স্মার্ট লজিক: ইউজার যদি ভুল করে /blog দেওয়ার পরও লিংক দেয়, তবে স্ক্র্যাপার কাজ করবে!
    if text.startswith("http"):
        context.user_data['state'] = None
        url = text
        msg = await update.message.reply_text("⏳ <b>লিংকটি স্ক্যান করা হচ্ছে... ভিডিও এবং ছবি খোঁজা হচ্ছে!</b>", parse_mode=ParseMode.HTML)
        
        loop = asyncio.get_event_loop()
        title, clean_html, text_content, images, videos = await loop.run_in_executor(None, scrape_post, url)
        
        if not title or not clean_html:
            return await msg.edit_text("❌ <b>পোস্ট কপি করা সম্ভব হয়নি!</b> সাইটটি সিকিউরড হতে পারে।", parse_mode=ParseMode.HTML)

        context.user_data['scraped_title'] = title
        context.user_data['scraped_html'] = clean_html
        context.user_data['scraped_text'] = text_content
        context.user_data['scraped_images'] = images
        context.user_data['scraped_videos'] = videos

        kb = [
            [InlineKeyboardButton("📥 ডাউনলোড মিডিয়া (ভিডিও/ছবি)", callback_data="dl_media")],
            [InlineKeyboardButton("🌐 ব্লগারে ও চ্যানেলে আপলোড করুন", callback_data="up_blogger")]
        ]
        return await msg.edit_text(f"✅ <b>পোস্ট সফলভাবে কপি হয়েছে!</b>\n\n📌 <b>টাইটেল:</b> {title[:50]}...\n🖼️ <b>ছবি:</b> {len(images)} টি\n🎬 <b>ভিডিও লিংক:</b> {len(videos)} টি\n\n👇 <i>কী করতে চান তা সিলেক্ট করুন:</i>", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)

    # ম্যানুয়াল পোস্ট লজিক (ছবি + ক্যাপশন)
    if state == 'WAITING_MANUAL_POST':
        if not update.message.photo:
            return await update.message.reply_text("❌ দয়া করে একটি ছবি (Photo) সেন্ড করুন ক্যাপশনসহ! (অথবা অটো-পোস্ট করতে সরাসরি লিংক দিন)")
            
        status = await update.message.reply_text("⏳ <b>ছবি প্রসেস করা হচ্ছে... ব্লগারে আপলোড হচ্ছে!</b>", parse_mode=ParseMode.HTML)
        
        caption = update.message.caption if update.message.caption else "New Post"
        photo_file = await update.message.photo[-1].get_file()
        file_path = await photo_file.download_to_drive()
        
        loop = asyncio.get_event_loop()
        img_url = await loop.run_in_executor(None, upload_image_to_telegraph, file_path)
        os.remove(file_path)
        
        if not img_url:
            return await status.edit_text("❌ ছবি হোস্ট করতে সমস্যা হয়েছে!")
            
        html_content = f"<center><img src='{img_url}' width='100%'></center><br><br><p>{caption}</p><br><hr><i>✅ Published by AutoBot</i>"
        
        try:
            service = await loop.run_in_executor(None, get_blogger_service)
            post_data = {'kind': 'blogger#post', 'title': caption[:50], 'content': html_content}
            request = service.posts().insert(blogId=BLOG_ID, body=post_data, isDraft=False)
            response = await loop.run_in_executor(None, request.execute)
            post_url = response.get('url')
            
            await status.edit_text(f"ফুল ভিডিও দেখতে লিংকে অথবা ছবিতে ক্লিক করে ভিডিও দেখুন 👇👆\n\n🔗 {post_url}", parse_mode=ParseMode.HTML)
            await broadcast_to_channels(context, post_url, img_url)
        except Exception as e:
            await status.edit_text(f"❌ <b>ব্লগার আপলোড ব্যর্থ:</b> {e}")
            
        context.user_data['state'] = None
        return

    # যদি লিংক বা ছবি না দিয়ে সাধারণ টেক্সট দেয়
    await update.message.reply_text("🤖 দয়া করে ওয়েবসাইটের লিংক দিন অথবা /blog ব্যবহার করুন।", parse_mode=ParseMode.HTML)

# --- বাটন ক্লিক হ্যান্ডলার ---
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
        status_msg = await query.edit_message_text("⏳ <b>ফাইলগুলো ডাউনলোড করা হচ্ছে... অপেক্ষা করুন!</b>", parse_mode=ParseMode.HTML)
        await context.bot.send_message(chat_id=query.message.chat.id, text=f"📌 <b>{title}</b>\n\n{text_content[:2000]}", parse_mode=ParseMode.HTML)
        
        for img in images[:5]: 
            try: await context.bot.send_photo(chat_id=query.message.chat.id, photo=img)
            except: pass
            
        if videos:
            await context.bot.send_message(chat_id=query.message.chat.id, text="🎬 <b>ভিডিও ডাউনলোড করা হচ্ছে... (একটু সময় লাগতে পারে)</b>", parse_mode=ParseMode.HTML)
            loop = asyncio.get_event_loop()
            for vid in videos[:3]:
                try: 
                    # ভিডিও ডাইরেক্ট ডাউনলোড করার চেষ্টা
                    vid_file = await loop.run_in_executor(None, download_video, vid, query.from_user.id)
                    if vid_file:
                        await context.bot.send_chat_action(chat_id=query.message.chat.id, action=ChatAction.UPLOAD_VIDEO)
                        with open(vid_file, 'rb') as f:
                            await context.bot.send_video(chat_id=query.message.chat.id, video=f)
                        os.remove(vid_file)
                    else:
                        # যদি টেরাবক্স বা এমন সিকিউরড লিংক হয় যা ডাউনলোড হলো না, তবে লিংক দিয়ে দেবে
                        await context.bot.send_message(chat_id=query.message.chat.id, text=f"🎥 <b>ভিডিও লিংক (সরাসরি ক্লিক করুন):</b>\n{vid}", parse_mode=ParseMode.HTML)
                except: pass
                
        await status_msg.edit_text("✅ <b>সব ডাটা পাঠানো সম্পন্ন!</b>", parse_mode=ParseMode.HTML)

    elif data == "up_blogger":
        await query.edit_message_text("🚀 <b>আপনার ব্লগারে পোস্ট আপলোড করা হচ্ছে...</b>", parse_mode=ParseMode.HTML)
        try:
            loop = asyncio.get_event_loop()
            service = await loop.run_in_executor(None, get_blogger_service)
            
            post_data = {'kind': 'blogger#post', 'title': title[:100], 'content': html_content}
            request = service.posts().insert(blogId=BLOG_ID, body=post_data, isDraft=False)
            response = await loop.run_in_executor(None, request.execute)
            
            post_url = response.get('url')
            
            # আপনার পছন্দের ক্যাপশন
            msg_text = f"ফুল ভিডিও দেখতে লিংকে অথবা ছবিতে ক্লিক করে ভিডিও দেখুন 👇👆\n\n🔗 {post_url}"
            await query.message.reply_text(msg_text, parse_mode=ParseMode.HTML)
            
            # অটোমেটিক চ্যানেলে ব্রডকাস্ট
            main_image = images[0] if images else None
            await broadcast_to_channels(context, post_url, main_image)
            
        except Exception as e:
            await query.message.reply_text(f"❌ <b>আপলোড ব্যর্থ হয়েছে!</b> কারণ: {e}", parse_mode=ParseMode.HTML)

def main():
    keep_alive()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("blog", manual_blog_command))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_input))
    app.add_handler(CallbackQueryHandler(button_click))
    
    print("🚀 Auto-Blogger Pro Bot is running 24/7...")
    app.run_polling()

if __name__ == '__main__':
    main()
