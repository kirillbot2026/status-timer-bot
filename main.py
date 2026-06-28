import asyncio, json, os, re, time
from pathlib import Path
from datetime import datetime, timedelta
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
            d = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            d = {}
    else:
        d = {}
    if "groups" not in d:
        d = {"groups": {}}
    return d


data = load_data()


def save_data():
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_group(chat_id: int):
    gid = str(chat_id)
    if gid not in data["groups"]:
        data["groups"][gid] = {"last_created": 0, "setup_running": False, "statuses": {}}
    g = data["groups"][gid]
    g.setdefault("last_created", 0)
    g.setdefault("setup_running", False)
    g.setdefault("statuses", {})
    return g


def format_left(sec: int):
    sec = max(0, sec)
    h = sec // 3600
    m = (sec % 3600) // 60
    return f"{h}ч {m:02d}м"


def parse_duration_to_seconds(text: str):
    text = text.lower().replace(",", " ").replace(".", " ")
    tokens = re.findall(r"(\d+)\s*([а-яa-z]*)", text)
    if not tokens:
        return None

    total = 0
    seen_hours = False

    for i, (num_s, unit) in enumerate(tokens):
        n = int(num_s)
        unit = unit.lower()

        if unit.startswith("м") or unit.startswith("min"):
            total += n
        elif unit.startswith("ч") or unit.startswith("h") or unit.startswith("час"):
            total += n * 60
            seen_hours = True
        elif unit == "":
            if i == 0:
                total += n * 60
                seen_hours = True
            else:
                total += n if seen_hours else n * 60

    return total * 60 if total > 0 else None


def seconds_until_moscow_10():
    tz = ZoneInfo("Europe/Moscow")
    now = datetime.now(tz)
    target = now.replace(hour=10, minute=0, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return int((target - now).total_seconds())


def sender_name(message: Message):
    u = message.from_user
    if not u:
        return "Неизвестный"
    return f"@{u.username}" if u.username else u.full_name


def make_caption(chat_id: int, status_id: str):
    g = get_group(chat_id)
    s = g["statuses"][status_id]
    now = int(time.time())

    busy_until = s.get("busy_until")
    reservation_until = s.get("reservation_until")
    text = s.get("text", "")

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
        await bot.send_message(chat_id=chat_id, text=text, reply_to_message_id=reply_to_message_id)
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after + 1)
        try:
            await bot.send_message(chat_id=chat_id, text=text, reply_to_message_id=reply_to_message_id)
        except Exception:
            pass
    except Exception:
        pass


async def edit_status(chat_id: int, status_id: str, force=False):
    g = get_group(chat_id)
    s = g["statuses"].get(status_id)
    if not s:
        return

    caption = make_caption(chat_id, status_id)
    if not force and s.get("last_caption") == caption:
        return

    try:
        await bot.edit_message_caption(chat_id=chat_id, message_id=s["message_id"], caption=caption)
        s["last_caption"] = caption
        save_data()
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            print(f"Ошибка обновления {status_id}: {e}")
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after + 1)
        await edit_status(chat_id, status_id, True)
    except Exception as e:
        print(f"Ошибка обновления {status_id}: {e}")


def find_status_by_message_id(chat_id: int, message_id: int):
    g = get_group(chat_id)
    for sid, s in g["statuses"].items():
        if s.get("message_id") == message_id:
            return sid
    return None


def sync_last_created(g):
    mx = 0
    for k in g["statuses"].keys():
        if str(k).isdigit():
            mx = max(mx, int(k))
    g["last_created"] = max(g.get("last_created", 0), mx)


async def create_missing_statuses(message: Message):
    chat_id = message.chat.id

    async with data_lock:
        g = get_group(chat_id)
        sync_last_created(g)

        if g["setup_running"]:
            await safe_answer(message, "Создание уже идёт. Подожди.")
            return

        if g["last_created"] >= STATUSES_COUNT:
            await safe_answer(message, "Уже есть все 55 статусов.")
            return

        g["setup_running"] = True
        start_from = g["last_created"] + 1
        save_data()

    try:
        for i in range(start_from, STATUSES_COUNT + 1):
            sid = str(i)
            caption = f"Статус {sid}\n\n🟢 Свободен"

            sent = await bot.send_photo(chat_id=chat_id, photo=DEFAULT_PHOTO_URL, caption=caption)

            async with data_lock:
                g = get_group(chat_id)
                g["statuses"][sid] = {
                    "message_id": sent.message_id,
                    "photo": DEFAULT_PHOTO_URL,
                    "text": "",
                    "busy_until": None,
                    "reservation_until": None,
                    "last_caption": caption
                }
                g["last_created"] = i
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
            g = get_group(chat_id)
            g["setup_running"] = False
            save_data()


@dp.message(Command("setup"))
async def setup(message: Message):
    g = get_group(message.chat.id)
    sync_last_created(g)

    if g["last_created"] > 0:
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
        g = get_group(message.chat.id)
        g["statuses"] = {}
        g["last_created"] = 0
        g["setup_running"] = False
        save_data()

    await safe_answer(message, "База очищена. Старые сообщения не удалены. Теперь напиши /setup.")


@dp.message(F.reply_to_message, ~F.text.startswith("/"))
async def handle_reply(message: Message):
    chat_id = message.chat.id
    replied_id = message.reply_to_message.message_id
    status_id = find_status_by_message_id(chat_id, replied_id)

    if not status_id:
        return

    if message.content_type == ContentType.PHOTO:
        file_id = message.photo[-1].file_id

        async with data_lock:
            g = get_group(chat_id)
            s = g["statuses"][status_id]
            s["photo"] = file_id
            save_data()

        try:
            caption = make_caption(chat_id, status_id)
            await bot.edit_message_media(
                chat_id=chat_id,
                message_id=s["message_id"],
                media=InputMediaPhoto(media=file_id, caption=caption)
            )

            async with data_lock:
                s["last_caption"] = caption
                save_data()

        except Exception as e:
            await safe_answer(message, f"Не смог заменить фото: {e}")

        return

    if message.content_type != ContentType.TEXT:
        return

    text = message.text.strip()
    text_lower = text.lower()

    gift = re.fullmatch(r"(\d+)\s*\+\s*(\d+)", text)
    if gift:
        base = int(gift.group(1))
        bonus = int(gift.group(2))
        total = base + bonus

        async with data_lock:
            g = get_group(chat_id)
            s = g["statuses"][status_id]
            s["busy_until"] = int(time.time()) + total * 3600
            save_data()

        try:
            await message.delete()
        except Exception:
            pass

        await safe_send_reply(
            chat_id,
            replied_id,
            f"на {base} часов + {bonus} часа подарок\n{sender_name(message)}"
        )
        await edit_status(chat_id, status_id, True)
        return

    if text_lower == "до утра":
        sec = seconds_until_moscow_10()

        async with data_lock:
            g = get_group(chat_id)
            s = g["statuses"][status_id]
            s["busy_until"] = int(time.time()) + sec
            save_data()

        await edit_status(chat_id, status_id, True)
        return

    if text_lower.startswith("бронь"):
        sec = parse_duration_to_seconds(text[5:].strip())
        if not sec:
            await safe_answer(message, "Не понял время брони. Пример: Бронь 1ч 30м")
            return

        async with data_lock:
            g = get_group(chat_id)
            s = g["statuses"][status_id]
            s["reservation_until"] = int(time.time()) + sec
            save_data()

        await edit_status(chat_id, status_id, True)
        return

    time_match = re.fullmatch(r"(\d+):(\d{1,2})", text)
    if time_match:
        hours = int(time_match.group(1))
        minutes = int(time_match.group(2))

        if minutes >= 60:
            await safe_answer(message, "Минут должно быть меньше 60.")
            return

        if hours > 999:
            await safe_answer(message, "Слишком большое число часов. Максимум 999.")
            return

        async with data_lock:
            g = get_group(chat_id)
            s = g["statuses"][status_id]
            s["busy_until"] = int(time.time()) + hours * 3600 + minutes * 60
            save_data()

        await edit_status(chat_id, status_id, True)
        return

    if text.isdigit():
        hours = int(text)

        async with data_lock:
            g = get_group(chat_id)
            s = g["statuses"][status_id]

            if hours == 0:
                s["busy_until"] = None
            elif hours > 999:
                await safe_answer(message, "Слишком большое число часов. Максимум 999.")
                return
            else:
                s["busy_until"] = int(time.time()) + hours * 3600

            save_data()

        await edit_status(chat_id, status_id, True)
        return

    plus_text_timer = re.fullmatch(r"(\d+)\s+\+(.+)", text)
    if plus_text_timer:
        hours = int(plus_text_timer.group(1))
        extra = plus_text_timer.group(2)

        if hours > 999:
            await safe_answer(message, "Слишком большое число часов. Максимум 999.")
            return

        async with data_lock:
            g = get_group(chat_id)
            s = g["statuses"][status_id]
            s["busy_until"] = int(time.time()) + hours * 3600
            s["text"] = extra
            save_data()

        await edit_status(chat_id, status_id, True)
        return

    any_text_timer = re.fullmatch(r"(\d+)\s+(.+)", text)
    if any_text_timer:
        hours = int(any_text_timer.group(1))

        if hours > 999:
            await safe_answer(message, "Слишком большое число часов. Максимум 999.")
            return

        async with data_lock:
            g = get_group(chat_id)
            s = g["statuses"][status_id]
            s["busy_until"] = int(time.time()) + hours * 3600
            save_data()

        await edit_status(chat_id, status_id, True)
        return

    if text.startswith("+"):
        async with data_lock:
            g = get_group(chat_id)
            s = g["statuses"][status_id]
            s["text"] = text[1:]
            save_data()

        await edit_status(chat_id, status_id, True)
        return


async def timer_loop():
    while True:
        to_edit = []
        warnings = []

        async with data_lock:
            now = int(time.time())

            for chat_id_str, g in data["groups"].items():
                chat_id = int(chat_id_str)

                for sid, s in g["statuses"].items():
                    changed = False

                    busy = s.get("busy_until")
                    bron = s.get("reservation_until")

                    if busy and busy <= now:
                        s["busy_until"] = None
                        changed = True

                    if bron and bron <= now:
                        s["reservation_until"] = None
                        changed = True
                        warnings.append((chat_id, sid))

                    new_caption = make_caption(chat_id, sid)
                    old_caption = s.get("last_caption", "")

                    if changed or new_caption != old_caption:
                        to_edit.append((chat_id, sid))

            save_data()

        for chat_id, sid in to_edit:
            await edit_status(chat_id, sid, True)
            await asyncio.sleep(0.25)

        for chat_id, sid in warnings:
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"⚠️ Пора выдать попке аккаунт.\nЛот: Статус {sid}"
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
