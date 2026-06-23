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
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))

DATA_FILE = Path("data.json")
STATUS_COUNT = 40


def load_data():
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text())
    return {"statuses": {}, "timers": {}}


def save_data(data):
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def make_caption(num, busy=False, left_text=None, extra=None):
    if extra is None:
        extra = "BEBRA RENT\nКрутой аккаунт\nЕсть машинка\n1337"

    if busy:
        first = f"Статус {num}: 🔴 Занят {left_text}"
    else:
        first = f"Статус {num}: 🟢 Свободно"

    return first + "\n\n" + extra


def get_extra_text(caption):
    parts = caption.split("\n\n", 1)
    if len(parts) == 2:
        return parts[1]
    return "BEBRA RENT\nКрутой аккаунт\nЕсть машинка\n1337"


def make_image(num):
    path = f"status_{num}.png"
    img = Image.new("RGB", (1200, 800), (20, 20, 20))
    draw = ImageDraw.Draw(img)
    draw.text((300, 280), "BEBRA RENT", fill=(255, 255, 255))
    draw.text((470, 390), f"STATUS {num}", fill=(255, 255, 255))
    img.save(path)
    return path


async def create40(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()

    for i in range(1, STATUS_COUNT + 1):
        img_path = make_image(i)
        msg = await context.bot.send_photo(
            chat_id=CHANNEL_ID,
            photo=open(img_path, "rb"),
            caption=make_caption(i)
        )

        data["statuses"][str(msg.message_id)] = {
            "num": i,
            "chat_id": CHANNEL_ID
        }

        await asyncio.sleep(0.4)

    save_data(data)
    await update.effective_message.reply_text("Готово: 40 статусов созданы.")


async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.reply_to_message:
        return

    text = (msg.text or "").strip().lower()
    replied = msg.reply_to_message
    replied_id = str(replied.message_id)

    data = load_data()

    if replied_id not in data["statuses"]:
        return

    status = data["statuses"][replied_id]
    num = status["num"]

    old_caption = replied.caption or replied.text or ""
    extra = get_extra_text(old_caption)

    if text in ["0", "free", "свободно", "стоп", "stop"]:
        data["timers"].pop(replied_id, None)
        save_data(data)

        await context.bot.edit_message_caption(
            chat_id=CHANNEL_ID,
            message_id=int(replied_id),
            caption=make_caption(num, busy=False, extra=extra)
        )
        return

    if not re.fullmatch(r"\d+", text):
        return

    hours = int(text)
    if hours <= 0:
        return

    finish = int(time.time() + hours * 3600)

    data["timers"][replied_id] = {
        "num": num,
        "chat_id": CHANNEL_ID,
        "message_id": int(replied_id),
        "finish": finish,
        "extra": extra
    }

    save_data(data)


async def timer_loop(app):
    while True:
        data = load_data()
        now = int(time.time())
        changed = False

        for msg_id, timer in list(data["timers"].items()):
            left = timer["finish"] - now
            num = timer["num"]
            extra = timer.get("extra")

            try:
                if left <= 0:
                    await app.bot.edit_message_caption(
                        chat_id=CHANNEL_ID,
                        message_id=int(msg_id),
                        caption=make_caption(num, busy=False, extra=extra)
                    )
                    del data["timers"][msg_id]
                    changed = True
                else:
                    h = left // 3600
                    m = (left % 3600) // 60
                    s = left % 60
                    left_text = f"{h:02d}:{m:02d}:{s:02d}"

                    await app.bot.edit_message_caption(
                        chat_id=CHANNEL_ID,
                        message_id=int(msg_id),
                        caption=make_caption(num, busy=True, left_text=left_text, extra=extra)
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
