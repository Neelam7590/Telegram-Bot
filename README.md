# TaskBuddy Bot 🤖
A Telegram bot that handles personal chat like a human and automatically posts daily mobile phone launch updates to a group.
## Features
- 💬 **Human-like chat** — responds to messages and commands in personal chat
- 📱 **Daily mobile updates** — posts new mobile phone launches & their features to a group every day at 12:00 PM, with the article link included
- 🧠 **Chat memory** — stores recent conversation history (rolling 3-day window) in Supabase
- 📝 **Activity logging** — logs all bot activity (chats + group posts) in Supabase
- ⚡ **Command execution** — supports commands like "send this message"
- ☁️ **Deployed on Render** — runs as a live web service, kept awake via a cron-job.org ping every 10 minutes
## Tech Stack
- Python
- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot)
- APScheduler (for scheduled daily posts)
- Supabase (for chat memory & activity logs)
- Render (deployment)
- cron-job.org (keep-alive pings)
## Author
**Neelam Dhiman**
- GitHub: [@Neelam7590](https://github.com/Neelam7590)
- LinkedIn: [neelam-dhiman](https://linkedin.com/in/neelam-dhiman-b5a8b4422)
