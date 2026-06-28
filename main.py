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


def load_data():
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)

    if DATA_FILE.exists():
        try:
            data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    else:
        data = {}

    if "groups" not in data:
        data = {"groups": {}}

    return data


data = load_data()


def save_data():
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def get_group(chat_id: int):
    group_id = str(chat_id)

    if group_id not in data["groups"]:
        data["groups"][group_id] = {
            "setup_running": False,
            "statuses": {}
        }

    if "setup_running" not in data["groups"][group_id]:
        data["groups"][group_id]["setup_running"] = False

    if "statuses" not in data["groups"][group_id]:
        data["groups"][group_id]["statuses"] = {}

    return data["groups"][group_id]


def get_status_numbers(group):
    numbers = []

    for key in group["statuses"].keys():
        if str(key).isdigit():
            numbers.append(int(key))

    return sorted(numbers)


def next_status_number(group):
    numbers = get_status_numbers(group)

    if not numbers:
        return 1

    return max(numbers) + 1


def format_left(seconds: int) -> str:
    seconds = max(0, seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    return f"{hours}ч {minutes:02d}м"


def make_caption(chat_id: int, status_id: str) -> str:
    group = get_group(chat_id)
    status = group["statuses"][status_id]

    busy_until = status.get("busy_until")
    text = status.get("text", "")
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
    group = get_group(chat_id)
    status = group["statuses"].get(status_id)

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
        save_data()

    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            print(f"Ошибка обновления статуса {status_id} в чате {chat_id}: {e}")

    except Exception as e:
        print(f"Ошибка обновления статуса {status_id} в чате {chat_id}: {e}")


def find_status_by_message_id(chat_id: int, message_id: int):
    group = get_group(chat_id)

    for status_id, status in group["statuses"].items():
        if status.get("message_id") == message_id:
            return status_id

    return None


async def create_statuses(message: Message):
    chat_id = message.chat.id
    group = get_group(chat_id)

    if group["setup_running"]:
        await message.answer("Создание статусов уже идёт. Подожди.")
        return

    start_from = next_status_number(group)

    if start_from > STATUSES_COUNT:
        await message.answer("Уже есть все 55 статусов.")
        return

    group["setup_running"] = True
    save_data()

    try:
        for i in range(start_from, STATUSES_COUNT + 1):
            status_id = str(i)
            caption = f"Статус {status_id}\n\n🟢 Свободен"

            sent = await bot.send_photo(
                chat_id=chat_id,
                photo=DEFAULT_PHOTO_URL,
                caption=caption
            )

            group["statuses"][status_id] = {
                "message_id": sent.message_id,
                "photo": DEFAULT_PHOTO_URL,
                "text": "",
                "busy_until": None,
                "last_caption": caption
            }

            save_data()
            await asyncio.sleep(0.5)

    finally:
        group["setup_running"] = False
        save_data()

    await message.answer("Готово. Статусы созданы до 55.")


@dp.message(Command("setup"))
async def setup(message: Message):
    group = get_group(message.chat.id)

    if group["statuses"]:
        await message.answer("Статусы уже есть. Если не все — напиши /continue.")
        return

    await create_statuses(message)


@dp.message(Command("continue"))
async def continue_setup(message: Message):
    await create_statuses(message)


@dp.message(Command("reset"))
async def reset(message: Message):
    group = get_group(message.chat.id)
    group["statuses"] = {}
    group["setup_running"] = False
    save_data()

    await message.answer("База статусов этого чата очищена. Старые сообщения не удалены. Теперь напиши /setup.")


@dp.message(Command("help"))
async def help_cmd(message: Message):
    await message.answer(
        "Команды:\n"
        "/setup — создать 55 статусов\n"
        "/continue — продолжить создание\n"
        "/reset — очистить базу этого чата\n\n"
        "Ответь на статус:\n"
        "6 — занять на 6 часов\n"
        "12 — занять на 12 часов\n"
        "0 — сделать свободным\n"
        "+ текст — изменить нижний текст\n"
        "фото — заменить картинку"
    )


@dp.message(F.reply_to_message, ~F.text.startswith("/"))
async def handle_reply(message: Message):
    chat_id = message.chat.id
    status_id = find_status_by_message_id(
        chat_id,
        message.reply_to_message.message_id
    )

    if not status_id:
        return

    group = get_group(chat_id)
    status = group["statuses"][status_id]

    if message.content_type == ContentType.PHOTO:
        file_id = message.photo[-1].file_id
        status["photo"] = file_id
        save_data()

        try:
            caption = make_caption(chat_id, status_id)

            await bot.edit_message_media(
                chat_id=chat_id,
                message_id=status["message_id"],
                media=InputMediaPhoto(
                    media=file_id,
                    caption=caption
                )
            )

            status["last_caption"] = caption
            save_data()

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
            save_data()
            await edit_status(chat_id, status_id, force=True)
            return

        if hours > 999:
            await message.answer("Слишком большое число часов. Максимум 999.")
            return

        status["busy_until"] = int(time.time()) + hours * 3600
        save_data()

        await edit_status(chat_id, status_id, force=True)
        return

    if text.startswith("+"):
        status["text"] = text[1:].strip()
        save_data()
        await edit_status(chat_id, status_id, force=True)
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
        to_edit = []

        now = int(time.time())

        for chat_id_str, group in data["groups"].items():
            chat_id = int(chat_id_str)

            for status_id, status in group["statuses"].items():
                busy_until = status.get("busy_until")

                if not busy_until:
                    continue

                old_caption = status.get("last_caption", "")
                new_caption = make_caption(chat_id, status_id)

                if busy_until <= now:
                    status["busy_until"] = None
                    new_caption = make_caption(chat_id, status_id)

                if new_caption != old_caption:
                    to_edit.append((chat_id, status_id))

        save_data()

        for chat_id, status_id in to_edit:
            await edit_status(chat_id, status_id, force=True)
            await asyncio.sleep(0.2)

        await asyncio.sleep(60)


async def main():
    if not BOT_TOKEN:
        raise RuntimeError("Нет BOT_TOKEN")

    asyncio.create_task(timer_loop())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
