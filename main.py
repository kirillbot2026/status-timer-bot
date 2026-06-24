import asyncio
import json
import os
import time
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ContentType
from aiogram.types import Message, InputMediaPhoto
from aiogram.filters import Command


BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", "0"))

STATUSES_COUNT = 55
DATA_FILE = Path("statuses.json")

DEFAULT_PHOTO_URL = os.getenv(
    "DEFAULT_PHOTO_URL",
    "https://picsum.photos/900/600"
)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def load_data():
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    data = {"statuses": {}}
    save_data(data)
    return data


def save_data(data):
    DATA_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


data = load_data()


def format_left(seconds: int) -> str:
    if seconds < 0:
        seconds = 0
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    return f"{hours} {minutes:02d}"


def make_caption(status_id: str) -> str:
    status = data["statuses"][status_id]
    text = status.get("text", "")
    busy_until = status.get("busy_until")

    lines = [f" {status_id}", ""]

    if busy_until and busy_until > int(time.time()):
        left = busy_until - int(time.time())
        lines.append(" ")
        lines.append(f": {format_left(left)}")
    else:
        lines.append(" ")

    if text:
        lines.append(text)

    return "\n".join(lines)


async def edit_status(status_id: str):
    status = data["statuses"][status_id]

    try:
        await bot.edit_message_caption(
            chat_id=CHAT_ID,
            message_id=status["message_id"],
            caption=make_caption(status_id)
        )
    except Exception as e:
        print(f"   {status_id}: {e}")


def find_status_by_message_id(message_id: int):
    for status_id, status in data["statuses"].items():
        if status.get("message_id") == message_id:
            return status_id
    return None


@dp.message(Command("setup"))
async def setup(message: Message):
    if message.chat.id != CHAT_ID:
        await message.answer("      .")
        return

    if data["statuses"]:
        await message.answer("  .      /continue.")
        return

    for i in range(1, STATUSES_COUNT + 1):
        status_id = str(i)

        sent = await bot.send_photo(
            chat_id=CHAT_ID,
            photo=DEFAULT_PHOTO_URL,
            caption=f" {status_id}\n\n "
        )

        data["statuses"][status_id] = {
            "message_id": sent.message_id,
            "photo": DEFAULT_PHOTO_URL,
            "text": "",
            "busy_until": None
        }

        save_data(data)
        await asyncio.sleep(0.5)

    await message.answer(".  40 .")


@dp.message(Command("continue"))
async def continue_setup(message: Message):
    if message.chat.id != CHAT_ID:
        return

    existing = len(data["statuses"])

    if existing >= STATUSES_COUNT:
        await message.answer("   40 .")
        return

    for i in range(existing + 1, STATUSES_COUNT + 1):
        status_id = str(i)

        sent = await bot.send_photo(
            chat_id=CHAT_ID,
            photo=DEFAULT_PHOTO_URL,
            caption=f" {status_id}\n\n "
        )

        data["statuses"][status_id] = {
            "message_id": sent.message_id,
            "photo": DEFAULT_PHOTO_URL,
            "text": "",
            "busy_until": None
        }

        save_data(data)
        await asyncio.sleep(0.5)

    await message.answer(".    40.")


@dp.message(Command("reset"))
async def reset(message: Message):
    if message.chat.id != CHAT_ID:
        return

    data["statuses"] = {}
    save_data(data)

    await message.answer("  .   /setup.")


@dp.message(F.reply_to_message)
async def handle_reply(message: Message):
    if message.chat.id != CHAT_ID:
        return

    replied_id = message.reply_to_message.message_id
    status_id = find_status_by_message_id(replied_id)

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
                    caption=make_caption(status_id)
                )
            )
        except Exception as e:
            await message.answer(f"   : {e}")

        return

    if message.content_type != ContentType.TEXT:
        return

    text = message.text.strip()


    if text.lower() == "":
        status["busy_until"] = int(time.time()) + 24 * 3600
        status["text"] = " "
        save_data(data)
        await edit_status(status_id)
        return

    if text.lower() == " ":
        now = time.localtime()
        tomorrow = time.mktime((
            now.tm_year, now.tm_mon, now.tm_mday + 1,
            8, 0, 0, 0, 0, -1
        ))
        status["busy_until"] = int(tomorrow)
        save_data(data)
        await edit_status(status_id)
        return

    parts = text.split(maxsplit=1)

    if parts and parts[0].isdigit():
        hours = int(parts[0])

        if hours == 0:
            status["busy_until"] = None
            save_data(data)
            await edit_status(status_id)
            return

        status["busy_until"] = int(time.time()) + hours * 3600
        save_data(data)

        await edit_status(status_id)
        return

    if text.startswith("+"):
        new_text = text[1:].strip()
        status["text"] = new_text
        save_data(data)

        await edit_status(status_id)
        return

    await message.answer(
        "  :\n"
        "6    6 \n"
        "0   \n"
        "+     \n"
        "   "
    )


async def timer_loop():
    while True:
        now = int(time.time())

        for status_id, status in list(data["statuses"].items()):
            busy_until = status.get("busy_until")

            if busy_until:
                if busy_until <= now:
                    status["busy_until"] = None
                    save_data(data)
                    await edit_status(status_id)
                else:
                    await edit_status(status_id)

        await asyncio.sleep(60)


async def main():
    if not BOT_TOKEN:
        raise RuntimeError(" BOT_TOKEN")

    if CHAT_ID == 0:
        raise RuntimeError(" CHAT_ID")

    asyncio.create_task(timer_loop())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
