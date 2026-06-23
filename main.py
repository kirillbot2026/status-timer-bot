import os
from telegram import Bot

TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(TOKEN)

CHANNEL_ID = -1004380519537

for i in range(1, 41):
    bot.send_message(
        chat_id=CHANNEL_ID,
        text=f"Статус {i}: 🟢 Свободно"
    )

print("40 статусов созданы")
