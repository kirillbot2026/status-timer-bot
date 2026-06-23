import os
import re
import json
import time
import asyncio
from pathlib import Path

from PIL import Image, ImageDraw
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, ContextTypes, filters

TOKEN = os.getenv("BOT_TOKEN")
DATA_FILE = Path("data.json")
STATUS_COUNT = 40

EXTRA = """BEBRA RENT
Крутой аккаунт
Есть машинка
1337"""


def load():
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text())
    return {"chat_id": None, "statuses": {}, "timers": {}}


def save(data):
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def caption(num, busy=False, left="00:00:00"):
    if busy:
        first = f"Статус {num}: 🔴 Занят {left}"
    else:
        first = f"Статус {num}: 🟢 Свободно"
    return first + "\n\n" + EXTRA


def make_img(num):
    path = f"status_{num}.png"
    img = Image.new("RGB", (1200, 800), (15, 15, 18))
    d = ImageDraw.Draw(img)
    d.text((330, 300), "BEBRA RENT", fill=(255, 255, 255))
    d.text((470, 410), f"STATUS {num}", fill=(255, 255, 255))
    img.save(path)
    return path


async def create40(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = {"chat_id": update.effective_chat.id, "statuses": {}, "timers": {}}

    for num in range(1, STATUS_COUNT + 1):
        img = make_img(num)
        with open(img, "rb") as p:
            msg = await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=p,
                caption=caption(num)
            )

        data["statuses"][str(msg.message_id)] = {
            "num": num,
            "message_id": msg.message_id
        }

        await asyncio.sleep(0.3)

    save(data)
    await update.message.reply_text("Готово: 40 статусов созданы.")


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message

    if not msg or not msg.text:
        return

    if not msg.reply_to_message:
        return

    text = msg.text.strip().lower()

    if not re.fullmatch(r"\d+|0|free|stop|стоп|свободно", text):
        return

    data = load()

    replied_id = str(msg.reply_to_message.message_id)

    if replied_id not in data["statuses"]:
        return

    status = data["statuses"][replied_id]
    num = status["num"]
    status_message_id = status["message_id"]
    chat_id = data["chat_id"]

    if text in ["0", "free", "stop", "стоп", "свободно"]:
        data["timers"].pop(replied_id, None)
        save(data)

        await context.bot.edit_message_caption(
            chat_id=chat_id,
            message_id=status_message_id,
            caption=caption(num)
        )
        return

    hours = int(text)

    if hours <= 0:
        return

    finish = int(time.time() + hours * 3600)

    data["timers"][replied_id] = {
        "finish": finish,
        "num": num,
        "message_id": status_message_id
    }

    save(data)

    await context.bot.edit_message_caption(
        chat_id=chat_id,
        message_id=status_message_id,
        caption=caption(num, True, f"{hours:02d}:00:00")
    )


async def loop(app):
    while True:
        data = load()
        now = int(time.time())

        for replied_id, timer in list(data["timers"].items()):
            left = timer["finish"] - now
            num = timer["num"]
            msg_id = timer["message_id"]
            chat_id = data["chat_id"]

            try:
                if left <= 0:
                    await app.bot.edit_message_caption(
                        chat_id=chat_id,
                        message_id=msg_id,
                        caption=caption(num)
                    )
                    del data["timers"][replied_id]
                    save(data)
                else:
                    h = left // 3600
                    m = (left % 3600) // 60
                    s = left % 60

                    await app.bot.edit_message_caption(
                        chat_id=chat_id,
                        message_id=msg_id,
                        caption=caption(num, True, f"{h:02d}:{m:02d}:{s:02d}")
                    )
            except Exception as e:
                print("TIMER ERROR:", e)

        await asyncio.sleep(60)


async def post_init(app):
    asyncio.create_task(loop(app))


def main():
    app = Application.builder().token(TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("create40", create40))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    app.run_polling()


if __name__ == "__main__":
    main()
