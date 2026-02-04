import os
import logging
import json
import asyncio
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

# Initialize Google Drive
def get_drive_service():
    """Create Google Drive service"""
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
    """Upload file to Google Drive"""
    try:
        service = get_drive_service()
        if not service:
            return None
        
        file_metadata = {
            'name': file_name,
            'parents': [DRIVE_FOLDER_ID]
        }
        
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

# Initialize Pyrogram Client
# We use "bot_session" for the session file name
app = Client(
    "bot_session",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

@app.on_message(filters.command("start"))
async def start(client, message):
    user_name = message.from_user.first_name
    await message.reply_text(
        f"👋 Hi {user_name}! Welcome to *Google Drive Upload Bot* (Pyrogram Edition)\n\n"
        "📤 Send me any file and I'll upload it to your Google Drive!\n\n"
        "📊 *Limits:*\n"
        "• Max file size: 2GB (Standard) / 4GB (Premium)\n\n"
        "📁 Files are saved to your configured Drive folder",
        parse_mode=enums.ParseMode.MARKDOWN
    )

@app.on_message(filters.command("stats"))
async def stats(client, message):
    status_msg = await message.reply_text("🔄 Fetching stats...")
    try:
        service = get_drive_service()
        if not service:
            await status_msg.edit_text("❌ Could not connect to Google Drive")
            return
        
        # Get files in folder
        results = service.files().list(
            q=f"'{DRIVE_FOLDER_ID}' in parents and trashed=false",
            fields="files(name, size)"
        ).execute()
        
        files = results.get('files', [])
        total_files = len(files)
        total_size = sum(int(f.get('size', 0)) for f in files)
        
        size_gb = total_size / (1024 ** 3)
        
        await status_msg.edit_text(
            f"📊 *Upload Statistics*\n\n"
            f"📁 Total files: {total_files}\n"
            f"💾 Total size: {size_gb:.2f} GB",
            parse_mode=enums.ParseMode.MARKDOWN
        )
    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        await status_msg.edit_text("❌ Could not fetch statistics")

# Handle all media types: Documents, Photos, Videos, Audio, Voice
@app.on_message(filters.media)
async def handle_media(client, message):
    # Determine file info based on media type
    media = getattr(message, message.media.value)
    
    # Filename fallback
    file_name = getattr(media, "file_name", None)
    if not file_name:
        # Generate generic name if none exists (e.g. for photos)
        ext = ""
        if message.photo: ext = ".jpg"
        elif message.voice: ext = ".ogg"
        elif message.video: ext = ".mp4"
        elif message.audio: ext = ".mp3"
        file_name = f"{message.media.value}_{message.id}{ext}"

    file_size = getattr(media, "file_size", 0)
    size_mb = file_size / (1024 * 1024)

    # Progress callback function
    async def progress(current, total):
        # Update progress every 5MB or so to avoid flooding API
        if (current // (1024 * 1024)) % 5 == 0 and current != total:
             # You can add a visual progress bar here if you want, 
             # but keeping it simple prevents "Message Not Modified" errors
             pass

    status_msg = await message.reply_text(
        f"📥 *Downloading...*\n"
        f"📄 Name: `{file_name}`\n"
        f"📊 Size: {size_mb:.2f} MB",
        parse_mode=enums.ParseMode.MARKDOWN
    )

    local_path = f"downloads/{file_name}"
    
    try:
        # Pyrogram Download
        download_path = await message.download(
            file_name=local_path,
            progress=progress
        )
        
        await status_msg.edit_text(
            f"☁️ *Uploading to Google Drive...*\n"
            f"📄 Name: `{file_name}`\n"
            f"⏳ Please wait..."
        )

        # Upload to Drive
        # Note: We rely on Google Drive API to detect mime_type automatically 
        # or defaults to application/octet-stream if we pass None, 
        # but your previous code had a helper. We can simplify by letting Drive handle it 
        # or use the mime_type from the message if available.
        mime_type = getattr(media, "mime_type", "application/octet-stream")
        
        # Run the blocking upload in a separate thread to not block the async loop
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, 
            upload_to_drive, 
            download_path, 
            file_name, 
            mime_type
        )

        if result:
            await status_msg.edit_text(
                f"✅ *Upload Successful!*\n\n"
                f"📄 Name: `{file_name}`\n"
                f"🔗 [View in Google Drive]({result.get('webViewLink')})",
                parse_mode=enums.ParseMode.MARKDOWN,
                disable_web_page_preview=True
            )
        else:
            await status_msg.edit_text("❌ Upload failed check logs.")

    except Exception as e:
        logger.error(f"Error: {e}")
        await status_msg.edit_text(f"❌ Error: {str(e)}")
    
    finally:
        # Cleanup
        if os.path.exists(local_path):
            os.remove(local_path)

if __name__ == '__main__':
    # Create downloads directory if not exists
    if not os.path.exists("downloads"):
        os.makedirs("downloads")
    
    print("🚀 Bot Started (Pyrogram)")
    app.run()
