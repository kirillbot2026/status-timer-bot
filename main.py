import os
import re
import json
import time
import asyncio
from pathlib import Path

from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, ContextTypes, filters

TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))

DATA_FILE = Path("data.json")
STATUS_COUNT = 40


def load_data():
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text())
    return {"statuses": {}, "timers": {}}


def save_data(data):
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def make_text(num, busy=False, left_text=None, extra=None):
    if extra is None:
        extra = "BEBRA RENT\nКрутой аккаунт\nЕсть машинка\n1337"

    if busy:
        first = f"Статус {num}: 🔴 Занят {left_text}"
    else:
        first = f"Статус {num}: 🟢 Свободно"

    return first + "\n\n" + extra


def get_extra(text):
    if not text:
        return "BEBRA RENT\nКрутой аккаунт\nЕсть машинка\n1337"
    parts = text.split("\n\n", 1)
    if len(parts) == 2:
        return parts[1]
    return "BEBRA RENT\nКрутой аккаунт\nЕсть машинка\n1337"


async def create40(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()

    for i in range(1, STATUS_COUNT + 1):
        msg = await context.bot.send_message(
            chat_id=CHANNEL_ID,
            text=make_text(i)
        )

        data["statuses"][str(i)] = {
            "message_id": msg.message_id,
            "extra": "BEBRA RENT\nКрутой аккаунт\nЕсть машинка\n1337"
        }

        await asyncio.sleep(0.3)

    save_data(data)
    await update.effective_message.reply_text("40 статусов созданы.")


async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.text:
        return

    text = msg.text.strip().lower()

    match = re.fullmatch(r"(\d{1,2})\s+(\d+|free|стоп|stop|свободно)", text)
    if not match:
        return

    num = int(match.group(1))
    value = match.group(2)

    if num < 1 or num > STATUS_COUNT:
        return

    data = load_data()

    if str(num) not in data["statuses"]:
        return

    status = data["statuses"][str(num)]
    message_id = status["message_id"]
    extra = status.get("extra", "BEBRA RENT\nКрутой аккаунт\nЕсть машинка\n1337")

    if value in ["free", "стоп", "stop", "свободно"] or value == "0":
        data["timers"].pop(str(num), None)
        save_data(data)

        await context.bot.edit_message_text(
            chat_id=CHANNEL_ID,
            message_id=message_id,
            text=make_text(num, busy=False, extra=extra)
        )
        return

    hours = int(value)
    finish = int(time.time() + hours * 3600)

    data["timers"][str(num)] = {
        "finish": finish
    }

    save_data(data)


async def timer_loop(app):
    while True:
        data = load_data()
        now = int(time.time())
        changed = False

        for num_str, timer in list(data["timers"].items()):
            num = int(num_str)
            status = data["statuses"].get(num_str)

            if not status:
                continue

            message_id = status["message_id"]
            extra = status.get("extra", "BEBRA RENT\nКрутой аккаунт\nЕсть машинка\n1337")
            left = timer["finish"] - now

            try:
                if left <= 0:
                    await app.bot.edit_message_text(
                        chat_id=CHANNEL_ID,
                        message_id=message_id,
                        text=make_text(num, busy=False, extra=extra)
                    )
                    del data["timers"][num_str]
                    changed = True
                else:
                    h = left // 3600
                    m = (left % 3600) // 60
                    s = left % 60
                    left_text = f"{h:02d}:{m:02d}:{s:02d}"

                    await app.bot.edit_message_text(
                        chat_id=CHANNEL_ID,
                        message_id=message_id,
                        text=make_text(num, busy=True, left_text=left_text, extra=extra)
                    )

            except Exception as e:
                print("EDIT ERROR:", e)

        if changed:
            save_data(data)

        await asyncio.sleep(60)


async def post_init(app):
    asyncio.create_task(timer_loop(app))


def main():
    app = Application.builder().token(TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("create40", create40))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handler))

    app.run_polling()


if __name__ == "__main__":
    main()
