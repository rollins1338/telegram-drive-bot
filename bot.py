import os
import logging
import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from datetime import datetime
import json

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Environment variables
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
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
        
        logger.info(f"Uploaded {file_name} to Drive")
        return file
    except Exception as e:
        logger.error(f"Error uploading to Drive: {e}")
        return None

def get_mime_type(filename):
    """Determine MIME type from filename"""
    ext = filename.lower().split('.')[-1]
    mime_types = {
        'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
        'gif': 'image/gif', 'webp': 'image/webp',
        'mp4': 'video/mp4', 'avi': 'video/x-msvideo', 'mkv': 'video/x-matroska',
        'mov': 'video/quicktime', 'webm': 'video/webm',
        'mp3': 'audio/mpeg', 'wav': 'audio/wav', 'ogg': 'audio/ogg',
        'pdf': 'application/pdf', 'doc': 'application/msword',
        'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'xls': 'application/vnd.ms-excel',
        'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'zip': 'application/zip', 'rar': 'application/x-rar-compressed',
        '7z': 'application/x-7z-compressed',
        'txt': 'text/plain', 'csv': 'text/csv'
    }
    return mime_types.get(ext, 'application/octet-stream')

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send welcome message"""
    user_name = update.effective_user.first_name
    await update.message.reply_text(
        f"👋 Hi {user_name}! Welcome to *Google Drive Upload Bot*\n\n"
        "📤 Send me any file and I'll upload it to your Google Drive!\n\n"
        "📊 *Limits:*\n"
        "• Max file size: 2GB (Telegram limit)\n"
        "• Supported: All file types\n\n"
        "📁 Files are saved to your *TelegramUploads* folder\n\n"
        "*Commands:*\n"
        "/start - Show this message\n"
        "/help - Get help\n"
        "/stats - View upload statistics",
        parse_mode='Markdown'
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send help message"""
    await update.message.reply_text(
        "📚 *How to use:*\n\n"
        "1️⃣ Send any file to this bot\n"
        "2️⃣ Wait for download & upload confirmation\n"
        "3️⃣ Click the link to view in Google Drive\n\n"
        "💡 *Tips:*\n"
        "• Larger files take longer to upload\n"
        "• You'll get a direct link to each file\n"
        "• All files are private in your Drive\n\n"
        "❓ *Need help?* Contact the bot admin!",
        parse_mode='Markdown'
    )

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send statistics"""
    try:
        service = get_drive_service()
        if not service:
            await update.message.reply_text("❌ Could not connect to Google Drive")
            return
        
        # Get files in folder
        results = service.files().list(
            q=f"'{DRIVE_FOLDER_ID}' in parents and trashed=false",
            fields="files(name, size, createdTime)"
        ).execute()
        
        files = results.get('files', [])
        total_files = len(files)
        total_size = sum(int(f.get('size', 0)) for f in files)
        
        size_mb = total_size / (1024 * 1024)
        size_gb = total_size / (1024 * 1024 * 1024)
        
        await update.message.reply_text(
            f"📊 *Upload Statistics*\n\n"
            f"📁 Total files: {total_files}\n"
            f"💾 Total size: {size_gb:.2f} GB ({size_mb:.1f} MB)\n"
            f"📂 Location: TelegramUploads folder",
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        await update.message.reply_text("❌ Could not fetch statistics")

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE, file_obj, file_name, file_type="file"):
    """Generic file handler"""
    try:
        file_size = file_obj.file_size if hasattr(file_obj, 'file_size') else 0
        
        # Check file size (2GB limit)
        if file_size > 2147483648:
            await update.message.reply_text(
                "❌ *File too large!*\n\n"
                "Maximum size: 2GB\n"
                f"Your file: {file_size / (1024**3):.2f} GB",
                parse_mode='Markdown'
            )
            return
        
        size_mb = file_size / (1024 * 1024)
        
        # Notify user
        progress_msg = await update.message.reply_text(
            f"📥 *Downloading {file_type}...*\n"
            f"📄 Name: `{file_name}`\n"
            f"📊 Size: {size_mb:.2f} MB",
            parse_mode='Markdown'
        )
        
        # Download from Telegram
        file = await context.bot.get_file(file_obj.file_id)
        local_path = f"/tmp/{file_name}"
        
        try:
            await file.download_to_drive(local_path)
        except Exception as e:
            await progress_msg.edit_text(
                f"❌ *Download failed!*\n\n"
                f"Error: {str(e)}",
                parse_mode='Markdown'
            )
            return
        
        # Update progress
        await progress_msg.edit_text(
            f"☁️ *Uploading to Google Drive...*\n"
            f"📄 Name: `{file_name}`\n"
            f"📊 Size: {size_mb:.2f} MB\n\n"
            f"⏳ Please wait...",
            parse_mode='Markdown'
        )
        
        # Upload to Drive
        mime_type = get_mime_type(file_name)
        result = upload_to_drive(local_path, file_name, mime_type)
        
        # Clean up
        if os.path.exists(local_path):
            os.remove(local_path)
        
        if result:
            await progress_msg.edit_text(
                f"✅ *Upload Successful!*\n\n"
                f"📄 Name: `{file_name}`\n"
                f"📊 Size: {size_mb:.2f} MB\n"
                f"🔗 [View in Google Drive]({result.get('webViewLink')})\n\n"
                f"_File saved to TelegramUploads folder_",
                parse_mode='Markdown',
                disable_web_page_preview=True
            )
            logger.info(f"Successfully uploaded {file_name} ({size_mb:.2f} MB)")
        else:
            await progress_msg.edit_text(
                "❌ *Upload failed!*\n\n"
                "Please try again or check bot permissions.\n"
                "Contact admin if the problem persists.",
                parse_mode='Markdown'
            )
            
    except Exception as e:
        logger.error(f"Error handling {file_type}: {e}")
        await update.message.reply_text(
            f"❌ *Error occurred!*\n\n"
            f"Type: {file_type}\n"
            f"Error: {str(e)}\n\n"
            "Please try again.",
            parse_mode='Markdown'
        )

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle document uploads"""
    document = update.message.document
    await handle_file(update, context, document, document.file_name, "document")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle photo uploads"""
    photo = update.message.photo[-1]  # Highest quality
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_name = f"photo_{timestamp}_{photo.file_unique_id}.jpg"
    await handle_file(update, context, photo, file_name, "photo")

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle video uploads"""
    video = update.message.video
    file_name = video.file_name or f"video_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
    await handle_file(update, context, video, file_name, "video")

async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle audio uploads"""
    audio = update.message.audio
    file_name = audio.file_name or f"audio_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp3"
    await handle_file(update, context, audio, file_name, "audio")

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle voice messages"""
    voice = update.message.voice
    file_name = f"voice_{datetime.now().strftime('%Y%m%d_%H%M%S')}.ogg"
    await handle_file(update, context, voice, file_name, "voice message")

def main():
    """Start the bot"""
    if not TELEGRAM_TOKEN:
        logger.error("❌ TELEGRAM_TOKEN not set!")
        return
    
    if not GOOGLE_CREDENTIALS:
        logger.error("❌ GOOGLE_CREDENTIALS not set!")
        return
        
    if not DRIVE_FOLDER_ID:
        logger.error("❌ DRIVE_FOLDER_ID not set!")
        return
    
    logger.info("🚀 Starting bot...")
    
    # Create application
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.VIDEO, handle_video))
    application.add_handler(MessageHandler(filters.AUDIO, handle_audio))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice))
    
    # Run bot
    logger.info("✅ Bot started successfully!")
    logger.info("🤖 Waiting for messages...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()