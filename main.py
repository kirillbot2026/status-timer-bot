import asyncio
import json
import os
import re
import time
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ContentType
from aiogram.filters import Command
from aiogram.types import Message, InputMediaPhoto

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", "0"))

STATUSES_COUNT = 55
DATA_FILE = Path("statuses.json")

DEFAULT_PHOTO_URL = "https://picsum.photos/900/600"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def load_data():
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass

    data = {"statuses": {}}

    for i in range(1, STATUSES_COUNT + 1):
        data["statuses"][str(i)] = {
            "message_id": None,
            "photo": DEFAULT_PHOTO_URL,
            "text": "",
            "busy_until": None,
        }

    save_data(data)
    return data


def save_data(data):
    DATA_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


data = load_data()


def format_time_left(seconds):
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    return f"{hours}ч {minutes}м"


def make_caption(status_id):
    status = data["statuses"][status_id]

    lines = [f"Статус {status_id}", ""]

    busy_until = status.get("busy_until")

    if busy_until and busy_until > int(time.time()):
        left = busy_until - int(time.time())

        lines.append("🔴 Занят")
        lines.append(f"⏳ Осталось: {format_time_left(left)}")
    else:
        lines.append("🟢 Свободен")

    if status.get("text"):
        lines.append("")
        lines.append(status["text"])

    return "\n".join(lines)


async def update_status_message(status_id):
    status = data["statuses"][status_id]

    if not status.get("message_id"):
        return

    try:
        await bot.edit_message_caption(
            chat_id=CHAT_ID,
            message_id=status["message_id"],
            caption=make_caption(status_id),
        )
    except Exception:
        pass


def find_status_by_message(message_id):
    for status_id, status in data["statuses"].items():
        if status.get("message_id") == message_id:
            return status_id

    return None
    @dp.message(Command("setup"))
async def setup_command(message: Message):
    if message.chat.id != CHAT_ID:
        return

    for i in range(1, STATUSES_COUNT + 1):
        status_id = str(i)
        status = data["statuses"][status_id]

        if status.get("message_id"):
            continue

        sent = await bot.send_photo(
            chat_id=CHAT_ID,
            photo=status["photo"],
            caption=make_caption(status_id),
        )

        status["message_id"] = sent.message_id
        save_data(data)

    await message.answer("✅ Все статусы созданы")


@dp.message(F.reply_to_message)
async def reply_handler(message: Message):
    replied = message.reply_to_message

    status_id = find_status_by_message(replied.message_id)

    if not status_id:
        return

    status = data["statuses"][status_id]

    if message.content_type == ContentType.PHOTO:
        file_id = message.photo[-1].file_id

        status["photo"] = file_id
        save_data(data)

        try:
            await bot.edit_message_media(
                chat_id=CHAT_ID,
                message_id=status["message_id"],
                media=InputMediaPhoto(
                    media=file_id,
                    caption=make_caption(status_id),
                ),
            )
        except Exception:
            pass

        return

    if message.content_type != ContentType.TEXT:
        return

    text = message.text.strip()
        if text.startswith("+"):
        status["text"] = text[1:].strip()

        save_data(data)
        await update_status_message(status_id)
        return

    if text == "0":
        status["busy_until"] = None

        save_data(data)
        await update_status_message(status_id)
        return

    if text.lower() == "до утра":
        now = time.localtime()

        tomorrow_8 = int(
            time.mktime(
                (
                    now.tm_year,
                    now.tm_mon,
                    now.tm_mday + 1,
                    8,
                    0,
                    0,
                    0,
                    0,
                    -1,
                )
            )
        )

        status["busy_until"] = tomorrow_8

        save_data(data)
        await update_status_message(status_id)
        return

    match = re.match(r"^(\d+)(?:\s+(\d+))?$", text)

    if match:
        hours = int(match.group(1))

        if match.group(2):
            hours += int(match.group(2))

        status["busy_until"] = int(time.time()) + hours * 3600

        save_data(data)
        await update_status_message(status_id)
        return
        async def timer_loop():
    while True:
        now = int(time.time())

        changed = False

        for status_id, status in data["statuses"].items():
            busy_until = status.get("busy_until")

            if busy_until and busy_until <= now:
                status["busy_until"] = None

                await update_status_message(status_id)
                changed = True

        if changed:
            save_data(data)

        await asyncio.sleep(60)


async def main():
    asyncio.create_task(timer_loop())

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
