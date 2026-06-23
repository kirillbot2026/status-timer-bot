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

STATUS_COUNT = 40
DATA_FILE = Path("data.json")


DEFAULT_EXTRA = """BEBRA RENT
Крутой аккаунт
Есть машинка
1337"""


def load_data():
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text())
    return {"statuses": {}, "timers": {}}


def save_data(data):
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def status_text(num, busy=False, left="00:00:00", extra=DEFAULT_EXTRA):
    if busy:
        first = f"Статус {num}: 🔴 Занят {left}"
    else:
        first = f"Статус {num}: 🟢 Свободно"

    return first + "\n\n" + extra


def make_image(num):
    path = f"status_{num}.png"

    img = Image.new("RGB", (1200, 800), (18, 18, 22))
    draw = ImageDraw.Draw(img)

    draw.rectangle((60, 60, 1140, 740), outline=(255, 255, 255), width=6)
    draw.text((310, 270), "BEBRA RENT", fill=(255, 255, 255))
    draw.text((460, 390), f"STATUS {num}", fill=(255, 255, 255))

    img.save(path)
    return path


async def create40(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = {"statuses": {}, "timers": {}}

    for num in range(1, STATUS_COUNT + 1):
        img_path = make_image(num)

        with open(img_path, "rb") as photo:
            msg = await context.bot.send_photo(
                chat_id=CHANNEL_ID,
                photo=photo,
                caption=status_text(num)
            )

        data["statuses"][str(num)] = {
            "message_id": msg.message_id,
            "extra": DEFAULT_EXTRA
        }

        await asyncio.sleep(0.4)

    save_data(data)

    if update.effective_message:
        await update.effective_message.reply_text("Готово. Создано 40 статусов.")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.text:
        return

    text = msg.text.strip().lower()

    match = re.fullmatch(r"(\d{1,2})\s+(\d+|0|free|stop|стоп|свободно)", text)
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
    extra = status.get("extra", DEFAULT_EXTRA)

    if value in ["0", "free", "stop", "стоп", "свободно"]:
        data["timers"].pop(str(num), None)
        save_data(data)

        try:
            await context.bot.edit_message_caption(
                chat_id=CHANNEL_ID,
                message_id=message_id,
                caption=status_text(num, busy=False, extra=extra)
            )
        except Exception as e:
            print("FREE ERROR:", e)

        return

    hours = int(value)
    finish = int(time.time() + hours * 3600)

    data["timers"][str(num)] = {
        "finish": finish
    }

    save_data(data)

    left = f"{hours:02d}:00:00"

    try:
        await context.bot.edit_message_caption(
            chat_id=CHANNEL_ID,
            message_id=message_id,
            caption=status_text(num, busy=True, left=left, extra=extra)
        )
    except Exception as e:
        print("START ERROR:", e)


async def timer_loop(app):
    while True:
        data = load_data()
        now = int(time.time())
        changed = False

        for num_str, timer in list(data["timers"].items()):
            status = data["statuses"].get(num_str)
            if not status:
                continue

            num = int(num_str)
            message_id = status["message_id"]
            extra = status.get("extra", DEFAULT_EXTRA)

            left_seconds = int(timer["finish"] - now)

            try:
                if left_seconds <= 0:
                    await app.bot.edit_message_caption(
                        chat_id=CHANNEL_ID,
                        message_id=message_id,
                        caption=status_text(num, busy=False, extra=extra)
                    )
                    del data["timers"][num_str]
                    changed = True
                else:
                    h = left_seconds // 3600
                    m = (left_seconds % 3600) // 60
                    s = left_seconds % 60
                    left = f"{h:02d}:{m:02d}:{s:02d}"

                    await app.bot.edit_message_caption(
                        chat_id=CHANNEL_ID,
                        message_id=message_id,
                        caption=status_text(num, busy=True, left=left, extra=extra)
                    )

            except Exception as e:
                print("TIMER ERROR:", e)

        if changed:
            save_data(data)

        await asyncio.sleep(60)


async def post_init(app):
    asyncio.create_task(timer_loop(app))


def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN не найден")
    if not CHANNEL_ID:
        raise RuntimeError("CHANNEL_ID не найден")

    app = Application.builder().token(TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("create40", create40))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.run_polling()


if __name__ == "__main__":
    main()
