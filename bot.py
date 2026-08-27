from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from groq import Groq
import requests
import json
import os
import threading
from datetime import datetime, timedelta
from dotenv import load_dotenv
from flask import Flask

load_dotenv()
from supabase import create_client

BOT_TOKEN = os.getenv("BOT_TOKEN")
MY_CHAT_ID = os.getenv("MY_CHAT_ID")
GROUP_CHAT_ID = os.getenv("GROUP_CHAT_ID")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "2407614931a74e8ab329f54edc0e81fb")

MEMORY_DAYS = 7

scheduler = AsyncIOScheduler()
groq_client = Groq(api_key=GROQ_API_KEY)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


# ==================== FLASK KEEP-ALIVE SERVER ====================
# Render ko "alive" dikhane ke liye ye chhota web server (naam alag rakha
# hai taaki niche wale Telegram bot ke "app" se clash na ho)
flask_app = Flask(__name__)


@flask_app.route('/')
def home():
    return "Bot is alive and running!"


def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host='0.0.0.0', port=port)


# ==================== MEMORY / SUPABASE HELPERS ====================

def add_to_memory(chat_id, role, text):
    try:
        supabase.table("chat_memory").insert({"chat_id": str(chat_id), "role": role, "message": text}).execute()
    except Exception as e:
        print("[MEMORY SAVE ERROR]", e)
    cleanup_old_memory()


def cleanup_old_memory():
    try:
        cutoff = (datetime.now() - timedelta(days=MEMORY_DAYS)).isoformat()
        supabase.table("chat_memory").delete().lt("created_at", cutoff).execute()
    except Exception as e:
        print("[CLEANUP ERROR]", e)


def get_recent_history(chat_id, limit=15):
    try:
        result = supabase.table("chat_memory").select("*").eq("chat_id", str(chat_id)).in_("role", ["user", "assistant"]).order("created_at", desc=False).limit(limit).execute()
        history = []
        for m in result.data:
            role = "user" if m["role"] == "user" else "assistant"
            history.append({"role": role, "content": m["message"]})
        return history
    except Exception as e:
        print("[HISTORY FETCH ERROR]", e)
        return []


def get_user_name(chat_id):
    try:
        result = supabase.table("user_profile").select("name").eq("chat_id", str(chat_id)).execute()
        if result.data:
            return result.data[0]["name"]
        return None
    except Exception as e:
        print("[GET NAME ERROR]", e)
        return None


def set_user_name(chat_id, name):
    try:
        supabase.table("user_profile").upsert({"chat_id": str(chat_id), "name": name}).execute()
    except Exception as e:
        print("[SET NAME ERROR]", e)


def save_last_composed(chat_id, text):
    try:
        supabase.table("last_composed").upsert({"chat_id": str(chat_id), "message": text, "updated_at": datetime.now().isoformat()}).execute()
    except Exception as e:
        print("[SAVE COMPOSED ERROR]", e)


def get_last_composed(chat_id):
    try:
        result = supabase.table("last_composed").select("message").eq("chat_id", str(chat_id)).execute()
        if result.data:
            return result.data[0]["message"]
        return None
    except Exception as e:
        print("[GET COMPOSED ERROR]", e)
        return None


def save_user_fact(chat_id, key, value):
    try:
        supabase.table("user_facts").upsert({"chat_id": str(chat_id), "fact_key": key, "fact_value": value, "updated_at": datetime.now().isoformat()}, on_conflict="chat_id,fact_key").execute()
    except Exception as e:
        print("[SAVE FACT ERROR]", e)


def get_user_facts(chat_id):
    try:
        result = supabase.table("user_facts").select("fact_key,fact_value").eq("chat_id", str(chat_id)).execute()
        facts = {}
        for r in result.data:
            facts[r["fact_key"]] = r["fact_value"]
        return facts
    except Exception as e:
        print("[GET FACTS ERROR]", e)
        return {}


# ==================== NEWS / MOBILE UPDATES ====================

def fetch_mobile_articles():
    url = "https://newsapi.org/v2/everything?q=(\"new smartphone\" OR \"phone launch\" OR \"mobile launch\" OR Samsung OR iPhone OR Xiaomi OR OnePlus OR \"Google Pixel\" OR \"new features\" smartphone)&sortBy=publishedAt&language=en&pageSize=10&apiKey=" + NEWS_API_KEY
    try:
        response = requests.get(url)
        data = response.json()
        return data.get("articles") or []
    except Exception:
        return []


async def send_one_article(bot_app, chat_id, article):
    title = article.get("title", "Untitled")
    source = article.get("source", {}).get("name", "")
    image_url = article.get("urlToImage")
    link = article.get("url", "")
    caption = "📱 " + title + "\n📰 Source: " + source + "\n🔗 " + link
    if image_url:
        try:
            await bot_app.bot.send_photo(chat_id=chat_id, photo=image_url, caption=caption)
            add_to_memory(chat_id, "bot_activity", "Mobile update bheja: " + title)
            return
        except Exception:
            pass
    await bot_app.bot.send_message(chat_id=chat_id, text=caption)
    add_to_memory(chat_id, "bot_activity", "Mobile update bheja: " + title)


async def check_and_send_new_updates(bot_app):
    articles = fetch_mobile_articles()
    for article in articles:
        url = article.get("url")
        if not url:
            continue
        try:
            existing = supabase.table("sent_articles").select("id").eq("article_url", url).execute()
            if not existing.data:
                await send_one_article(bot_app, GROUP_CHAT_ID, article)
                supabase.table("sent_articles").insert({"article_url": url}).execute()
        except Exception as e:
            print("[SENT ARTICLES ERROR]", e)


async def test_mobile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    articles = fetch_mobile_articles()
    if not articles:
        await update.message.reply_text("Abhi koi mobile update nahi mila.")
        return
    for article in articles[:5]:
        await send_one_article(context.application, update.message.chat_id, article)


async def send_scheduled_reminder(bot_app, chat_id, text):
    await bot_app.bot.send_message(chat_id=chat_id, text=text)
    add_to_memory(chat_id, "bot_activity", "Scheduled reminder bheja: " + text)


async def send_scheduled_mobile_update(bot_app, chat_id):
    articles = fetch_mobile_articles()
    if articles:
        await send_one_article(bot_app, chat_id, articles[0])


async def send_delayed_mobile_update(bot_app, chat_id):
    articles = fetch_mobile_articles()
    if articles:
        await send_one_article(bot_app, chat_id, articles[0])


async def send_interval_mobile_update(bot_app, chat_id):
    articles = fetch_mobile_articles()
    if articles:
        await send_one_article(bot_app, chat_id, articles[0])


# ==================== INTENT UNDERSTANDING (AI) ====================

INTENT_SYSTEM_PROMPT = """Tum ek intent router ho ek Telegram bot ke liye. SIRF ek valid JSON object return karo, kuch aur text nahi.

Top-level optional field: "name_update" (Naam) -> SIRF jab user apna naam STATE kar raha ho, POOCH raha ho to ye field mat bhejo.

Intents:
1. remember_fact - user STATE kar raha hai naya fact yaad rakhne ke liye
   {"intent": "remember_fact", "fact_key": "last_number", "fact_value": "45"}
   Generic number ke liye HAMESHA fact_key="last_number" use karo, kabhi variation naam mat banao. "phone_number" sirf jab explicitly phone bola gaya ho.
2. schedule - roz fixed time pe plain text reminder
   {"intent": "schedule", "hour": 9, "minute": 0, "message": "reminder text"}
3. schedule_mobile_update - roz (daily) fixed time pe mobile update, EK BAAR PER DIN
   {"intent": "schedule_mobile_update", "hour": 12, "minute": 2, "target": "group"}
4. delayed_mobile_update - X second/minute BAAD, EK BAAR mobile update
   {"intent": "delayed_mobile_update", "seconds": 30, "target": "group"}
4b. interval_mobile_update - HAR X second/minute mein REPEAT hoke (baar baar, ruk ruk kar) mobile update bheja jaye
   {"intent": "interval_mobile_update", "seconds": 20, "target": "group"}
4c. stop_interval_mobile_update - user chahta hai ki jo "har X second/minute" wala repeated update chal raha hai, wo ROK diya jaye
   {"intent": "stop_interval_mobile_update", "target": "group"}
5. mobile_update - abhi turant, sirf ek baar, mobile update
   {"intent": "mobile_update", "target": "group"}
6. compose_send - bot khud naya message likhe
   {"intent": "compose_send", "target": "group", "description": "kaisa message"}
7. modify_last - pehle compose kiye message ko modify karna
   {"intent": "modify_last", "target": "group", "instruction": "kya badlav"}
8. resend_last - pichla composed message dobara bhejna
   {"intent": "resend_last", "target": "group"}
9. send_now - exact likha text bhejna
   {"intent": "send_now", "target": "group", "message": "exact text"}
10. chat - har sawaal/baat jismein user apne baare mein kuch pooch raha ho (recall) ya normal baatcheet
   {"intent": "chat"}

Examples:
mera number 9876543210 hai -> remember_fact fact_key=phone_number
1 number yaad rakhna 45 -> remember_fact fact_key=last_number fact_value=45
mera naam kya hai -> chat
mne abhi kya number btaya -> chat
mera naam Neelam hai -> chat with name_update=Neelam
welcome message professional banao -> compose_send
isi message ko English mein convert karo -> modify_last
wahi wala dobara bhejo -> resend_last
12:02 pe group mein update dalo (roz) -> schedule_mobile_update hour=12 minute=2 target=group
1 or update dalo lakin 30 second baad (ek baar) -> delayed_mobile_update seconds=30 target=group
har 20 second mein group m update dalo (repeat) -> interval_mobile_update seconds=20 target=group
har 5 minute mein update bhejo (repeat) -> interval_mobile_update seconds=300 target=group
ab update rok do -> stop_interval_mobile_update target=group
20 second wali update band karo -> stop_interval_mobile_update target=group
group mein abhi update bhejo -> mobile_update target=group
roz 9 baje paani pine yaad dilana -> schedule hour=9 minute=0 message=paani pina hai

CRITICAL RULES:
- Sawaal (kya/kaunsa/kab/yaad hai kya) ho aur user kuch bata nahi raha to hamesha chat do.
- "har X second/minute" (repeat, baar baar, HAMESHA chalte rehna) -> interval_mobile_update.
- "X second/minute baad" (sirf EK BAAR) -> delayed_mobile_update.
- "roz/daily/fixed time" (ek fixed ghadi pe, har din) -> schedule_mobile_update.
- "rok do", "band karo", "stop karo" (jo repeat wala chal raha hai use rokna) -> stop_interval_mobile_update.
- Ye options kabhi mix mat karo — sirf sabse matching wala use karo."""


def understand_intent(user_text, chat_id):
    history = get_recent_history(chat_id, limit=6)
    lines = []
    for h in history:
        lines.append(h["role"] + ": " + h["content"])
    if lines:
        history_text = "\n".join(lines)
    else:
        history_text = "(koi pichli baat nahi)"
    full_prompt = INTENT_SYSTEM_PROMPT + "\n\nPichli baatcheet:\n" + history_text
    try:
        response = groq_client.chat.completions.create(
            model="openai/gpt-oss-120b",
            temperature=0,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": full_prompt}, {"role": "user", "content": user_text}]
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        print("[INTENT CLASSIFY ERROR]", e)
        return {"intent": "chat"}


def compose_message_with_ai(description):
    response = groq_client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[
            {"role": "system", "content": "Tum ek professional message writer ho. User jo describe kare uske hisaab se ek acha message likho, sirf final message do."},
            {"role": "user", "content": description}
        ]
    )
    return response.choices[0].message.content


async def get_ai_chat_reply(chat_id, user_text):
    history = get_recent_history(chat_id, limit=15)
    name = get_user_name(chat_id)
    facts = get_user_facts(chat_id)

    system_content = ("Tum Neelam ke ek close dost jaise baat karte ho, insaan jaisa, casual aur natural. "
                       "Greeting ka reply simple do, naam/facts zabardasti mat lao. Agar user directly naam/fact "
                       "ke baare mein poochta hai to saved data se exact jawab do. Robotic pattern repeat mat karo.")
    if name:
        system_content += " User ka confirmed naam: " + name
    if facts:
        parts = []
        for k, v in facts.items():
            parts.append(k + ": " + v)
        system_content += " User ke saved facts: " + ", ".join(parts)

    messages = [{"role": "system", "content": system_content}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_text})

    response = groq_client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=messages,
        temperature=0.9,
        max_tokens=200
    )
    return response.choices[0].message.content


# ==================== TELEGRAM HANDLERS ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hello! Main aapka automation bot hoon. Kaise madad karu?")


async def reply_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if message is None or not message.text:
        return

    user_text = message.text
    chat_id = message.chat_id
    bot_app = context.application

    print("[MESSAGE RECEIVED] chat_id=" + str(chat_id) + ", text=" + user_text)
    result = understand_intent(user_text, chat_id)
    intent = result.get("intent", "chat")
    print("[INTENT DETECTED]", result)

    name_update = result.get("name_update")
    if name_update:
        set_user_name(chat_id, name_update)

    add_to_memory(chat_id, "user", user_text)

    if intent == "remember_fact":
        key = result.get("fact_key", "note")
        value = str(result.get("fact_value", "")).strip()
        if not value or value.lower() in ["none", "null", "n/a", ""]:
            ai_reply = await get_ai_chat_reply(chat_id, user_text)
            add_to_memory(chat_id, "assistant", ai_reply)
            await message.reply_text(ai_reply)
        else:
            save_user_fact(chat_id, key, value)
            await message.reply_text("Theek hai, yaad rakh liya — \"" + value + "\"")
            add_to_memory(chat_id, "bot_activity", "Fact save kiya: " + key + " = " + value)

    elif intent == "schedule":
        hour = result.get("hour")
        minute = result.get("minute")
        msg_text = result.get("message", "")
        scheduler.add_job(send_scheduled_reminder, "cron", hour=hour, minute=minute, args=[bot_app, chat_id, msg_text])
        await message.reply_text("Theek hai! Roz " + str(hour).zfill(2) + ":" + str(minute).zfill(2) + " baje ye bhej dunga: \"" + msg_text + "\"")
        add_to_memory(chat_id, "bot_activity", "Reminder schedule kiya: " + msg_text)

    elif intent == "schedule_mobile_update":
        hour = result.get("hour")
        minute = result.get("minute")
        target_chat_id = GROUP_CHAT_ID if result.get("target") == "group" else chat_id
        scheduler.add_job(send_scheduled_mobile_update, "cron", hour=hour, minute=minute, args=[bot_app, target_chat_id])
        await message.reply_text("Theek hai! Roz " + str(hour).zfill(2) + ":" + str(minute).zfill(2) + " baje mobile update bhej dunga.")
        add_to_memory(chat_id, "bot_activity", "Mobile update schedule kiya roz")

    elif intent == "delayed_mobile_update":
        seconds = result.get("seconds", 30)
        target_chat_id = GROUP_CHAT_ID if result.get("target") == "group" else chat_id
        run_time = datetime.now() + timedelta(seconds=seconds)
        scheduler.add_job(send_delayed_mobile_update, "date", run_date=run_time, args=[bot_app, target_chat_id])
        await message.reply_text("Theek hai! " + str(seconds) + " second baad update bhej dunga.")
        add_to_memory(chat_id, "bot_activity", "Delayed mobile update: " + str(seconds) + " sec baad")

    elif intent == "interval_mobile_update":
        seconds = result.get("seconds", 20)
        target_chat_id = GROUP_CHAT_ID if result.get("target") == "group" else chat_id
        job_id = "interval_mobile_" + str(target_chat_id)
        try:
            scheduler.remove_job(job_id)
        except Exception:
            pass
        scheduler.add_job(send_interval_mobile_update, "interval", seconds=seconds, args=[bot_app, target_chat_id], id=job_id)
        await message.reply_text("Theek hai! Ab har " + str(seconds) + " second mein update bhejta rahunga.")
        add_to_memory(chat_id, "bot_activity", "Interval mobile update schedule kiya: har " + str(seconds) + " second")

    elif intent == "stop_interval_mobile_update":
        target_chat_id = GROUP_CHAT_ID if result.get("target") == "group" else chat_id
        job_id = "interval_mobile_" + str(target_chat_id)
        try:
            scheduler.remove_job(job_id)
            await message.reply_text("Theek hai, ab wo repeated update rok diya!")
        except Exception:
            await message.reply_text("Abhi koi chalta hua repeated update nahi mila jo rokna hai.")
        add_to_memory(chat_id, "bot_activity", "Interval mobile update rok diya")

    elif intent == "mobile_update":
        target_chat_id = GROUP_CHAT_ID if result.get("target") == "group" else chat_id
        articles = fetch_mobile_articles()
        if articles:
            await send_one_article(bot_app, target_chat_id, articles[0])
        else:
            await message.reply_text("Abhi koi mobile update nahi mila.")

    elif intent == "compose_send":
        target_chat_id = GROUP_CHAT_ID if result.get("target") == "group" else MY_CHAT_ID
        composed_text = compose_message_with_ai(result.get("description", ""))
        await bot_app.bot.send_message(chat_id=target_chat_id, text=composed_text)
        await message.reply_text("Bhej diya:\n\n" + composed_text)
        add_to_memory(chat_id, "bot_activity", "Composed message bheja: " + composed_text)
        save_last_composed(chat_id, composed_text)

    elif intent == "modify_last":
        target_chat_id = GROUP_CHAT_ID if result.get("target") == "group" else MY_CHAT_ID
        last_msg = get_last_composed(chat_id)
        instruction = result.get("instruction", "")
        if last_msg:
            modify_response = groq_client.chat.completions.create(
                model="openai/gpt-oss-120b",
                messages=[
                    {"role": "system", "content": "Tum ek editor ho. Original message ko instruction ke hisaab se modify karo, naya content mat banao."},
                    {"role": "user", "content": "Original message:\n" + last_msg + "\n\nInstruction: " + instruction}
                ]
            )
            modified_text = modify_response.choices[0].message.content
            await bot_app.bot.send_message(chat_id=target_chat_id, text=modified_text)
            await message.reply_text("Bhej diya:\n\n" + modified_text)
            save_last_composed(chat_id, modified_text)
        else:
            await message.reply_text("Mujhe koi pichla message yaad nahi hai jo modify kar sakoon.")

    elif intent == "resend_last":
        target_chat_id = GROUP_CHAT_ID if result.get("target") == "group" else MY_CHAT_ID
        last_msg = get_last_composed(chat_id)
        if last_msg:
            await bot_app.bot.send_message(chat_id=target_chat_id, text=last_msg)
            await message.reply_text("Bhej diya, wahi wala message!")
        else:
            await message.reply_text("Mujhe koi pichla message yaad nahi hai.")

    elif intent == "send_now":
        target_chat_id = GROUP_CHAT_ID if result.get("target") == "group" else MY_CHAT_ID
        msg_text = result.get("message", "")
        await bot_app.bot.send_message(chat_id=target_chat_id, text=msg_text)
        await message.reply_text("Bhej diya: \"" + msg_text + "\"")

    else:
        ai_reply = await get_ai_chat_reply(chat_id, user_text)
        add_to_memory(chat_id, "assistant", ai_reply)
        await message.reply_text(ai_reply)


async def post_init(bot_app):
    scheduler.add_job(check_and_send_new_updates, "interval", minutes=30, args=[bot_app])
    scheduler.add_job(cleanup_old_memory, "interval", hours=6)
    scheduler.start()
    print("Scheduler start ho gaya hai...")


# ==================== MAIN ====================

telegram_app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()
telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("testmobile", test_mobile_command))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reply_message))

if __name__ == "__main__":
    # Flask ko alag thread mein chalao taaki Render ko "port pe alive" dikhe
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    print("Bot chalu ho gaya hai...")
    telegram_app.run_polling()
