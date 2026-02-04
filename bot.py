import os
import logging
import json
import asyncio
import time
from pyrogram import Client, filters, enums
from google.oauth2.credentials import Credentials
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
TOKEN_JSON = os.environ.get('TOKEN_JSON') # <--- CHANGED THIS
DRIVE_FOLDER_ID = os.environ.get('DRIVE_FOLDER_ID')

# --- HELPERS ---
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
    if (now - getattr(message, "last_update_time", 0)) > 5 or current == total:
        elapsed_time = now - start_time
        if elapsed_time == 0: elapsed_time = 0.1
        speed = current / elapsed_time
        percentage = current * 100 / total
        time_to_completion = (total - current) / speed if speed > 0 else 0
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
            pass

# --- GOOGLE DRIVE STUFF (UPDATED FOR USER AUTH) ---
def get_drive_service():
    try:
        # Load the USER TOKEN instead of Service Account
        token_info = json.loads(TOKEN_JSON)
        creds = Credentials.from_authorized_user_info(token_info)
        return build('drive', 'v3', credentials=creds)
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
app = Client("bot_session", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

@app.on_message(filters.command("start"))
async def start(client, message):
    await message.reply_text("👋 **Authenticated & Ready!**\nSend me files.")

@app.on_message(filters.media)
async def handle_media(client, message):
    media = getattr(message, message.media.value)
    file_name = getattr(media, "file_name", None) or f"{message.media.value}_{message.id}"
    file_name = "".join([c for c in file_name if c.isalpha() or c.isdigit() or c in "._- "]).strip()

    status_msg = await message.reply_text(f"⏳ **Starting...**\n`{file_name}`")
    status_msg.last_update_time = 0
    start_time = time.time()
    local_path = f"downloads/{file_name}"

    try:
        await message.download(
            file_name=local_path,
            progress=progress_callback,
            progress_args=(status_msg, start_time, file_name)
        )
        
        await status_msg.edit_text(f"☁️ **Uploading as YOU...**\n📄 `{file_name}`")
        
        mime_type = getattr(media, "mime_type", "application/octet-stream")
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, upload_to_drive, local_path, file_name, mime_type)

        if result:
            size_fmt = human_readable_size(int(result.get('size', 0)))
            await status_msg.edit_text(
                f"✅ **Done.**\n📄 `{file_name}`\n💾 Size: {size_fmt}\n🔗 [Open in Drive]({result.get('webViewLink')})",
                disable_web_page_preview=True
            )
        else:
            await status_msg.edit_text("❌ Upload failed. Check Railway logs.")

    except Exception as e:
        await status_msg.edit_text(f"❌ Error: {str(e)}")
    finally:
        if os.path.exists(local_path): os.remove(local_path)

if __name__ == '__main__':
    if not os.path.exists("downloads"): os.makedirs("downloads")
    app.run()
