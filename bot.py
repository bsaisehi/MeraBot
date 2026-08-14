#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import asyncio
import logging
import tempfile
import subprocess
import shutil
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse, parse_qs, quote
import requests
import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram.constants import ParseMode
from telethon import TelegramClient
from telethon.tl.types import DocumentAttributeVideo, DocumentAttributeFilename
from concurrent.futures import ThreadPoolExecutor
import threading

# Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Config
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")

# Headers - Critical for Classplus
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': '*/*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Origin': 'https://classplusapp.com',
    'Referer': 'https://classplusapp.com/',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'cross-site',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
}

# Active downloads - Multiple parallel downloads
active_downloads = {}
MAX_PARALLEL = 3  # Max 3 videos at once

# Telethon client
telethon_client = None
client_lock = threading.Lock()

class VideoDownloader:
    """Video download with proper encoding and headers"""
    
    @staticmethod
    def encode_url(url):
        """URL properly encode karein"""
        # URL ko parse karein
        parsed = urlparse(url)
        
        # Query parameters ko properly encode karein
        if parsed.query:
            # Original query string preserve karein
            return url
        return url
    
    @staticmethod
    async def download_video(url, quality="720", progress_callback=None):
        """Video download karein with proper headers"""
        try:
            # Temporary directory for download
            temp_dir = tempfile.mkdtemp(prefix='video_download_')
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
                'nocheckcertificate': True,
                'ignoreerrors': False,
                'quiet': True,
                'no_warnings': True,
                'retries': 3,
                'fragment_retries': 3,
                'concurrent_fragment_downloads': 5,
                'socket_timeout': 30,
                'http_headers': HEADERS.copy(),
                'progress_hooks': [lambda d: VideoDownloader.sync_progress_hook(d, progress_callback)] if progress_callback else [],
                'postprocessor_args': {
                    'ffmpeg': ['-c:v', 'libx264', '-c:a', 'aac', '-movflags', '+faststart']
                },
                'external_downloader_args': ['--header', f'Referer: {HEADERS["Referer"]}', '--header', f'Origin: {HEADERS["Origin"]}'],
            }
            
            # URL properly handle karein
            encoded_url = url.strip()
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(encoded_url, download=True)
                
                # Downloaded file find karein
                video_path = None
                for file in os.listdir(temp_dir):
                    file_path = os.path.join(temp_dir, file)
                    if os.path.getsize(file_path) > 0:  # Non-empty file
                        if file.endswith(('.mp4', '.mkv', '.webm', '.ts', '.m4a')):
                            video_path = file_path
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
                
                if not video_path or not os.path.exists(video_path) or os.path.getsize(video_path) == 0:
                    return {
                        'success': False,
                        'error': 'Downloaded file empty or not found'
                    }
                
                # MP4 conversion if needed
                if not video_path.endswith('.mp4'):
                    mp4_path = await VideoDownloader.convert_to_mp4(video_path)
                    if mp4_path:
                        video_path = mp4_path
                
                file_size = os.path.getsize(video_path)
                
                return {
                    'success': True,
                    'path': video_path,
                    'title': info.get('title', 'video'),
                    'duration': info.get('duration', 0),
                    'size': file_size,
                    'size_mb': file_size / (1024 * 1024),
                    'temp_dir': temp_dir
                }
                
        except Exception as e:
            logger.error(f"Download error: {str(e)}")
            return {
                'success': False,
                'error': str(e)[:200]  # Truncate error message
            }
    
    @staticmethod
    def sync_progress_hook(d, progress_callback):
        """Synchronous progress hook - asyncio se handle"""
        if d['status'] == 'downloading':
            try:
                downloaded = d.get('downloaded_bytes', 0)
                total = d.get('total_bytes', 0) or d.get('total_bytes_estimate', 0)
                speed = d.get('speed', 0)
                eta = d.get('eta', 0)
                
                if total and progress_callback:
                    percent = (downloaded / total) * 100
                    speed_mb = speed / (1024 * 1024) if speed else 0
                    # Sync callback - asyncio event loop mein schedule karein
                    asyncio.create_task(progress_callback(percent, speed_mb, eta))
            except:
                pass
        elif d['status'] == 'finished':
            if progress_callback:
                try:
                    asyncio.create_task(progress_callback(100, 0, 0))
                except:
                    pass
    
    @staticmethod
    async def convert_to_mp4(input_path):
        """Video ko MP4 mein convert karein"""
        try:
            output_path = input_path.rsplit('.', 1)[0] + '.mp4'
            
            cmd = [
                'ffmpeg', '-i', input_path,
                '-c:v', 'libx264',
                '-preset', 'fast',
                '-crf', '23',
                '-c:a', 'aac',
                '-b:a', '128k',
                '-movflags', '+faststart',
                '-y',
                output_path
            ]
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            await process.communicate()
            
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                os.remove(input_path)  # Original delete
                return output_path
            
            return input_path
            
        except Exception as e:
            logger.error(f"Conversion error: {e}")
            return input_path

class DirectUploader:
    """Upload with proper handling"""
    
    @staticmethod
    async def init_client():
        """Telethon client initialize - thread safe"""
        global telethon_client
        try:
            async with client_lock:
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
            
            with open(video_path, 'rb') as f:
                await client.send_file(
                    chat_id,
                    f,
                    caption=title,
                    attributes=attributes,
                    supports_streaming=True,
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
        self.executor = ThreadPoolExecutor(max_workers=MAX_PARALLEL)
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start command"""
        welcome = (
            "🎬 **Video Download Bot**\n\n"
            "Main videos download karke MP4 mein convert karke upload karta hoon.\n\n"
            f"**⚡ Features:**\n"
            f"✅ {MAX_PARALLEL} parallel downloads\n"
            f"✅ MP4 conversion\n"
            f"✅ Auto cleanup\n"
            f"✅ 2GB support\n\n"
            f"**📝 Usage:**\n"
            f"1. URL bhejo\n"
            f"2. Quality select karo\n"
            f"3. Video MP4 mein milega!"
        )
        
        await update.message.reply_text(welcome, parse_mode=ParseMode.MARKDOWN)
    
    async def handle_url(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """URL handler - Parallel downloads support"""
        user_id = update.effective_user.id
        url = update.message.text.strip()
        
        # Parallel downloads check
        if len(active_downloads) >= MAX_PARALLEL:
            await update.message.reply_text(
                f"⚠️ Maximum {MAX_PARALLEL} downloads already running!\n"
                "Thodi der baad try karo.",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        # URL basic validate
        if not url.startswith(('http://', 'https://')):
            await update.message.reply_text(
                "❌ Invalid URL!",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        # Quality selection
        keyboard = [
            [InlineKeyboardButton("360p", callback_data=f'q_360_{user_id}'),
             InlineKeyboardButton("480p", callback_data=f'q_480_{user_id}')],
            [InlineKeyboardButton("720p", callback_data=f'q_720_{user_id}'),
             InlineKeyboardButton("1080p", callback_data=f'q_1080_{user_id}')],
            [InlineKeyboardButton("Best Quality", callback_data=f'q_best_{user_id}')]
        ]
        
        context.user_data['pending_url'] = url
        
        await update.message.reply_text(
            "🔗 URL mil gaya!\n\n**Quality select karo:**",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    async def quality_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Quality selection - Download & Upload"""
        query = update.callback_query
        await query.answer()
        
        data = query.data.split('_')
        if len(data) < 3:
            return
        
        quality = data[1]
        user_id = int(data[2])
        
        if user_id != query.from_user.id:
            await query.message.reply_text("❌ Ye aapka download nahi hai!")
            return
        
        url = context.user_data.get('pending_url')
        if not url:
            await query.message.edit_text("❌ URL not found!")
            return
        
        # Download tracking
        download_id = f"{user_id}_{int(datetime.now().timestamp())}"
        active_downloads[download_id] = {
            'user_id': user_id,
            'status': 'downloading',
            'quality': quality
        }
        
        await query.message.edit_text(
            f"⬇️ Downloading ({quality})...\n"
            f"ID: `{download_id}`\n\n"
            f"Download → MP4 Convert → Upload",
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Progress callback
        async def progress_cb(percent, speed, eta):
            try:
                if percent >= 100:
                    await query.message.edit_text(
                        f"🔄 Converting to MP4...\n"
                        f"Download complete!",
                        parse_mode=ParseMode.MARKDOWN
                    )
                    return
                
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
        
        # Download in executor
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                self.executor,
                lambda: asyncio.run(self.downloader.download_video(url, quality, progress_cb))
            )
        except:
            # Direct download if executor fails
            result = await self.downloader.download_video(url, quality, progress_cb)
        
        if not result['success']:
            if download_id in active_downloads:
                del active_downloads[download_id]
            
            error_msg = result.get('error', 'Unknown error')[:150]
            await query.message.edit_text(
                f"❌ **Download Failed!**\n\n"
                f"Error: `{error_msg}`\n\n"
                f"Possible reasons:\n"
                f"• Token expire\n"
                f"• URL invalid\n"
                f"• Try fresh URL",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        # Upload
        await query.message.edit_text(
            f"📤 **Uploading...**\n\n"
            f"Size: {result['size_mb']:.1f} MB\n"
            f"Format: MP4",
            parse_mode=ParseMode.MARKDOWN
        )
        
        try:
            success = await self.uploader.upload_video(
                query.message.chat_id,
                result['path'],
                f"✅ {result['title']}\n📁 {result['size_mb']:.1f} MB\n🎬 MP4",
                result['duration']
            )
            
            if success:
                await query.message.edit_text(
                    "✅ **Upload Complete!**\n\n"
                    f"Video MP4 format mein mil gaya!",
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await query.message.edit_text(
                    "❌ Upload failed!",
                    parse_mode=ParseMode.MARKDOWN
                )
            
        except Exception as e:
            await query.message.edit_text(
                f"❌ Upload error: {str(e)[:100]}",
                parse_mode=ParseMode.MARKDOWN
            )
        
        # Cleanup
        if download_id in active_downloads:
            del active_downloads[download_id]
        
        # Temp files cleanup
        try:
            if 'temp_dir' in result:
                shutil.rmtree(result['temp_dir'], ignore_errors=True)
            elif os.path.exists(result['path']):
                os.remove(result['path'])
            logger.info("Temp files cleaned")
        except:
            pass
    
    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Error handler"""
        logger.error(f"Error: {context.error}")
        try:
            if update and update.effective_message:
                await update.effective_message.reply_text(
                    "❌ Kuch error aa gaya!",
                    parse_mode=ParseMode.MARKDOWN
                )
        except:
            pass
    
    def run(self):
        """Bot run"""
        app = Application.builder().token(BOT_TOKEN).build()
        
        app.add_handler(CommandHandler("start", self.start))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_url))
        app.add_handler(CallbackQueryHandler(self.quality_callback, pattern='^q_'))
        app.add_error_handler(self.error_handler)
        
        logger.info("Bot starting...")
        app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    bot = VideoBot()
    bot.run()
