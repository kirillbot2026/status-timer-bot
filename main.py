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
            "last_created": 0,
            "setup_running": False,
            "statuses": {}
        }

    group = data["groups"][group_id]

    group.setdefault("last_created", 0)
    group.setdefault("setup_running", False)
    group.setdefault("statuses", {})

    return group


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


def sync_last_created(group):
    max_number = 0

    for key in group["statuses"].keys():
        if str(key).isdigit():
            max_number = max(max_number, int(key))

    group["last_created"] = max(group.get("last_created", 0), max_number)


async def create_missing_statuses(message: Message):
    chat_id = message.chat.id

    async with data_lock:
        group = get_group(chat_id)
        sync_last_created(group)

        if group["setup_running"]:
            await message.answer("Создание статусов уже идёт. Подожди.")
            return

        if group["last_created"] >= STATUSES_COUNT:
            await message.answer("Уже есть все 55 статусов.")
            return

        group["setup_running"] = True
        start_from = group["last_created"] + 1
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

            async with data_lock:
                group = get_group(chat_id)

                group["statuses"][status_id] = {
                    "message_id": sent.message_id,
                    "photo": DEFAULT_PHOTO_URL,
                    "text": "",
                    "busy_until": None,
                    "last_caption": caption
                }

                group["last_created"] = i
                save_data()

            await asyncio.sleep(0.6)

        await message.answer("Готово. Статусы созданы до 55.")

    except Exception as e:
        await message.answer(f"Создание остановилось на ошибке. Напиши /continue.\n\nОшибка: {e}")

    finally:
        async with data_lock:
            group = get_group(chat_id)
            group["setup_running"] = False
            save_data()


@dp.message(Command("setup"))
async def setup(message: Message):
    group = get_group(message.chat.id)
    sync_last_created(group)

    if group["last_created"] > 0:
        await message.answer("Статусы уже есть. Если не все — напиши /continue.")
        save_data()
        return

    await create_missing_statuses(message)


@dp.message(Command("continue"))
async def continue_setup(message: Message):
    await create_missing_statuses(message)


@dp.message(Command("reset"))
async def reset(message: Message):
    async with data_lock:
        group = get_group(message.chat.id)
        group["statuses"] = {}
        group["last_created"] = 0
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

    if message.content_type == ContentType.PHOTO:
        file_id = message.photo[-1].file_id

        async with data_lock:
            group = get_group(chat_id)
            status = group["statuses"][status_id]
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

            async with data_lock:
                status["last_caption"] = caption
                save_data()

        except Exception as e:
            await message.answer(f"Не смог заменить фото: {e}")

        return

    if message.content_type != ContentType.TEXT:
        return

    text = message.text.strip()

    async with data_lock:
        group = get_group(chat_id)
        status = group["statuses"][status_id]

        if text.isdigit():
            hours = int(text)

            if hours == 0:
                status["busy_until"] = None
                save_data()
                force = True
            elif hours > 999:
                await message.answer("Слишком большое число часов. Максимум 999.")
                return
            else:
                status["busy_until"] = int(time.time()) + hours * 3600
                save_data()
                force = True

        elif text.startswith("+"):
            status["text"] = text[1:].strip()
            save_data()
            force = True

        else:
            await message.answer(
                "Ответь на статус:\n"
                "6 — таймер на 6 часов\n"
                "0 — сделать свободным\n"
                "+ текст — изменить нижний текст\n"
                "фото — изменить картинку"
            )
            return

    await edit_status(chat_id, status_id, force=force)


async def timer_loop():
    while True:
        to_edit = []

        async with data_lock:
            now = int(time.time())

            for chat_id_str, group in data["groups"].items():
                chat_id = int(chat_id_str)

                for status_id, status in group["statuses"].items():
                    busy_until = status.get("busy_until")

                    if not busy_until:
                        continue

                    old_caption = status.get("last_caption", "")

                    if busy_until <= now:
                        status["busy_until"] = None
                        new_caption = make_caption(chat_id, status_id)
                    else:
                        new_caption = make_caption(chat_id, status_id)

                    if new_caption != old_caption:
                        to_edit.append((chat_id, status_id))

            save_data()

        for chat_id, status_id in to_edit:
            await edit_status(chat_id, status_id, force=True)
            await asyncio.sleep(0.25)

        await asyncio.sleep(60)


async def main():
    if not BOT_TOKEN:
        raise RuntimeError("Нет BOT_TOKEN")

    asyncio.create_task(timer_loop())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
