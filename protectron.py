import os
import datetime
import logging
from collections import defaultdict, deque
from pyrogram import Client, filters
from pyrogram.types import Message
from pymongo import MongoClient
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pymongo.errors import PyMongoError

# Load environment variables from .env file
load_dotenv()

API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URL")
BOT_OWNER_ID = int(os.getenv("BOT_OWNER_ID"))

# Initialize logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Pyrogram Client
app = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Initialize MongoDB Client
mongo_client = MongoClient(MONGO_URL)
db = mongo_client['bot_database']  # Database name
approved_chats_collection = db['approved_chats']  # Collection for approved chat IDs
messages_collection = db['messages']  # Collection for messages
approved_users_collection = db['approved_users']  # Collection for approved users

# Initialize APScheduler
scheduler = AsyncIOScheduler()

# Track deletions per user in each chat
deletion_tracker = defaultdict(lambda: defaultdict(lambda: deque(maxlen=3)))

def track_deletion(chat_id, user_id):
    now = datetime.datetime.utcnow()
    deletion_tracker[chat_id][user_id].append(now)
    return len(deletion_tracker[chat_id][user_id])

async def notify_user(client, chat_id, user_id):
    user = await client.get_users(user_id)
    user_link = f"[{user.first_name}](tg://user?id={user_id})"
    try:
        await client.send_message(
            chat_id,
            f"Hey {user_link}, I have removed your messages because of security guidelines. "
            f"You can ask my admins to approve you so that I won't delete your messages."
        )
        logger.info(f"Notification sent to user {user_id} in chat {chat_id}.")
    except Exception as e:
        logger.error(f"Error sending notification to user {user_id} in chat {chat_id}: {e}")

@app.on_message(filters.command("approve") & filters.user(BOT_OWNER_ID))
async def approve_chat(client, message: Message):
    chat_id = message.command[1]
    try:
        if not approved_chats_collection.find_one({"chat_id": chat_id}):
            approved_chats_collection.insert_one({"chat_id": chat_id})
            await message.reply(f"Chat ID {chat_id} has been approved.")
        else:
            await message.reply(f"Chat ID {chat_id} is already approved.")
        logger.info(f"Chat ID {chat_id} approved by {message.from_user.id}.")
    except PyMongoError as e:
        logger.error(f"Error approving chat ID {chat_id}: {e}")
        await message.reply("An error occurred while approving the chat ID.")

@app.on_message(filters.command("approveuser") & filters.user(BOT_OWNER_ID))
async def approve_user(client, message: Message):
    user_id = int(message.command[1])
    try:
        if not approved_users_collection.find_one({"user_id": user_id}):
            approved_users_collection.insert_one({"user_id": user_id})
            await message.reply(f"User ID {user_id} has been approved.")
        else:
            await message.reply(f"User ID {user_id} is already approved.")
        logger.info(f"User ID {user_id} approved by {message.from_user.id}.")
    except PyMongoError as e:
        logger.error(f"Error approving user ID {user_id}: {e}")
        await message.reply("An error occurred while approving the user ID.")

@app.on_message(filters.group)
async def save_message(client, message: Message):
    chat_id = str(message.chat.id)
    user_id = message.from_user.id
    if approved_chats_collection.find_one({"chat_id": chat_id}):
        text = message.text or message.caption
        if text:
            words_count = len(text.split())
            if words_count > 30 and user_id != BOT_OWNER_ID and not approved_users_collection.find_one({"user_id": user_id}):
                await message.delete()
                logger.info(f"Message deleted from user {user_id} in chat {chat_id} due to word limit.")
                if track_deletion(chat_id, user_id) == 3:
                    await notify_user(client, chat_id, user_id)
            else:
                try:
                    messages_collection.insert_one({
                        "message_id": message.id,
                        "chat_id": chat_id,
                        "user_id": user_id,
                        "text": text,
                        "date": message.date
                    })
                    logger.info(f"Message from user {user_id} in chat {chat_id} saved to database.")
                except PyMongoError as e:
                    logger.error(f"Error saving message from user {user_id} in chat {chat_id}: {e}")

@app.on_edited_message(filters.group)
async def edit_message(client, message: Message):
    chat_id = str(message.chat.id)
    user_id = message.from_user.id
    if approved_chats_collection.find_one({"chat_id": chat_id}):
        text = message.text or message.caption
        if text:
            words_count = len(text.split())
            if words_count > 30 and user_id != BOT_OWNER_ID and not approved_users_collection.find_one({"user_id": user_id}):
                await message.delete()
                logger.info(f"Edited message deleted from user {user_id} in chat {chat_id} due to word limit.")
                if track_deletion(chat_id, user_id) == 3:
                    await notify_user(client, chat_id, user_id)
            else:
                try:
                    messages_collection.update_one(
                        {"message_id": message.id, "chat_id": chat_id},
                        {"$set": {"text": text, "edited_date": message.edit_date}}
                    )
                    logger.info(f"Edited message from user {user_id} in chat {chat_id} updated in database.")
                except PyMongoError as e:
                    logger.error(f"Error updating edited message from user {user_id} in chat {chat_id}: {e}")

def delete_old_messages():
    try:
        # Get the datetime 48 hours ago from now
        cutoff_date = datetime.datetime.utcnow() - datetime.timedelta(hours=48)
        # Delete messages older than 48 hours
        result = messages_collection.delete_many({"date": {"$lt": cutoff_date}})
        logger.info(f"Deleted {result.deleted_count} messages older than 48 hours from database.")
    except PyMongoError as e:
        logger.error(f"Error deleting old messages: {e}")

# Schedule the delete_old_messages function to run every hour
scheduler.add_job(delete_old_messages, "interval", hours=1)
scheduler.start()

if __name__ == "__main__":
    app.run()
