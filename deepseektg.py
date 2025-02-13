import os
import logging
import asyncio
import re
import time
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext
from fireworks.client import Fireworks  # Synchronous client
from dotenv import load_dotenv

# Load environment variables from a .env file if present
load_dotenv()

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
FIREWORKS_API_KEY = os.environ.get("FIREWORKS_API_KEY")

# 🔒 List of allowed user IDs (replace with your actual user IDs)
AUTHORIZED_USERS = [123456789, 987654321]  # Replace with your actual Telegram user IDs

# A set to track users who have initiated the conversation with /start
started_users = set()

# Telegram message maximum length
MAX_MESSAGE_LENGTH = 4096

# Set up logging for debugging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)

# Create a global Fireworks client instance (synchronous)
client = Fireworks(api_key=FIREWORKS_API_KEY)

async def stream_fireworks_api(user_text: str):
    """
    Async generator that wraps the synchronous Fireworks streaming API.
    Uses run_in_executor to yield chunks without blocking the event loop.
    Each yielded chunk is the new delta text provided by the API.
    """
    def generator():
        response_generator = client.chat.completions.create(
            model="accounts/fireworks/models/deepseek-r1",
            messages=[{"role": "user", "content": user_text}],
            stream=True,  # Enable streaming mode
        )
        for chunk in response_generator:
            if chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    gen = generator()
    loop = asyncio.get_running_loop()

    def get_next_chunk():
        try:
            return next(gen)
        except StopIteration:
            return None  # Sentinel value to indicate the generator is done

    while True:
        chunk = await loop.run_in_executor(None, get_next_chunk)
        if chunk is None:
            break
        yield chunk

async def start(update: Update, context: CallbackContext) -> None:
    """Handles the /start command for authorized users."""
    user_id = update.message.from_user.id
    logging.info("User %s used /start", user_id)

    if user_id not in AUTHORIZED_USERS:
        logging.warning("Unauthorized user %s tried to use /start.", user_id)
        await update.message.reply_text("You're not authorized to use this bot.")
        return

    started_users.add(user_id)
    await update.message.reply_text("Hello! You're authorized. Send me a message and I'll reply using Fireworks AI.")

async def handle_message(update: Update, context: CallbackContext) -> None:
    """
    Handles messages from authorized users.
    If the user hasn't typed /start, they are prompted to do so.
    Otherwise, the bot streams the LLM response and updates a single Telegram message
    at 4-second intervals to avoid flooding, and ensures the message never exceeds
    Telegram's maximum allowed length.
    """
    user_id = update.message.from_user.id
    user_text = update.message.text
    logging.info("Received message from user %s: %s", user_id, user_text)

    if user_id not in AUTHORIZED_USERS:
        logging.warning("Unauthorized user %s sent a message. Ignoring.", user_id)
        return

    if user_id not in started_users:
        await update.message.reply_text("Please type /start to begin.")
        return

    # Send an initial processing message
    processing_message = await update.message.reply_text("Processing your request, please wait...")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    accumulated_text = ""
    last_update_time = time.monotonic()

    try:
        async for chunk in stream_fireworks_api(user_text):
            accumulated_text += chunk
            now = time.monotonic()
            # Only update the Telegram message every 4 seconds
            if now - last_update_time >= 4:
                cleaned_text = re.sub(r"<.*?>", "", accumulated_text).strip()
                # Ensure message does not exceed Telegram's maximum allowed length
                if len(cleaned_text) > MAX_MESSAGE_LENGTH:
                    cleaned_text = cleaned_text[-MAX_MESSAGE_LENGTH:]
                try:
                    await processing_message.edit_text(cleaned_text)
                except Exception as e:
                    logging.error("Error updating message: %s", e)
                last_update_time = now

        # Final update after streaming is complete
        cleaned_text = re.sub(r"<.*?>", "", accumulated_text).strip()
        if len(cleaned_text) > MAX_MESSAGE_LENGTH:
            cleaned_text = cleaned_text[-MAX_MESSAGE_LENGTH:]
        await processing_message.edit_text(cleaned_text)
    except Exception as e:
        logging.exception("Error during streaming: %s", e)
        await processing_message.edit_text("Sorry, there was an error processing your request.")

def main() -> None:
    """Starts the Telegram bot."""
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Register command and message handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Run the bot
    application.run_polling()

if __name__ == '__main__':
    main()
