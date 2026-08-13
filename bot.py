#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import asyncio
import logging
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse, parse_qs
import requests
import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram.constants import ParseMode
from telethon import TelegramClient
from telethon.tl.types import DocumentAttributeVideo, DocumentAttributeFilename
import io

# Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Config from env
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")

# Headers for Classplus/Akamai
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': '*/*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Origin': 'https://classplusapp.com',
    'Referer': 'https://classplusapp.com/',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'cross-site',
}

# Active downloads
active_downloads = {}

# Telethon client for large files
telethon_client = None

class DirectVideoDownloader:
    """Video directly download karein without saving permanently"""
    
    @staticmethod
    def validate_url(url):
        """URL validate karein"""
        try:
            parsed = urlparse(url)
            
            # Expiry check
            if 'exp' in parse_qs(parsed.query):
                exp_time = int(parse_qs(parsed.query)['exp'][0])
                current_time = int(datetime.now().timestamp())
                
                if current_time > exp_time:
                    return False, f"❌ Token expire ho gaya!\nExpired: {datetime.fromtimestamp(exp_time).strftime('%Y-%m-%d %H:%M:%S')}"
            
            # URL check
            response = requests.head(url, headers=HEADERS, allow_redirects=True, timeout=10)
            
            if response.status_code == 403:
                return False, "❌ Access Denied! Fresh URL use karein."
            elif response.status_code == 404:
                return False, "❌ Video not found!"
            
            return True, "✅ URL valid hai"
            
        except Exception as e:
            return False, f"❌ Error: {str(e)}"
    
    @staticmethod
    async def download_to_memory(url, quality="720", progress_callback=None):
        """Video download karke temporary file mein save karein"""
        try:
            # Temporary file create
            with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as tmp_file:
                temp_path = tmp_file.name
            
            # Quality selection
            if quality == "1080":
                format_str = 'bestvideo[height<=1080]+bestaudio/best[height<=1080]/best'
            elif quality == "720":
                format_str = 'bestvideo[height<=720]+bestaudio/best[height<=720]/best'
            elif quality == "480":
                format_str = 'bestvideo[height<=480]+bestaudio/best[height<=480]/best'
            elif quality == "360":
                format_str = 'bestvideo[height<=360]+bestaudio/best[height<=360]/best'
            else:
                format_str = 'best'
            
            ydl_opts = {
                'format': format_str,
                'merge_output_format': 'mp4',
                'outtmpl': temp_path,
                'http_headers': HEADERS,
                'nocheckcertificate': True,
                'ignoreerrors': False,
                'quiet': True,
                'no_warnings': True,
                'retries': 2,
                'fragment_retries': 2,
                'concurrent_fragment_downloads': 3,
                'progress_hooks': [lambda d: DirectVideoDownloader._progress_hook(d, progress_callback)] if progress_callback else [],
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                
                # Final path check
                video_path = temp_path
                if not os.path.exists(video_path):
                    base = os.path.splitext(temp_path)[0]
                    for ext in ['.mp4', '.mkv', '.webm']:
                        if os.path.exists(base + ext):
                            video_path = base + ext
                            break
                
                file_size = os.path.getsize(video_path)
                
                return {
                    'success': True,
                    'path': video_path,
                    'title': info.get('title', 'video'),
                    'duration': info.get('duration', 0),
                    'size': file_size,
                    'size_mb': file_size / (1024 * 1024)
                }
                
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    @staticmethod
    def _progress_hook(d, progress_callback):
        """Progress update"""
        if d['status'] == 'downloading':
            try:
                downloaded = d.get('downloaded_bytes', 0)
                total = d.get('total_bytes', 0) or d.get('total_bytes_estimate', 0)
                speed = d.get('speed', 0)
                eta = d.get('eta', 0)
                
                if total and progress_callback:
                    percent = (downloaded / total) * 100
                    speed_mb = speed / (1024 * 1024) if speed else 0
                    progress_callback(percent, speed_mb, eta)
            except:
                pass
        elif d['status'] == 'finished':
            if progress_callback:
                progress_callback(100, 0, 0)

class DirectUploader:
    """Direct upload without saving"""
    
    @staticmethod
    async def init_client():
        """Telethon client initialize"""
        global telethon_client
        try:
            if telethon_client is None:
                telethon_client = TelegramClient('memory_session', API_ID, API_HASH)
                await telethon_client.start(bot_token=BOT_TOKEN)
                logger.info("Telethon client ready")
            return telethon_client
        except Exception as e:
            logger.error(f"Telethon init error: {e}")
            return None
    
    @staticmethod
    async def upload_video(chat_id, video_path, title="", duration=0):
        """Video upload karein - chota ya bada"""
        try:
            file_size = os.path.getsize(video_path)
            file_size_mb = file_size / (1024 * 1024)
            
            logger.info(f"Uploading {file_size_mb:.1f} MB file...")
            
            # 50MB se chota - Bot API use karo
            if file_size <= 50 * 1024 * 1024:
                from telegram import Bot
                bot = Bot(token=BOT_TOKEN)
                
                with open(video_path, 'rb') as f:
                    await bot.send_video(
                        chat_id=chat_id,
                        video=f,
                        caption=title,
                        supports_streaming=True
                    )
                logger.info("Uploaded via Bot API")
                return True
            
            # 50MB se bada - Telethon use karo
            client = await DirectUploader.init_client()
            if not client:
                return False
            
            attributes = [
                DocumentAttributeVideo(
                    duration=int(duration) if duration else 0,
                    w=1280,
                    h=720,
                    supports_streaming=True
                ),
                DocumentAttributeFilename(os.path.basename(video_path))
            ]
            
            # Progress callback
            async def progress_cb(current, total):
                if total:
                    percent = (current / total) * 100
                    logger.info(f"Upload: {percent:.1f}%")
            
            with open(video_path, 'rb') as f:
                await client.send_file(
                    chat_id,
                    f,
                    caption=title,
                    attributes=attributes,
                    supports_streaming=True,
                    progress_callback=progress_cb,
                    part_size_kb=512,
                    force_document=False
                )
            
            logger.info("Uploaded via Telethon")
            return True
            
        except Exception as e:
            logger.error(f"Upload error: {e}")
            return False

class VideoBot:
    def __init__(self):
        self.downloader = DirectVideoDownloader()
        self.uploader = DirectUploader()
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start command"""
        welcome = (
            "🎬 **Video Download Bot**\n\n"
            "Main videos directly download karke Telegram par upload karta hoon.\n"
            "Koi file save nahi hoti - sab direct hota hai!\n\n"
            "**✨ Features:**\n"
            "✅ Direct download & upload\n"
            "✅ No permanent storage\n"
            "✅ 2GB tak support\n"
            "✅ Quality selection\n\n"
            "**📝 Usage:**\n"
            "1. Video URL bhejo\n"
            "2. Quality select karo\n"
            "3. Video mil jayega!\n\n"
            "/help - Help ke liye"
        )
        
        keyboard = [
            [InlineKeyboardButton("📖 Help", callback_data='help')]
        ]
        
        await update.message.reply_text(
            welcome,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    async def help_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Help command"""
        help_text = (
            "**📚 Help**\n\n"
            "**URL Kaise Milega:**\n"
            "1. Classplus website kholo\n"
            "2. Video start karo\n"
            "3. F12 (DevTools) kholo\n"
            "4. Network tab mein jao\n"
            "5. `.m3u8` URL dhundho\n"
            "6. Copy karke bot ko bhejo\n\n"
            "**Quality Options:**\n"
            "• 144p - Low\n"
            "• 360p - Medium\n"
            "• 480p - Good\n"
            "• 720p - HD\n"
            "• 1080p - Full HD\n"
            "• Best - Auto\n\n"
            "**Note:**\n"
            "Files directly upload hoti hain\n"
            "Koi storage use nahi hota"
        )
        
        await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)
    
    async def handle_url(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """URL handler"""
        user_id = update.effective_user.id
        url = update.message.text.strip()
        
        # Active check
        if user_id in active_downloads:
            await update.message.reply_text(
                "⏳ Ek download already chal raha hai!\n"
                "Complete hone ka wait karo.",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        # URL validate
        if not url.startswith(('http://', 'https://')):
            await update.message.reply_text(
                "❌ Invalid URL!\nhttp:// ya https:// se start ho.",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        status_msg = await update.message.reply_text(
            "🔍 URL validate kar raha hoon...",
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Validate
        valid, message = self.downloader.validate_url(url)
        if not valid:
            await status_msg.edit_text(message, parse_mode=ParseMode.MARKDOWN)
            return
        
        # Quality selection buttons
        keyboard = [
            [InlineKeyboardButton("360p", callback_data=f'q_360_{user_id}'),
             InlineKeyboardButton("480p", callback_data=f'q_480_{user_id}')],
            [InlineKeyboardButton("720p", callback_data=f'q_720_{user_id}'),
             InlineKeyboardButton("1080p", callback_data=f'q_1080_{user_id}')],
            [InlineKeyboardButton("Best Quality", callback_data=f'q_best_{user_id}')]
        ]
        
        context.user_data['pending_url'] = url
        
        await status_msg.edit_text(
            "✅ URL valid!\n\n"
            "**Quality select karo:**",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    async def quality_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Quality selection handler"""
        query = update.callback_query
        await query.answer()
        
        data = query.data.split('_')
        if len(data) < 3:
            return
        
        action = data[0]
        if action != 'q':
            return
        
        quality = data[1]
        user_id = int(data[2])
        
        if user_id != query.from_user.id:
            await query.message.reply_text("❌ Ye aapka download nahi hai!")
            return
        
        url = context.user_data.get('pending_url')
        if not url:
            await query.message.edit_text("❌ URL not found! Dobara bhejo.")
            return
        
        # Download start
        active_downloads[user_id] = {
            'url': url,
            'quality': quality,
            'status': 'downloading'
        }
        
        await query.message.edit_text(
            f"⬇️ Downloading ({quality})...\n"
            "Direct upload hoga, koi file save nahi hogi.",
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Progress callback
        async def progress_cb(percent, speed, eta):
            try:
                bar_length = 20
                filled = int(bar_length * percent / 100)
                bar = '█' * filled + '░' * (bar_length - filled)
                
                await query.message.edit_text(
                    f"⬇️ **Downloading...**\n\n"
                    f"{bar}\n"
                    f"📊 {percent:.1f}%\n"
                    f"⚡ {speed:.2f} MB/s\n"
                    f"⏱️ ETA: {eta}s\n\n"
                    f"Direct upload hoga - no storage!",
                    parse_mode=ParseMode.MARKDOWN
                )
            except:
                pass
        
        # Download to temp
        result = await self.downloader.download_to_memory(url, quality, progress_cb)
        
        if not result['success']:
            if user_id in active_downloads:
                del active_downloads[user_id]
            await query.message.edit_text(
                f"❌ Download failed!\n\n"
                f"Error: {result['error']}\n\n"
                f"Fresh URL try karo.",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        # Upload directly
        await query.message.edit_text(
            f"📤 Uploading directly...\n"
            f"Size: {result['size_mb']:.1f} MB\n"
            f"No storage use ho rahi!",
            parse_mode=ParseMode.MARKDOWN
        )
        
        try:
            success = await self.uploader.upload_video(
                query.message.chat_id,
                result['path'],
                f"✅ {result['title']}\n📁 {result['size_mb']:.1f} MB",
                result['duration']
            )
            
            if success:
                await query.message.edit_text(
                    "✅ Video successfully upload ho gaya!\n"
                    "Koi file save nahi hui.",
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await query.message.edit_text(
                    "❌ Upload failed! Dobara try karo.",
                    parse_mode=ParseMode.MARKDOWN
                )
            
        except Exception as e:
            await query.message.edit_text(
                f"❌ Upload error: {str(e)}",
                parse_mode=ParseMode.MARKDOWN
            )
        
        # Cleanup - temp file delete
        if user_id in active_downloads:
            del active_downloads[user_id]
        
        try:
            if os.path.exists(result['path']):
                os.remove(result['path'])
                logger.info("Temp file deleted")
        except:
            pass
    
    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Button callback"""
        query = update.callback_query
        await query.answer()
        
        if query.data == 'help':
            await self.help_cmd(update, context)
    
    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Error handler"""
        logger.error(f"Error: {context.error}")
        try:
            if update and update.effective_message:
                await update.effective_message.reply_text(
                    "❌ Error aa gaya! Dobara try karo.",
                    parse_mode=ParseMode.MARKDOWN
                )
        except:
            pass
    
    def run(self):
        """Bot run"""
        app = Application.builder().token(BOT_TOKEN).build()
        
        # Handlers
        app.add_handler(CommandHandler("start", self.start))
        app.add_handler(CommandHandler("help", self.help_cmd))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_url))
        app.add_handler(CallbackQueryHandler(self.quality_callback, pattern='^q_'))
        app.add_handler(CallbackQueryHandler(self.button_callback))
        app.add_error_handler(self.error_handler)
        
        logger.info("Bot starting...")
        app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    bot = VideoBot()
    bot.run()
