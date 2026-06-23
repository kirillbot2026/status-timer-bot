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

STATUSES_COUNT = 40
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

    data = {
        "statuses": {}
    }
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

    return f"{hours}ч {minutes:02d}м"


def make_caption(status_id: str) -> str:
    status = data["statuses"][status_id]

    text = status.get("text", "")
    busy_until = status.get("busy_until")

    lines = [f"Статус {status_id}", ""]

    if busy_until and busy_until > int(time.time()):
        left = busy_until - int(time.time())
        lines.append("🔴 Занят")
        lines.append(f"Осталось: {format_left(left)}")
    else:
        lines.append("🟢 Свободен")

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
        print(f"Не смог обновить статус {status_id}: {e}")


def find_status_by_message_id(message_id: int):
    for status_id, status in data["statuses"].items():
        if status.get("message_id") == message_id:
            return status_id
    return None


@dp.message(Command("setup"))
async def setup(message: Message):
    if message.chat.id != CHAT_ID:
        await message.answer("Эту команду нужно писать в нужной группе.")
        return

    if data["statuses"]:
        await message.answer("Статусы уже созданы. Если нужно заново — напиши /reset.")
        return

    for i in range(1, STATUSES_COUNT + 1):
        status_id = str(i)

        sent = await bot.send_photo(
            chat_id=CHAT_ID,
            photo=DEFAULT_PHOTO_URL,
            caption=f"Статус {status_id}\n\n🟢 Свободен"
        )

        data["statuses"][status_id] = {
            "message_id": sent.message_id,
            "photo": DEFAULT_PHOTO_URL,
            "text": "",
            "busy_until": None
        }

        save_data(data)
        await asyncio.sleep(0.3)

    await message.answer("Готово. Создал 40 статусов.")


@dp.message(Command("reset"))
async def reset(message: Message):
    if message.chat.id != CHAT_ID:
        return

    data["statuses"] = {}
    save_data(data)

    await message.answer("Список статусов очищен. Теперь напиши /setup.")


@dp.message(F.reply_to_message)
async def handle_reply(message: Message):
    if message.chat.id != CHAT_ID:
        return

    replied_id = message.reply_to_message.message_id
    status_id = find_status_by_message_id(replied_id)

    if not status_id:
        return

    status = data["statuses"][status_id]

    # 1. Если ответили фотографией — меняем картинку статуса
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
            await message.delete()
        except Exception as e:
            await message.answer(f"Не смог заменить фото: {e}")

        return

    # 2. Если ответили текстом
    if message.content_type != ContentType.TEXT:
        return

    text = message.text.strip()

    # Число = таймер в часах
    if text.isdigit():
        hours = int(text)

        if hours <= 0:
            await message.answer("Напиши число больше 0.")
            return

        status["busy_until"] = int(time.time()) + hours * 3600
        save_data(data)

        await edit_status(status_id)

        try:
            await message.delete()
        except Exception:
            pass

        return

    # + текст = нижний текст статуса
    if text.startswith("+"):
        new_text = text[1:].strip()

        status["text"] = new_text
        save_data(data)

        await edit_status(status_id)

        try:
            await message.delete()
        except Exception:
            pass

        return

    await message.answer(
        "Ответь на статус:\n"
        "6 — таймер на 6 часов\n"
        "+ текст — изменить нижний текст\n"
        "фото — изменить картинку"
    )


async def timer_loop():
    while True:
        now = int(time.time())

        for status_id, status in data["statuses"].items():
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
        raise RuntimeError("Нет BOT_TOKEN")

    if CHAT_ID == 0:
        raise RuntimeError("Нет CHAT_ID")

    asyncio.create_task(timer_loop())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
