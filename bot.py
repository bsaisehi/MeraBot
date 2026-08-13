#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import asyncio
import logging
import tempfile
import subprocess
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
import math
import time

# Logging setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Environment variables se config
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
SESSION_NAME = os.getenv("SESSION_NAME", "video_bot")

# Download directory
DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

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

# Active downloads tracking
active_downloads = {}

# Telegram client for large uploads (2GB limit)
telethon_client = None

class VideoDownloader:
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
    async def download_video(url, quality="720", progress_callback=None):
        """Video download karein"""
        try:
            output_template = str(DOWNLOAD_DIR / '%(title)s.%(ext)s')
            
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
                'outtmpl': output_template,
                'http_headers': HEADERS,
                'nocheckcertificate': True,
                'ignoreerrors': False,
                'quiet': True,
                'no_warnings': True,
                'retries': 3,
                'fragment_retries': 3,
                'concurrent_fragment_downloads': 5,
                'progress_hooks': [lambda d: VideoDownloader._progress_hook(d, progress_callback)] if progress_callback else [],
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                video_path = ydl.prepare_filename(info)
                
                # Final path check
                if not os.path.exists(video_path):
                    base = os.path.splitext(video_path)[0]
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
    
    @staticmethod
    async def compress_video(input_path, max_size_mb=1900):
        """Video compress karein agar 2GB se bada hai"""
        try:
            file_size = os.path.getsize(input_path)
            size_mb = file_size / (1024 * 1024)
            
            if size_mb <= max_size_mb:
                return input_path
            
            output_path = str(Path(input_path).with_suffix('.compressed.mp4'))
            
            # Target bitrate calculate
            duration = await VideoDownloader.get_duration(input_path)
            target_bitrate = int((max_size_mb * 8 * 1024) / duration) if duration > 0 else 1000
            
            cmd = [
                'ffmpeg', '-i', input_path,
                '-c:v', 'libx264',
                '-preset', 'medium',
                '-b:v', f'{target_bitrate}k',
                '-c:a', 'aac',
                '-b:a', '128k',
                '-movflags', '+faststart',
                '-y',
                output_path
            ]
            
            process = subprocess.run(cmd, capture_output=True, text=True)
            
            if os.path.exists(output_path) and os.path.getsize(output_path) < file_size:
                os.remove(input_path)
                return output_path
            
            return input_path
            
        except Exception as e:
            logger.error(f"Compression error: {e}")
            return input_path
    
    @staticmethod
    async def get_duration(video_path):
        """Video duration get karein"""
        try:
            cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', video_path]
            result = subprocess.run(cmd, capture_output=True, text=True)
            return float(result.stdout.strip())
        except:
            return 0

class LargeUploader:
    """Telethon use karke 2GB tak upload"""
    
    @staticmethod
    async def init_client():
        """Telethon client initialize"""
        global telethon_client
        try:
            if telethon_client is None:
                telethon_client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
                await telethon_client.start(bot_token=BOT_TOKEN)
                logger.info("Telethon client initialized successfully")
            return telethon_client
        except Exception as e:
            logger.error(f"Telethon init error: {e}")
            return None
    
    @staticmethod
    async def upload_large_video(chat_id, video_path, title="", duration=0):
        """Large video upload using Telethon"""
        try:
            client = await LargeUploader.init_client()
            if not client:
                return None
            
            file_size = os.path.getsize(video_path)
            file_size_mb = file_size / (1024 * 1024)
            
            # Upload with progress
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
            async def progress_callback(current, total):
                if total:
                    percent = (current / total) * 100
                    logger.info(f"Upload progress: {percent:.1f}%")
            
            # File upload
            with open(video_path, 'rb') as f:
                result = await client.send_file(
                    chat_id,
                    f,
                    caption=title,
                    attributes=attributes,
                    supports_streaming=True,
                    progress_callback=progress_callback,
                    part_size_kb=512,
                    force_document=False
                )
            
            return result
            
        except Exception as e:
            logger.error(f"Upload error: {e}")
            return None

class Bot:
    def __init__(self):
        self.downloader = VideoDownloader()
        self.uploader = LargeUploader()
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start command"""
        welcome_text = (
            "🎬 **Video Download Bot**\n\n"
            "Main Classplus aur dusre platforms ke videos download karke Telegram par upload karta hoon.\n\n"
            "**✨ Features:**\n"
            "✅ m3u8/HLS support\n"
            "✅ MP4 format\n"
            "✅ 2GB tak upload\n"
            "✅ Quality selection\n"
            "✅ Progress tracking\n\n"
            "**📝 Usage:**\n"
            "1. Video URL bhejo\n"
            "2. Quality select karo\n"
            "3. Video download aur upload hoga\n\n"
            "**Commands:**\n"
            "/start - Bot start\n"
            "/help - Help\n"
            "/status - Active downloads"
        )
        
        keyboard = [
            [InlineKeyboardButton("📖 Help", callback_data='help'),
             InlineKeyboardButton("ℹ️ About", callback_data='about')]
        ]
        
        await update.message.reply_text(
            welcome_text,
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
            "• 144p - Low quality\n"
            "• 360p - Medium\n"
            "• 480p - Good\n"
            "• 720p - HD\n"
            "• 1080p - Full HD\n\n"
            "**Limits:**\n"
            "📁 Max 2GB file\n"
            "⚡ 3 parallel downloads"
        )
        
        await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)
    
    async def handle_url(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """URL handler"""
        user_id = update.effective_user.id
        url = update.message.text.strip()
        
        # Active download check
        if user_id in active_downloads:
            await update.message.reply_text(
                "⏳ Ek download already chal raha hai!\n"
                "Wait karo ya /status check karo.",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        # URL validate
        if not url.startswith(('http://', 'https://')):
            await update.message.reply_text(
                "❌ Invalid URL!\nhttp:// ya https:// se start hona chahiye.",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        # Status message
        status_msg = await update.message.reply_text(
            "🔍 URL validate kar raha hoon...",
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Validate
        valid, message = self.downloader.validate_url(url)
        if not valid:
            await status_msg.edit_text(message, parse_mode=ParseMode.MARKDOWN)
            return
        
        # Quality selection
        keyboard = [
            [InlineKeyboardButton("360p", callback_data=f'q_360_{user_id}'),
             InlineKeyboardButton("480p", callback_data=f'q_480_{user_id}')],
            [InlineKeyboardButton("720p", callback_data=f'q_720_{user_id}'),
             InlineKeyboardButton("1080p", callback_data=f'q_1080_{user_id}')],
            [InlineKeyboardButton("Best", callback_data=f'q_best_{user_id}')]
        ]
        
        # URL store in context
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
            "Ye process time le sakta hai.",
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
                    f"⏱️ ETA: {eta}s",
                    parse_mode=ParseMode.MARKDOWN
                )
            except:
                pass
        
        # Download
        result = await self.downloader.download_video(url, quality, progress_cb)
        
        if not result['success']:
            del active_downloads[user_id]
            await query.message.edit_text(
                f"❌ Download failed!\n\n"
                f"Error: {result['error']}\n\n"
                f"Fresh URL try karo.",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        # Compress if needed
        if result['size'] > 2 * 1024 * 1024 * 1024:  # 2GB
            await query.message.edit_text(
                "📦 File bada hai, compress kar raha hoon...",
                parse_mode=ParseMode.MARKDOWN
            )
            result['path'] = await self.downloader.compress_video(result['path'])
        
        # Upload
        await query.message.edit_text(
            f"📤 Uploading...\n"
            f"Size: {result['size_mb']:.1f} MB",
            parse_mode=ParseMode.MARKDOWN
        )
        
        try:
            if result['size'] <= 50 * 1024 * 1024:  # 50MB - Bot API
                with open(result['path'], 'rb') as f:
                    await query.message.reply_video(
                        video=f,
                        caption=f"✅ {result['title']}\n📁 {result['size_mb']:.1f} MB",
                        supports_streaming=True
                    )
            else:  # Large file - Telethon
                await self.uploader.upload_large_video(
                    query.message.chat_id,
                    result['path'],
                    f"✅ {result['title']}\n📁 {result['size_mb']:.1f} MB",
                    result['duration']
                )
            
            await query.message.edit_text(
                "✅ Upload complete!",
                parse_mode=ParseMode.MARKDOWN
            )
            
        except Exception as e:
            await query.message.edit_text(
                f"❌ Upload failed: {str(e)}",
                parse_mode=ParseMode.MARKDOWN
            )
        
        # Cleanup
        if user_id in active_downloads:
            del active_downloads[user_id]
        
        try:
            os.remove(result['path'])
        except:
            pass
    
    async def status_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Status command"""
        if not active_downloads:
            await update.message.reply_text("ℹ️ Koi active download nahi hai.")
            return
        
        status_text = "**Active Downloads:**\n\n"
        for user_id, info in active_downloads.items():
            status_text += f"• User: {user_id}\n"
            status_text += f"  Quality: {info['quality']}\n"
            status_text += f"  Status: {info['status']}\n\n"
        
        await update.message.reply_text(status_text, parse_mode=ParseMode.MARKDOWN)
    
    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Button callback"""
        query = update.callback_query
        await query.answer()
        
        if query.data == 'help':
            await self.help_cmd(update, context)
        elif query.data == 'about':
            await query.message.reply_text(
                "**ℹ️ About**\n\n"
                "Video Download Bot\n"
                "Version: 2.0.0\n\n"
                "Tech: Python, yt-dlp, FFmpeg, Telethon",
                parse_mode=ParseMode.MARKDOWN
            )
    
    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Error handler"""
        logger.error(f"Error: {context.error}")
        try:
            if update and update.effective_message:
                await update.effective_message.reply_text(
                    "❌ Kuch error aa gaya! Dobara try karo.",
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
        app.add_handler(CommandHandler("status", self.status_cmd))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_url))
        app.add_handler(CallbackQueryHandler(self.quality_callback, pattern='^q_'))
        app.add_handler(CallbackQueryHandler(self.button_callback))
        app.add_error_handler(self.error_handler)
        
        # Start
        logger.info("Bot starting...")
        app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    bot = Bot()
    bot.run()
