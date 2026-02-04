import os
import logging
import json
import asyncio
import time
import math
from pyrogram import Client, filters, enums
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Environment variables
API_ID = os.environ.get('API_ID') 
API_HASH = os.environ.get('API_HASH')
BOT_TOKEN = os.environ.get('TELEGRAM_TOKEN')
GOOGLE_CREDENTIALS = os.environ.get('GOOGLE_CREDENTIALS')
DRIVE_FOLDER_ID = os.environ.get('DRIVE_FOLDER_ID')

# --- HELPERS FOR THE PROGRESS BAR ---

def human_readable_size(size, decimal_places=2):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024.0:
            return f"{size:.{decimal_places}f} {unit}"
        size /= 1024.0
    return f"{size:.{decimal_places}f} PB"

def get_progress_bar_string(current, total):
    filled_length = int(10 * current // total)
    return '■' * filled_length + '□' * (10 - filled_length)

async def progress_callback(current, total, message, start_time, file_name):
    now = time.time()
    # Only update every 5 seconds to avoid FloodWait, or when finished
    if (now - getattr(message, "last_update_time", 0)) > 5 or current == total:
        elapsed_time = now - start_time
        if elapsed_time == 0: elapsed_time = 0.1 # Avoid div by zero
        
        speed = current / elapsed_time
        percentage = current * 100 / total
        time_to_completion = (total - current) / speed if speed > 0 else 0
        
        # Format time strings
        eta_str = time.strftime("%M:%S", time.gmtime(time_to_completion))
        
        progress_str = (
            f"📥 **Downloading...**\n"
            f"📄 `{file_name}`\n"
            f"[{get_progress_bar_string(current, total)}] {percentage:.1f}%\n"
            f"⚡ {human_readable_size(speed)}/s | ⏱ ETA: {eta_str}\n"
            f"💾 {human_readable_size(current)} / {human_readable_size(total)}"
        )
        
        try:
            await message.edit_text(progress_str)
            message.last_update_time = now
        except Exception:
            pass # Ignore "Message Not Modified" errors

# --- GOOGLE DRIVE STUFF ---

def get_drive_service():
    try:
        credentials_dict = json.loads(GOOGLE_CREDENTIALS)
        credentials = service_account.Credentials.from_service_account_info(
            credentials_dict,
            scopes=['https://www.googleapis.com/auth/drive.file']
        )
        return build('drive', 'v3', credentials=credentials)
    except Exception as e:
        logger.error(f"Error creating Drive service: {e}")
        return None

def upload_to_drive(file_path, file_name, mime_type='application/octet-stream'):
    try:
        service = get_drive_service()
        if not service: return None
        
        file_metadata = {'name': file_name, 'parents': [DRIVE_FOLDER_ID]}
        media = MediaFileUpload(file_path, mimetype=mime_type, resumable=True)
        
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, name, webViewLink, size'
        ).execute()
        return file
    except Exception as e:
        logger.error(f"Error uploading to Drive: {e}")
        return None

# --- BOT CODE ---

app = Client(
    "bot_session",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

@app.on_message(filters.command("start"))
async def start(client, message):
    await message.reply_text(
        f"👋 **Yo {message.from_user.first_name}!**\n\n"
        "I'm ready. Send me files (Video, Audio, Docs).\n"
        "I'll handle the heavy lifting (up to 2GB).",
        parse_mode=enums.ParseMode.MARKDOWN
    )

@app.on_message(filters.media)
async def handle_media(client, message):
    # 1. Get File Info
    media = getattr(message, message.media.value)
    file_name = getattr(media, "file_name", None)
    
    # Fallback names for nameless files
    if not file_name:
        ext = ""
        if message.photo: ext = ".jpg"
        elif message.voice: ext = ".ogg"
        elif message.video: ext = ".mp4"
        elif message.audio: ext = ".mp3"
        file_name = f"{message.media.value}_{message.id}{ext}"
    
    # Sanitize filename (remove weird chars that break Linux)
    file_name = "".join([c for c in file_name if c.isalpha() or c.isdigit() or c in "._- "]).strip()

    # 2. Initial Message
    status_msg = await message.reply_text(
        f"⏳ **Starting...**\n`{file_name}`", 
        parse_mode=enums.ParseMode.MARKDOWN
    )
    status_msg.last_update_time = 0 # Initialize timer
    start_time = time.time()
    local_path = f"downloads/{file_name}"

    try:
        # 3. DOWNLOAD (Telegram -> VPS)
        await message.download(
            file_name=local_path,
            progress=progress_callback,
            progress_args=(status_msg, start_time, file_name)
        )
        
        # 4. UPLOAD (VPS -> Drive)
        await status_msg.edit_text(
            f"☁️ **Uploading to Drive...**\n"
            f"📄 `{file_name}`\n"
            f"⚠️ This can take a min. Don't touch me."
        )

        mime_type = getattr(media, "mime_type", "application/octet-stream")
        
        # Run sync upload in thread
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, upload_to_drive, local_path, file_name, mime_type
        )

        # 5. RESULT
        if result:
            size_fmt = human_readable_size(int(result.get('size', 0)))
            await status_msg.edit_text(
                f"✅ **Done.**\n\n"
                f"📄 `{file_name}`\n"
                f"💾 Size: {size_fmt}\n"
                f"🔗 [Open in Drive]({result.get('webViewLink')})",
                parse_mode=enums.ParseMode.MARKDOWN,
                disable_web_page_preview=True
            )
        else:
            await status_msg.edit_text("❌ Drive rejected the upload. Check logs.")

    except Exception as e:
        logger.error(f"Error: {e}")
        await status_msg.edit_text(f"❌ **Error:** {str(e)}")
    
    finally:
        # 6. CLEANUP (Delete local file)
        if os.path.exists(local_path):
            os.remove(local_path)

if __name__ == '__main__':
    if not os.path.exists("downloads"):
        os.makedirs("downloads")
    print("🚀 Bot Started with QOL Upgrade")
    app.run()
