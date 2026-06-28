import asyncio
import json
import os
import time
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ContentType
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import Message, InputMediaPhoto

BOT_TOKEN = os.getenv("BOT_TOKEN")

STATUSES_COUNT = 55
DATA_FILE = Path(os.getenv("DATA_FILE", "statuses.json"))

DEFAULT_PHOTO_URL = os.getenv(
    "DEFAULT_PHOTO_URL",
    "https://picsum.photos/900/600"
)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
data_lock = asyncio.Lock()


def save_data(data):
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def load_data():
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)

    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass

    data = {"chats": {}}
    save_data(data)
    return data


data = load_data()

if "chats" not in data:
    data = {"chats": {}}
    save_data(data)


def chat_key(chat_id: int) -> str:
    return str(chat_id)


def get_chat_data(chat_id: int):
    key = chat_key(chat_id)

    if key not in data["chats"]:
        data["chats"][key] = {"statuses": {}}

    return data["chats"][key]


def format_left(seconds: int) -> str:
    seconds = max(0, seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    return f"{hours}ч {minutes:02d}м"


def make_caption(chat_id: int, status_id: str) -> str:
    chat_data = get_chat_data(chat_id)
    status = chat_data["statuses"][status_id]

    text = status.get("text", "")
    busy_until = status.get("busy_until")
    now = int(time.time())

    lines = [f"Статус {status_id}", ""]

    if busy_until and busy_until > now:
        lines.append("🔴 Занят")
        lines.append(f"Осталось: {format_left(busy_until - now)}")
    else:
        lines.append("🟢 Свободен")

    if text:
        lines.extend(["", text])

    return "\n".join(lines)


async def edit_status(chat_id: int, status_id: str, force: bool = False):
    chat_data = get_chat_data(chat_id)
    status = chat_data["statuses"].get(status_id)

    if not status:
        return

    caption = make_caption(chat_id, status_id)

    if not force and status.get("last_caption") == caption:
        return

    try:
        await bot.edit_message_caption(
            chat_id=chat_id,
            message_id=status["message_id"],
            caption=caption
        )

        status["last_caption"] = caption
        save_data(data)

    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            print(f"Ошибка обновления статуса {status_id} в чате {chat_id}: {e}")

    except Exception as e:
        print(f"Ошибка обновления статуса {status_id} в чате {chat_id}: {e}")


def find_status_by_message_id(chat_id: int, message_id: int):
    chat_data = get_chat_data(chat_id)

    for status_id, status in chat_data["statuses"].items():
        if status.get("message_id") == message_id:
            return status_id

    return None


@dp.message(Command("setup"))
async def setup(message: Message):
    async with data_lock:
        chat_data = get_chat_data(message.chat.id)

        if chat_data["statuses"]:
            await message.answer("Статусы уже созданы. Если не все — напиши /continue.")
            return

        for i in range(1, STATUSES_COUNT + 1):
            status_id = str(i)
            caption = f"Статус {status_id}\n\n🟢 Свободен"

            sent = await bot.send_photo(
                chat_id=message.chat.id,
                photo=DEFAULT_PHOTO_URL,
                caption=caption
            )

            chat_data["statuses"][status_id] = {
                "message_id": sent.message_id,
                "photo": DEFAULT_PHOTO_URL,
                "text": "",
                "busy_until": None,
                "last_caption": caption
            }

            save_data(data)
            await asyncio.sleep(0.5)

    await message.answer("Готово. Создал 55 статусов.")


@dp.message(Command("continue"))
async def continue_setup(message: Message):
    async with data_lock:
        chat_data = get_chat_data(message.chat.id)
        existing = len(chat_data["statuses"])

        if existing >= STATUSES_COUNT:
            await message.answer("Уже есть все 55 статусов.")
            return

        for i in range(existing + 1, STATUSES_COUNT + 1):
            status_id = str(i)
            caption = f"Статус {status_id}\n\n🟢 Свободен"

            sent = await bot.send_photo(
                chat_id=message.chat.id,
                photo=DEFAULT_PHOTO_URL,
                caption=caption
            )

            chat_data["statuses"][status_id] = {
                "message_id": sent.message_id,
                "photo": DEFAULT_PHOTO_URL,
                "text": "",
                "busy_until": None,
                "last_caption": caption
            }

            save_data(data)
            await asyncio.sleep(0.5)

    await message.answer("Готово. Досоздал статусы до 55.")


@dp.message(Command("reset"))
async def reset(message: Message):
    async with data_lock:
        chat_data = get_chat_data(message.chat.id)
        chat_data["statuses"] = {}
        save_data(data)

    await message.answer("Список статусов в этом чате очищен. Теперь напиши /setup.")


@dp.message(Command("help"))
async def help_cmd(message: Message):
    await message.answer(
        "Как пользоваться:\n\n"
        "Ответь на сообщение-статус:\n"
        "6 — занять на 6 часов\n"
        "12 — занять на 12 часов\n"
        "0 — сделать свободным\n"
        "+ текст — изменить нижний текст\n"
        "фото — заменить картинку\n\n"
        "Команды:\n"
        "/setup — создать 55 статусов\n"
        "/continue — досоздать недостающие\n"
        "/reset — очистить базу статусов этого чата"
    )


@dp.message(F.reply_to_message)
async def handle_reply(message: Message):
    replied_id = message.reply_to_message.message_id
    status_id = find_status_by_message_id(message.chat.id, replied_id)

    if not status_id:
        return

    async with data_lock:
        chat_data = get_chat_data(message.chat.id)
        status = chat_data["statuses"][status_id]

        if message.content_type == ContentType.PHOTO:
            file_id = message.photo[-1].file_id
            status["photo"] = file_id
            save_data(data)

            try:
                caption = make_caption(message.chat.id, status_id)

                await bot.edit_message_media(
                    chat_id=message.chat.id,
                    message_id=status["message_id"],
                    media=InputMediaPhoto(
                        media=file_id,
                        caption=caption
                    )
                )

                status["last_caption"] = caption
                save_data(data)

            except Exception as e:
                await message.answer(f"Не смог заменить фото: {e}")

            return

        if message.content_type != ContentType.TEXT:
            return

        text = message.text.strip()

        if text.isdigit():
            hours = int(text)

            if hours == 0:
                status["busy_until"] = None
                save_data(data)
                await edit_status(message.chat.id, status_id, force=True)
                return

            if hours > 999:
                await message.answer("Слишком большое число часов. Максимум 999.")
                return

            status["busy_until"] = int(time.time()) + hours * 3600
            save_data(data)

            await edit_status(message.chat.id, status_id, force=True)
            return

        if text.startswith("+"):
            status["text"] = text[1:].strip()
            save_data(data)
            await edit_status(message.chat.id, status_id, force=True)
            return

    await message.answer(
        "Ответь на статус:\n"
        "6 — таймер на 6 часов\n"
        "0 — сделать свободным\n"
        "+ текст — изменить нижний текст\n"
        "фото — изменить картинку"
    )


async def timer_loop():
    while True:
        async with data_lock:
            now = int(time.time())

            for chat_id_str, chat_data in data["chats"].items():
                chat_id = int(chat_id_str)

                for status_id, status in list(chat_data["statuses"].items()):
                    busy_until = status.get("busy_until")

                    if not busy_until:
                        continue

                    if busy_until <= now:
                        status["busy_until"] = None
                        save_data(data)
                        await edit_status(chat_id, status_id, force=True)
                    else:
                        await edit_status(chat_id, status_id)

                    await asyncio.sleep(0.1)

        await asyncio.sleep(60)


async def main():
    if not BOT_TOKEN:
        raise RuntimeError("Нет BOT_TOKEN")

    asyncio.create_task(timer_loop())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
