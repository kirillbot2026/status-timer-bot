import asyncio
import json
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ContentType
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.types import Message, InputMediaPhoto

BOT_TOKEN = os.getenv("BOT_TOKEN")

STATUSES_COUNT = 55
DATA_FILE = Path(os.getenv("DATA_FILE", "statuses.json"))
DEFAULT_PHOTO_URL = os.getenv("DEFAULT_PHOTO_URL", "https://picsum.photos/900/600")

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


def parse_duration_to_seconds(text: str):
    text = text.lower().replace(",", " ").replace(".", " ")
    tokens = re.findall(r"(\d+)\s*([а-яa-z]*)", text)

    if not tokens:
        return None

    total_minutes = 0
    seen_hours = False

    for index, (num_str, unit) in enumerate(tokens):
        num = int(num_str)
        unit = unit.strip().lower()

        if unit.startswith("м") or unit.startswith("min"):
            total_minutes += num
        elif unit.startswith("ч") or unit.startswith("h") or unit.startswith("час"):
            total_minutes += num * 60
            seen_hours = True
        elif unit == "":
            if index == 0:
                total_minutes += num * 60
                seen_hours = True
            else:
                if seen_hours:
                    total_minutes += num
                else:
                    total_minutes += num * 60
                    seen_hours = True

    if total_minutes <= 0:
        return None

    return total_minutes * 60


def seconds_until_moscow_10():
    moscow = ZoneInfo("Europe/Moscow")
    now = datetime.now(moscow)
    target = now.replace(hour=10, minute=0, second=0, microsecond=0)

    if now >= target:
        target += timedelta(days=1)

    return int((target - now).total_seconds())


def sender_name(message: Message):
    user = message.from_user

    if not user:
        return "Неизвестный"

    if user.username:
        return f"@{user.username}"

    return user.full_name


def make_caption(chat_id: int, status_id: str) -> str:
    group = get_group(chat_id)
    status = group["statuses"][status_id]

    busy_until = status.get("busy_until")
    reservation_until = status.get("reservation_until")
    text = status.get("text", "")
    now = int(time.time())

    lines = [f"Статус {status_id}", ""]

    if busy_until and busy_until > now:
        lines.append("🔴 Занят")
        lines.append(f"Осталось: {format_left(busy_until - now)}")
    else:
        lines.append("🟢 Свободен")

    if reservation_until and reservation_until > now:
        lines.append(f"Бронь: {format_left(reservation_until - now)}")

    if text:
        lines.append(text)

    return "\n".join(lines)


async def safe_answer(message: Message, text: str):
    try:
        await message.answer(text)
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after + 1)
        try:
            await message.answer(text)
        except Exception:
            pass
    except Exception:
        pass


async def safe_send_reply(chat_id: int, reply_to_message_id: int, text: str):
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_to_message_id=reply_to_message_id
        )
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after + 1)
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_to_message_id=reply_to_message_id
            )
        except Exception:
            pass
    except Exception:
        pass


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

    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after + 1)
        await edit_status(chat_id, status_id, force=True)

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
            await safe_answer(message, "Создание статусов уже идёт. Подожди.")
            return

        if group["last_created"] >= STATUSES_COUNT:
            await safe_answer(message, "Уже есть все 55 статусов.")
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
                    "reservation_until": None,
                    "last_caption": caption
                }

                group["last_created"] = i
                save_data()

            await asyncio.sleep(1.2)

        await safe_answer(message, "Готово. Статусы созданы до 55.")

    except TelegramRetryAfter as e:
        print(f"Flood limit. Retry after {e.retry_after}")
        await asyncio.sleep(e.retry_after + 1)

    except Exception as e:
        print(f"Создание остановилось: {e}")

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
        await safe_answer(message, "Статусы уже есть. Если не все — напиши /continue.")
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

    await safe_answer(message, "База очищена. Старые сообщения не удалены. Теперь напиши /setup.")


@dp.message(Command("help"))
async def help_cmd(message: Message):
    await safe_answer(
        message,
        "Функции бота:\n\n"
        "Основные команды:\n"
        "/setup — создать 55 статусов\n"
        "/continue — продолжить создание, если остановилось\n"
        "/reset — очистить базу этого чата\n"
        "/help — показать помощь\n\n"
        "Ответом на статус:\n"
        "6 — занять статус на 6 часов\n"
        "12 — занять статус на 12 часов\n"
        "0 — сделать статус свободным\n"
        "До утра — занять до 10:00 по МСК\n\n"
        "Таймер + подарок:\n"
        "6+2 — поставить 8 часов, удалить сообщение и написать ответом на статус:\n"
        "на 6 часов + 2 часа подарок\n"
        "ник человека\n\n"
        "Таймер + обычный текст:\n"
        "6 попка — поставить таймер на 6 часов, текст не менять\n"
        "6 любой текст — поставить таймер на 6 часов, текст не менять\n\n"
        "Таймер + нижний текст:\n"
        "6 +попка — поставить 6 часов и нижний текст попка\n"
        "12 +клиент Иван — поставить 12 часов и нижний текст клиент Иван\n\n"
        "Нижний текст без таймера:\n"
        "+не — поставить нижний текст не\n"
        "+любой текст — изменить нижний текст\n\n"
        "Бронь:\n"
        "Бронь 3 часа — добавить бронь на 3 часа\n"
        "Бронь 1ч 30м — добавить бронь на 1ч 30м\n"
        "Бронь 40м — добавить бронь на 40 минут\n"
        "Бронь 2 — добавить бронь на 2 часа\n\n"
        "Фото:\n"
        "Ответь фотографией на статус — заменить картинку статуса\n\n"
        "Когда бронь закончится, бот уберёт строку брони и напишет предупреждение в чат."
    )


@dp.message(F.reply_to_message, ~F.text.startswith("/"))
async def handle_reply(message: Message):
    chat_id = message.chat.id
    replied_message_id = message.reply_to_message.message_id

    status_id = find_status_by_message_id(chat_id, replied_message_id)

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
                media=InputMediaPhoto(media=file_id, caption=caption)
            )

            async with data_lock:
                status["last_caption"] = caption
                save_data()

        except Exception as e:
            await safe_answer(message, f"Не смог заменить фото: {e}")

        return

    if message.content_type != ContentType.TEXT:
        return

    text = message.text.strip()
    text_lower = text.lower()

    gift_match = re.fullmatch(r"(\d+)\s*\+\s*(\d+)", text)

    if gift_match:
        base_hours = int(gift_match.group(1))
        gift_hours = int(gift_match.group(2))
        total_hours = base_hours + gift_hours

        async with data_lock:
            group = get_group(chat_id)
            status = group["statuses"][status_id]
            status["busy_until"] = int(time.time()) + total_hours * 3600
            save_data()

        try:
            await message.delete()
        except Exception:
            pass

        await safe_send_reply(
            chat_id=chat_id,
            reply_to_message_id=replied_message_id,
            text=(
                f"на {base_hours} часов + {gift_hours} часа подарок\n"
                f"{sender_name(message)}"
            )
        )

        await edit_status(chat_id, status_id, force=True)
        return

    if text_lower == "до утра":
        seconds = seconds_until_moscow_10()

        async with data_lock:
            group = get_group(chat_id)
            status = group["statuses"][status_id]
            status["busy_until"] = int(time.time()) + seconds
            save_data()

        await edit_status(chat_id, status_id, force=True)
        return

    if text_lower.startswith("бронь"):
        duration_text = text[5:].strip()
        seconds = parse_duration_to_seconds(duration_text)

        if not seconds:
            await safe_answer(message, "Не понял время брони. Пример: Бронь 1ч 30м")
            return

        async with data_lock:
            group = get_group(chat_id)
            status = group["statuses"][status_id]
            status["reservation_until"] = int(time.time()) + seconds
            save_data()

        await edit_status(chat_id, status_id, force=True)
        return

    if text.isdigit():
        hours = int(text)

        async with data_lock:
            group = get_group(chat_id)
            status = group["statuses"][status_id]

            if hours == 0:
                status["busy_until"] = None
            elif hours > 999:
                await safe_answer(message, "Слишком большое число часов. Максимум 999.")
                return
            else:
                status["busy_until"] = int(time.time()) + hours * 3600

            save_data()

        await edit_status(chat_id, status_id, force=True)
        return

    timer_with_plus_text = re.fullmatch(r"(\d+)\s+\+(.+)", text)

    if timer_with_plus_text:
        hours = int(timer_with_plus_text.group(1))
        extra_text = timer_with_plus_text.group(2)

        async with data_lock:
            group = get_group(chat_id)
            status = group["statuses"][status_id]

            if hours > 999:
                await safe_answer(message, "Слишком большое число часов. Максимум 999.")
                return

            status["busy_until"] = int(time.time()) + hours * 3600
            status["text"] = extra_text
            save_data()

        await edit_status(chat_id, status_id, force=True)
        return

    timer_with_any_text = re.fullmatch(r"(\d+)\s+(.+)", text)

    if timer_with_any_text:
        hours = int(timer_with_any_text.group(1))

        async with data_lock:
            group = get_group(chat_id)
            status = group["statuses"][status_id]

            if hours > 999:
                await safe_answer(message, "Слишком большое число часов. Максимум 999.")
                return

            status["busy_until"] = int(time.time()) + hours * 3600
            save_data()

        await edit_status(chat_id, status_id, force=True)
        return

    if text.startswith("+"):
        async with data_lock:
            group = get_group(chat_id)
            status = group["statuses"][status_id]
            status["text"] = text[1:]
            save_data()

        await edit_status(chat_id, status_id, force=True)
        return

    await safe_answer(
        message,
        "Не понял команду. Напиши /help"
    )


async def timer_loop():
    while True:
        to_edit = []
        warnings = []

        async with data_lock:
            now = int(time.time())

            for chat_id_str, group in data["groups"].items():
                chat_id = int(chat_id_str)

                for status_id, status in group["statuses"].items():
                    changed = False

                    busy_until = status.get("busy_until")
                    reservation_until = status.get("reservation_until")

                    if busy_until and busy_until <= now:
                        status["busy_until"] = None
                        changed = True

                    if reservation_until and reservation_until <= now:
                        status["reservation_until"] = None
                        changed = True
                        warnings.append((chat_id, status_id))

                    old_caption = status.get("last_caption", "")
                    new_caption = make_caption(chat_id, status_id)

                    if changed or new_caption != old_caption:
                        to_edit.append((chat_id, status_id))

            save_data()

        for chat_id, status_id in to_edit:
            await edit_status(chat_id, status_id, force=True)
            await asyncio.sleep(0.25)

        for chat_id, status_id in warnings:
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"⚠️ Пора выдать клиенту аккаунт.\nЛот: Статус {status_id}"
                )
                await asyncio.sleep(0.5)
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except Exception as e:
                print(f"Не смог отправить предупреждение: {e}")

        await asyncio.sleep(60)


async def main():
    if not BOT_TOKEN:
        raise RuntimeError("Нет BOT_TOKEN")

    asyncio.create_task(timer_loop())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
