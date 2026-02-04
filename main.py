import os
import logging
import asyncio
import shutil
import uuid
import re
from typing import List, Union
from dotenv import load_dotenv

from telegram import Update, InputMediaPhoto, InputMediaVideo
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Third-party libraries
import instaloader
from tiktok_downloader import snaptik

# Load environment variables
load_dotenv()

# Configuration
BOT_TOKEN = os.getenv('BOT_TOKEN')
BOT_OWNER_ID = os.getenv('BOT_OWNER_ID')

# Logging Configuration
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class MediaHandler:
    def __init__(self):
        self.base_temp_dir = "downloads"
        if not os.path.exists(self.base_temp_dir):
            os.makedirs(self.base_temp_dir)
            
        self.instagram = instaloader.Instaloader(
            download_pictures=True,
            download_videos=True,
            download_video_thumbnails=False,
            compress_json=False,
            save_metadata=False,
            quiet=True
        )

    def create_session_dir(self):
        """Creates a unique directory for a single download session."""
        session_id = str(uuid.uuid4())
        path = os.path.join(self.base_temp_dir, session_id)
        os.makedirs(path, exist_ok=True)
        return path

    def cleanup(self, folder_path: str):
        """Removes the session directory and all its contents."""
        try:
            if os.path.exists(folder_path):
                shutil.rmtree(folder_path)
                logger.info(f"Cleaned up session: {folder_path}")
        except Exception as e:
            logger.error(f"Error cleaning up {folder_path}: {e}")

    async def process_url(self, url: str) -> tuple[List[str], str]:
        """
        Processes the URL and returns a list of file paths and the session directory.
        Returns: (list_of_files, session_directory_path)
        """
        session_dir = self.create_session_dir()
        files = []

        try:
            if "instagram.com" in url:
                files = await self.handle_instagram(url, session_dir)
            elif "tiktok.com" in url:
                files = await self.handle_tiktok(url, session_dir)
        except Exception as e:
            logger.error(f"Processing error: {e}")
            self.cleanup(session_dir) # Cleanup immediately on error
            return [], ""

        return files, session_dir

    async def handle_tiktok(self, url: str, save_dir: str) -> List[str]:
        """Handles TikTok downloads using snaptik."""
        try:
            logger.info(f"Processing TikTok URL: {url}")
            # Running synchronous download in a thread to avoid blocking asyncio loop
            loop = asyncio.get_event_loop()
            
            def download_task():
                d = snaptik(url)
                if d and len(d) > 0:
                    filename = os.path.join(save_dir, f'tiktok_{uuid.uuid4().hex[:8]}.mp4')
                    d[0].download(filename)
                    return [filename]
                return []

            return await loop.run_in_executor(None, download_task)
            
        except Exception as e:
            logger.error(f"TikTok Error: {e}", exc_info=True)
            return []

    async def handle_instagram(self, url: str, save_dir: str) -> List[str]:
        """Handles Instagram downloads using Instaloader."""
        try:
            logger.info(f"Processing Instagram URL: {url}")
            shortcode = self.extract_instagram_shortcode(url)
            if not shortcode:
                return []

            # Running synchronous instaloader in a thread
            loop = asyncio.get_event_loop()

            def download_task():
                post = instaloader.Post.from_shortcode(self.instagram.context, shortcode)
                # Instaloader downloads to a folder named by target, we need to move files or point target to save_dir
                # Changing target to our session dir
                self.instagram.download_post(post, target=save_dir)
                
                downloaded_files = []
                # Walk through directory to find media
                for root, _, files in os.walk(save_dir):
                    for file in files:
                        if file.endswith(('.jpg', '.mp4', '.jpeg', '.png')):
                            downloaded_files.append(os.path.join(root, file))
                return downloaded_files

            return await loop.run_in_executor(None, download_task)

        except Exception as e:
            logger.error(f"Instagram Error: {e}", exc_info=True)
            return []

    @staticmethod
    def extract_instagram_shortcode(url: str) -> Union[str, None]:
        patterns = [
            r'instagram\.com/(?:[^/]+/)?p/([^/?]+)',
            r'instagram\.com/(?:[^/]+/)?v/([^/?]+)',
            r'instagram\.com/(?:[^/]+/)?reels/([^/?]+)',
            r'instagram\.com/(?:[^/]+/)?reel/([^/?]+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

class TelegramBot:
    def __init__(self, token: str):
        self.media_handler = MediaHandler()
        self.application = Application.builder().token(token).build()

    async def notify_owner(self, user, message_text):
        """Sends a notification to the bot owner about usage."""
        if BOT_OWNER_ID:
            try:
                username = f"@{user.username}" if user.username else "No Username"
                text = (
                    f"🔔 <b>New Activity</b>\n"
                    f"👤 <b>User:</b> {user.first_name} ({username})\n"
                    f"ID: <code>{user.id}</code>\n"
                    f"🔗 <b>Link:</b> {message_text}"
                )
                await self.application.bot.send_message(
                    chat_id=BOT_OWNER_ID, 
                    text=text, 
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Failed to notify owner: {e}")

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        await update.message.reply_text(
            f"👋 <b>Hello, {user.first_name}!</b>\n\n"
            "Send me a link from <b>Instagram</b> or <b>TikTok</b>, "
            "and I will download the video/photos for you! 📸🎥\n\n"
            "<i>Waiting for your link...</i>",
            parse_mode="HTML"
        )

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        message_text = update.message.text
        if not message_text:
            return

        # Simple URL validation
        if "instagram.com" not in message_text and "tiktok.com" not in message_text:
            await update.message.reply_text("⚠️ Please send a valid <b>Instagram</b> or <b>TikTok</b> link.", parse_mode="HTML")
            return

        # Notify owner
        await self.notify_owner(update.effective_user, message_text)

        status_msg = await update.message.reply_text("⏳ <b>Processing...</b>\n<i>Downloading media from source.</i>", parse_mode="HTML")

        # Process URL
        files, session_dir = await self.media_handler.process_url(message_text)

        if not files:
            await status_msg.edit_text("❌ <b>Error!</b>\nCould not download media. Please check the link or try again later.")
            if session_dir:
                self.media_handler.cleanup(session_dir)
            return

        try:
            await status_msg.edit_text("⬆️ <b>Uploading...</b>\n<i>Sending files to Telegram.</i>", parse_mode="HTML")
            
            # Send files (handling limits)
            # Splitting into chunks of 10 for MediaGroup limits
            for i in range(0, len(files), 10):
                chunk = files[i:i + 10]
                media_group = []
                for file_path in chunk:
                    with open(file_path, 'rb') as f:
                        if file_path.endswith('.mp4'):
                            media_group.append(InputMediaVideo(media=f))
                        else:
                            media_group.append(InputMediaPhoto(media=f))
                
                if media_group:
                    await update.message.reply_media_group(media=media_group)

            await update.message.reply_text("✅ <b>Done!</b>\nSend another link to download.", parse_mode="HTML")
            
            # Delete status message to keep chat clean
            await status_msg.delete()

        except Exception as e:
            logger.error(f"Upload error: {e}")
            await update.message.reply_text("❌ An error occurred while uploading the files.")
        
        finally:
            # Always cleanup files
            if session_dir:
                self.media_handler.cleanup(session_dir)

    def run(self):
        logger.info("Bot is starting...")
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        self.application.run_polling()

if __name__ == "__main__":
    if not BOT_TOKEN:
        print("Error: BOT_TOKEN is missing in .env file")
        exit(1)
    
    bot = TelegramBot(BOT_TOKEN)
    bot.run()
