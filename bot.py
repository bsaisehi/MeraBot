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

# Headers for Classplus/Akamai - IMPORTANT: Ye headers download ke time use honge
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

# Telethon client
telethon_client = None

class VideoDownloader:
    """Video download karein with proper headers"""
    
    @staticmethod
    async def download_video(url, quality="720", progress_callback=None):
        """Video download karein"""
        try:
            # Temporary file create
            temp_dir = tempfile.mkdtemp()
            output_template = os.path.join(temp_dir, 'video.%(ext)s')
            
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
                'socket_timeout': 30,
                'extractor_args': {
                    'generic': {
                        'http_headers': HEADERS,
                    }
                },
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                
                # Find video file
                video_path = None
                for file in os.listdir(temp_dir):
                    if file.endswith(('.mp4', '.mkv', '.webm', '.ts')):
                        video_path = os.path.join(temp_dir, file)
                        break
                
                if not video_path:
                    # Try prepare_filename
                    video_path = ydl.prepare_filename(info)
                    if not os.path.exists(video_path):
                        base = os.path.splitext(video_path)[0]
                        for ext in ['.mp4', '.mkv', '.webm']:
                            if os.path.exists(base + ext):
                                video_path = base + ext
                                break
                
                if not video_path or not os.path.exists(video_path):
                    return {
                        'success': False,
                        'error': 'Downloaded file not found'
                    }
                
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
            logger.error(f"Download error: {e}")
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
                    asyncio.create_task(progress_callback(percent, speed_mb, eta))
            except:
                pass
        elif d['status'] == 'finished':
            if progress_callback:
                try:
                    asyncio.create_task(progress_callback(100, 0, 0))
                except:
                    pass

class DirectUploader:
    """Upload directly"""
    
    @staticmethod
    async def init_client():
        """Telethon client"""
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
        """Upload video"""
        try:
            file_size = os.path.getsize(video_path)
            file_size_mb = file_size / (1024 * 1024)
            
            logger.info(f"Uploading {file_size_mb:.1f} MB file...")
            
            # 50MB se chota - Bot API
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
            
            # 50MB se bada - Telethon
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
        self.downloader = VideoDownloader()
        self.uploader = DirectUploader()
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start command"""
        welcome = (
            "🎬 **Video Download Bot**\n\n"
            "Main videos directly download karke Telegram par upload karta hoon.\n\n"
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
            "**Note:**\n"
            "Fresh URL use karo\n"
            "Token expire ho jata hai"
        )
        
        await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)
    
    async def handle_url(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """URL handler - No validation, direct download"""
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
        
        # URL validate - bas http check
        if not url.startswith(('http://', 'https://')):
            await update.message.reply_text(
                "❌ Invalid URL!\nhttp:// ya https:// se start ho.",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        # Quality selection buttons - Direct quality options
        keyboard = [
            [InlineKeyboardButton("360p", callback_data=f'q_360_{user_id}'),
             InlineKeyboardButton("480p", callback_data=f'q_480_{user_id}')],
            [InlineKeyboardButton("720p", callback_data=f'q_720_{user_id}'),
             InlineKeyboardButton("1080p", callback_data=f'q_1080_{user_id}')],
            [InlineKeyboardButton("Best Quality", callback_data=f'q_best_{user_id}')]
        ]
        
        context.user_data['pending_url'] = url
        
        await update.message.reply_text(
            "🔗 URL mil gaya!\n\n"
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
            if user_id in active_downloads:
                del active_downloads[user_id]
            await query.message.edit_text(
                f"❌ Download failed!\n\n"
                f"Error: {result['error']}\n\n"
                f"Possible issues:\n"
                f"• Token expire ho gaya\n"
                f"• URL invalid hai\n"
                f"• Fresh URL try karo",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        # Upload
        await query.message.edit_text(
            f"📤 Uploading...\n"
            f"Size: {result['size_mb']:.1f} MB",
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
                    "✅ Video successfully upload ho gaya!",
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
        
        # Cleanup
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
