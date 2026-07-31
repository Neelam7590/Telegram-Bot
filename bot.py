from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from groq import Groq
import requests
import json
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()
from supabase import create_client

BOT_TOKEN = os.getenv("BOT_TOKEN")
MY_CHAT_ID = os.getenv("MY_CHAT_ID")
GROUP_CHAT_ID = os.getenv("GROUP_CHAT_ID")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

NEWS_API_KEY = os.getenv("NEWS_API_KEY")
MEMORY_DAYS = 3

scheduler = AsyncIOScheduler()
groq_client = Groq(api_key=GROQ_API_KEY)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ---------------- MEMORY ----------------

def add_to_memory(chat_id, role, text):
    supabase.table("chat_memory").insert({
        "chat_id": str(chat_id), "role": role, "message": text
    }).execute()
    cleanup_old_memory()

def cleanup_old_memory():
    cutoff = (datetime.now() - timedelta(days=MEMORY_DAYS)).isoformat()
    supabase.table("chat_memory").delete().lt("created_at", cutoff).execute()

def get_recent_history(chat_id, limit=10):
    result = supabase.table("chat_memory") \
        .select("*") \
        .eq("chat_id", str(chat_id)) \
        .in_("role", ["user", "assistant"]) \
        .order("created_at", desc=False) \
        .limit(limit) \
        .execute()
    return [{"role": m["role"], "content": m["message"]} for m in result.data]

def get_user_name(chat_id):
    result = supabase.table("user_profile").select("name").eq("chat_id", str(chat_id)).execute()
    return result.data[0]["name"] if result.data else None

def set_user_name(chat_id, name):
    supabase.table("user_profile").upsert({"chat_id": str(chat_id), "name": name}).execute()

# ---------------- MOBILE UPDATES ----------------

def fetch_mobile_articles():
    url = (f"https://newsapi.org/v2/everything?q=(\"new smartphone\" OR \"phone launch\" OR "
           f"\"mobile launch\" OR Samsung OR iPhone OR Xiaomi OR OnePlus OR \"Google Pixel\" OR "
           f"\"new features\" smartphone)&sortBy=publishedAt&language=en&pageSize=10&apiKey={NEWS_API_KEY}")
    try:
        return requests.get(url).json().get("articles") or []
    except Exception:
        return []

async def send_one_article(app, chat_id, article):
    title = article.get("title", "Untitled")
    source = article.get("source", {}).get("name", "")
    image_url = article.get("urlToImage")
    link = article.get("url", "")
    caption = f"📱 {title}\n📰 Source: {source}\n🔗 {link}"
    if image_url:
        try:
            await app.bot.send_photo(chat_id=chat_id, photo=image_url, caption=caption)
            add_to_memory(chat_id, "bot_activity", f"Mobile update bheja: {title}")
            return
        except Exception:
            pass
    await app.bot.send_message(chat_id=chat_id, text=caption)
    add_to_memory(chat_id, "bot_activity", f"Mobile update bheja: {title}")

async def check_and_send_new_updates(app):
    for article in fetch_mobile_articles():
        url = article.get("url")
        if not url:
            continue
        existing = supabase.table("sent_articles").select("id").eq("article_url", url).execute()
        if not existing.data:
            await send_one_article(app, GROUP_CHAT_ID, article)
            supabase.table("sent_articles").insert({"article_url": url}).execute()

async def test_mobile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    articles = fetch_mobile_articles()
    if not articles:
        await update.message.reply_text("Abhi koi mobile update nahi mila.")
        return
    for article in articles[:5]:
        await send_one_article(context.application, update.message.chat_id, article)

async def send_scheduled_reminder(app, chat_id, text):
    await app.bot.send_message(chat_id=chat_id, text=text)
    add_to_memory(chat_id, "bot_activity", f"Scheduled reminder bheja: {text}")

# ---------------- SINGLE, ROBUST INTENT CLASSIFIER ----------------
# Ek hi jagah decide hota hai — koi alag keyword-shortcut nahi (jo pehle AI se takrata tha).
# Few-shot examples diye hain taaki AI pattern se seekhe, sirf rules se guess na kare.

INTENT_SYSTEM_PROMPT = """Tum ek intent router ho ek Telegram bot ke liye. Tumhe SIRF ek valid JSON object return karna hai, kuch aur text nahi (na explanation, na markdown).

Bot ye cheezein kar sakta hai:
1. "set_name" - jab user apna naam bata/confirm kare
   {"intent": "set_name", "name": "Naam"}
2. "schedule" - jab user chahta hai bot FUTURE me (roz ya kisi din) ek fixed time pe koi message bheje
   {"intent": "schedule", "hour": 9, "minute": 0, "message": "reminder text"}
3. "mobile_update" - jab user naye mobile phone/smartphone launches, features, ya "update"/"latest update" (bina kisi custom text ke) maange
   {"intent": "mobile_update", "target": "group"}   // target: "group" agar group/channel ka zikar ho, warna "me"
4. "compose_send" - jab user chahta hai bot KHUD ek naya message LIKHE (jaise "welcome message banao", "professional message likho group ke liye")
   {"intent": "compose_send", "target": "group", "description": "kaisa message chahiye"}
5. "send_now" - jab user ne EXACT already-likha hua text diya ho jo waisa hi bhejna hai (jaise "ye bhejo: Welcome!")
   {"intent": "send_now", "target": "group", "message": "exact text"}
6. "chat" - baaki sab normal baatcheet
   {"intent": "chat"}

Important disambiguation examples:
- "group mein latest update bhejo" -> mobile_update, target=group  (yahan "update" ka matlab HAMESHA mobile update hai, custom message nahi)
- "channel pe update daal do" -> mobile_update, target=group  ("channel" = group hi hai is bot ke context me)
- "apne channel pr ek update bhejo" -> mobile_update, target=group
- "group ke liye professional welcome message banao" -> compose_send, target=group, description="professional welcome message"
- "ye bhejo group me: kal chutti hai" -> send_now, target=group, message="kal chutti hai"
- "mera naam Ajay hai" -> set_name, name="Ajay"
- "mera naam Neelam hai" -> set_name, name="Neelam"
- "mera naam kya hai" -> chat  (ye poochh raha hai, bata nahi raha)
- "mujhe roz 9 baje paani pine yaad dilana" -> schedule, hour=9, minute=0, message="paani pina hai"
- "kaise ho" -> chat

Rule: agar user ka message chhota ya ambiguous hai (jaise "haan", "ok", "wahi wala"), pichli baatcheet (neeche di gayi hai) se context lo.
"""

def understand_intent(user_text, chat_id):
    history = get_recent_history(chat_id, limit=6)
    history_text = "\n".join(f"{h['role']}: {h['content']}" for h in history) or "(koi pichli baat nahi)"

    full_prompt = f"{INTENT_SYSTEM_PROMPT}\n\nPichli baatcheet:\n{history_text}"

    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": full_prompt},
                {"role": "user", "content": user_text}
            ]
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        print(f"Intent classify error: {e}")
        return {"intent": "chat"}

def compose_message_with_ai(description):
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": "Tum ek professional message writer ho. User jo describe kare uske hisaab se ek acha, complete message likho (Hindi/Hinglish me), sirf final message do, koi explanation nahi."},
            {"role": "user", "content": description}
        ]
    )
    return response.choices[0].message.content

async def get_ai_chat_reply(chat_id, user_text):
    history = get_recent_history(chat_id, limit=10)
    name = get_user_name(chat_id)
    system_content = ("Tum Neelam ki personal Telegram assistant ho — friendly, thodi witty, insaan jaisi baat karo, "
                       "chhoti natural replies, robotic tone bilkul nahi.")
    if name:
        system_content += f" User ka confirmed naam '{name}' hai — hamesha yahi use karo."

    messages = [{"role": "system", "content": system_content}] + history + [{"role": "user", "content": user_text}]
    response = groq_client.chat.completions.create(model="llama-3.3-70b-versatile", messages=messages)
    return response.choices[0].message.content

# ---------------- HANDLERS ----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hello! Main aapka automation bot hoon. Kaise madad karu?")

async def reply_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if message is None or not message.text:
        return

    user_text = message.text
    chat_id = message.chat_id
    app = context.application

    result = understand_intent(user_text, chat_id)
    intent = result.get("intent", "chat")

    if intent == "set_name":
        name = result.get("name")
        if name:
            set_user_name(chat_id, name)
            await message.reply_text(f"Theek hai, ab se main tumhe {name} kehkar bulaunga!")
        return

    add_to_memory(chat_id, "user", user_text)

    if intent == "schedule":
        hour, minute, msg_text = result.get("hour"), result.get("minute"), result.get("message", "")
        scheduler.add_job(send_scheduled_reminder, "cron", hour=hour, minute=minute, args=[app, chat_id, msg_text])
        reply = f"Theek hai! Roz {hour:02d}:{minute:02d} baje ye bhej dunga: \"{msg_text}\""
        await message.reply_text(reply)
        add_to_memory(chat_id, "bot_activity", f"Reminder schedule kiya: {msg_text} at {hour:02d}:{minute:02d}")

    elif intent == "mobile_update":
        target_chat_id = GROUP_CHAT_ID if result.get("target") == "group" else chat_id
        articles = fetch_mobile_articles()
        if articles:
            await send_one_article(app, target_chat_id, articles[0])
        else:
            await message.reply_text("Abhi koi mobile update nahi mila.")

    elif intent == "compose_send":
        target_chat_id = GROUP_CHAT_ID if result.get("target") == "group" else MY_CHAT_ID
        composed_text = compose_message_with_ai(result.get("description", ""))
        await app.bot.send_message(chat_id=target_chat_id, text=composed_text)
        await message.reply_text(f"Bhej diya:\n\n{composed_text}")
        add_to_memory(chat_id, "bot_activity", f"Composed message bheja: {composed_text}")

    elif intent == "send_now":
        target_chat_id = GROUP_CHAT_ID if result.get("target") == "group" else MY_CHAT_ID
        msg_text = result.get("message", "")
        await app.bot.send_message(chat_id=target_chat_id, text=msg_text)
        await message.reply_text(f"Bhej diya: \"{msg_text}\"")
        add_to_memory(chat_id, "bot_activity", f"Custom message bheja: {msg_text}")

    else:
        ai_reply = await get_ai_chat_reply(chat_id, user_text)
        add_to_memory(chat_id, "assistant", ai_reply)
        await message.reply_text(ai_reply)

async def post_init(app):
    scheduler.add_job(check_and_send_new_updates, "interval", minutes=30, args=[app])
    scheduler.add_job(cleanup_old_memory, "interval", hours=6)
    scheduler.start()
    print("Scheduler start ho gaya hai...")

app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("testmobile", test_mobile_command))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reply_message))

print("Bot chalu ho gaya hai...")
app.run_polling()