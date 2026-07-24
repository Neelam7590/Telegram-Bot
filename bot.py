from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from groq import Groq
import requests
import json
from datetime import date

import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
MY_CHAT_ID = os.getenv("MY_CHAT_ID")
GROUP_CHAT_ID = os.getenv("GROUP_CHAT_ID")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

SPORTS_API_KEY = "123"

scheduler = AsyncIOScheduler()
groq_client = Groq(api_key=GROQ_API_KEY)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hello! Main aapka automation bot hoon. Kaise madad karu?")

def get_sports_updates():
    today = date.today().strftime("%Y-%m-%d")
    url = f"https://www.thesportsdb.com/api/v1/json/{SPORTS_API_KEY}/eventsday.php?d={today}"
    try:
        response = requests.get(url)
        data = response.json()
        events = data.get("events")
        if not events:
            return "Aaj koi bada match schedule nahi hai."
        message = "📅 Aaj ke Sports Updates:\n\n"
        for event in events[:10]:
            name = event.get("strEvent", "Unknown Match")
            league = event.get("strLeague", "")
            time = event.get("strTime", "")
            message += f"🏆 {name} ({league}) - {time}\n"
        return message
    except Exception:
        return "Sports updates fetch karne me error aayi."

async def send_daily_sports_update(app):
    message = get_sports_updates()
    await app.bot.send_message(chat_id=GROUP_CHAT_ID, text=message)

async def send_scheduled_reminder(app, chat_id, text):
    await app.bot.send_message(chat_id=chat_id, text=text)

async def test_sports_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(get_sports_updates())

def understand_intent(user_text):
    system_prompt = """Tum ek intent classifier ho. User ke message ko analyze karo aur SIRF ye JSON format me reply karo, kuch aur mat likho:

{"intent": "schedule", "hour": 9, "minute": 0, "message": "reminder text"}
YA
{"intent": "sports", "message": ""}
YA
{"intent": "chat", "message": ""}

Agar user kisi time pe reminder/message set karna chahta hai, "schedule" use karo aur time 24-hour format me nikaalo.
Agar user sports/scores puchta hai, "sports" use karo.
Baaki normal baaton ke liye "chat" use karo."""

    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text}
        ]
    )
    try:
        return json.loads(response.choices[0].message.content)
    except Exception:
        return {"intent": "chat", "message": ""}

async def get_ai_chat_reply(user_text):
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": "Tum ek friendly Telegram assistant ho. Hinglish me natural, chhoti aur helpful reply dena."},
            {"role": "user", "content": user_text}
        ]
    )
    return response.choices[0].message.content

async def reply_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if message is None or not message.text:
        return

    user_text = message.text
    chat_id = message.chat_id
    app = context.application

    result = understand_intent(user_text)
    intent = result.get("intent")

    if intent == "schedule":
        hour = result.get("hour")
        minute = result.get("minute")
        message_text = result.get("message")
        scheduler.add_job(
            send_scheduled_reminder,
            "cron",
            hour=hour,
            minute=minute,
            args=[app, chat_id, message_text],
        )
        await message.reply_text(f"Theek hai! Roz {hour:02d}:{minute:02d} baje ye bhej dunga: \"{message_text}\"")

    elif intent == "sports":
        await message.reply_text(get_sports_updates())

    else:
        ai_reply = await get_ai_chat_reply(user_text)
        await message.reply_text(ai_reply)

async def post_init(app):
    scheduler.add_job(send_daily_sports_update, "cron", hour=12, minute=0, args=[app])
    scheduler.start()
    print("Scheduler start ho gaya hai...")

app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("testsports", test_sports_command))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reply_message))

print("Bot chalu ho gaya hai...")
app.run_polling()