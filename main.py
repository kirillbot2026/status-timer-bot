import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError


# ============================================================
# НАСТРОЙКИ
# ============================================================

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]

BOT_TOKEN = os.environ["BOT_TOKEN"]

SOURCE_CHAT_ID = int(os.environ["SOURCE_CHAT_ID"])
TARGET_CHAT_ID = int(os.environ["TARGET_CHAT_ID"])

# Сейчас учитываем только аккаунты 1-50
MIN_ACCOUNT = 1
MAX_ACCOUNT = 50

# Как часто обновлять итоговое сообщение
UPDATE_INTERVAL = 60

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

DATA_FILE = DATA_DIR / "statuses.json"


# ============================================================
# ЛОГИ
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("status_aggregator")


# ============================================================
# СОСТОЯНИЕ
# ============================================================

statuses = {}

# ID сообщения с общей сводкой во второй группе
summary_message_id = None

# Чтобы не редактировать сообщение без необходимости
last_rendered_text = None


# ============================================================
# TELEGRAM CLIENT
# ============================================================

client = TelegramClient(
    "status_aggregator_bot",
    API_ID,
    API_HASH,
)


# ============================================================
# СОХРАНЕНИЕ
# ============================================================

def save_data():
    data = {
        "statuses": statuses,
        "summary_message_id": summary_message_id,
    }

    tmp_file = DATA_FILE.with_suffix(".tmp")

    with tmp_file.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    tmp_file.replace(DATA_FILE)


def load_data():
    global statuses
    global summary_message_id

    if not DATA_FILE.exists():
        logger.info("Файл состояния ещё не существует.")
        return

    try:
        with DATA_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)

        statuses = data.get("statuses", {})
        summary_message_id = data.get("summary_message_id")

        logger.info(
            "Состояние загружено: %s аккаунтов",
            len(statuses),
        )

    except Exception:
        logger.exception("Ошибка загрузки statuses.json")


# ============================================================
# ПОИСК НОМЕРА АККАУНТА
# ============================================================

def extract_account_number(text: str):
    """
    Ищет:
        Аккаунт 10
        Аккаунт №10
        Аккаунт #10
        Account 10
        ACCOUNT 10
    """

    if not text:
        return None

    patterns = [
        r"\bаккаунт\s*[№#:]?\s*(\d+)\b",
        r"\baccount\s*[№#:]?\s*(\d+)\b",
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE,
        )

        if match:
            number = int(match.group(1))

            if MIN_ACCOUNT <= number <= MAX_ACCOUNT:
                return number

    return None


# ============================================================
# ПАРСИНГ ТАЙМЕРА
# ============================================================

def parse_duration(text: str):
    """
    Поддерживает:

    22ч
    22 ч
    2ч 52м
    1д 11ч
    1д 11ч 20м
    49м
    1д
    2 часа
    30 минут

    Возвращает timedelta или None.
    """

    if not text:
        return None

    total_seconds = 0

    # Дни
    day_match = re.search(
        r"(\d+)\s*(?:д|дн|день|дня|дней)",
        text,
        re.IGNORECASE,
    )

    if day_match:
        total_seconds += int(day_match.group(1)) * 86400

    # Часы
    hour_match = re.search(
        r"(\d+)\s*(?:ч|час|часа|часов)",
        text,
        re.IGNORECASE,
    )

    if hour_match:
        total_seconds += int(hour_match.group(1)) * 3600

    # Минуты
    minute_match = re.search(
        r"(\d+)\s*(?:м|мин|минута|минуты|минут)",
        text,
        re.IGNORECASE,
    )

    if minute_match:
        total_seconds += int(minute_match.group(1)) * 60

    if total_seconds <= 0:
        return None

    return timedelta(seconds=total_seconds)


# ============================================================
# ОПРЕДЕЛЕНИЕ СТАТУСА
# ============================================================

def detect_status(text: str):
    """
    Возвращает:
        free
        occupied
        metro_ban
        unavailable
        inactive
        None
    """

    if not text:
        return None

    lower = text.lower()

    # Сначала проверяем бан метро,
    # потому что там одновременно может быть слово "свободен".
    if (
        "бан метро" in lower
        or "🟡 бан метро" in lower
    ):
        return "metro_ban"

    # Обычный бан
    if re.search(
        r"(?:^|\n)\s*(?:⚫\s*)?бан(?:\s|$)",
        lower,
        re.IGNORECASE,
    ):
        return "unavailable"

    # Занят
    if re.search(
        r"(?:^|\n)\s*(?:🔴\s*)?занят(?:\s|$)",
        lower,
        re.IGNORECASE,
    ):
        return "occupied"

    # Свободен
    if re.search(
        r"(?:^|\n)\s*(?:🟢\s*)?свободен(?:\s|$)",
        lower,
        re.IGNORECASE,
    ):
        return "free"

    # Неактивен
    if "неактив" in lower:
        return "inactive"

    return None


# ============================================================
# ПОЛУЧЕНИЕ ССЫЛКИ НА СООБЩЕНИЕ
# ============================================================

def make_source_message_link(message_id: int):
    """
    Для закрытой супергруппы:

    -1001234567890
          ↓
    1234567890

    Telegram message link:
    https://t.me/c/1234567890/MESSAGE_ID
    """

    chat_id_string = str(abs(SOURCE_CHAT_ID))

    if chat_id_string.startswith("100"):
        internal_id = chat_id_string[3:]
    else:
        internal_id = chat_id_string

    return f"https://t.me/c/{internal_id}/{message_id}"


# ============================================================
# ОБНОВЛЕНИЕ АККАУНТА
# ============================================================

def process_message(message):
    global statuses

    text = message.raw_text or ""

    account = extract_account_number(text)

    if account is None:
        return False

    status = detect_status(text)

    if status is None:
        return False

    # Неактивный сейчас не выводим.
    # Но аккаунт всё равно может существовать.
    if status == "inactive":
        if str(account) in statuses:
            statuses[str(account)]["status"] = "inactive"
            statuses[str(account)]["expires_at"] = None
            statuses[str(account)]["source_message_id"] = message.id
            statuses[str(account)]["source_link"] = make_source_message_link(
                message.id
            )
            save_data()

        logger.info(
            "ACCOUNT %s: НЕАКТИВЕН (не выводится)",
            account,
        )

        return True

    duration = parse_duration(text)

    expires_at = None

    if duration:
        expires_at = (
            datetime.now(timezone.utc) + duration
        ).isoformat()

    source_link = make_source_message_link(message.id)

    statuses[str(account)] = {
        "account": account,
        "status": status,
        "expires_at": expires_at,
        "source_message_id": message.id,
        "source_link": source_link,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    save_data()

    logger.info(
        "ACCOUNT %s -> %s | timer=%s | message=%s",
        account,
        status,
        duration,
        message.id,
    )

    return True


# ============================================================
# ПРОВЕРКА ИСТЁКШИХ ТАЙМЕРОВ
# ============================================================

def normalize_expired_statuses():
    changed = False

    now = datetime.now(timezone.utc)

    for account, item in statuses.items():

        expires_at = item.get("expires_at")

        if not expires_at:
            continue

        try:
            expires = datetime.fromisoformat(expires_at)

        except Exception:
            item["expires_at"] = None
            changed = True
            continue

        if now >= expires:

            # После окончания любого таймера
            # аккаунт становится свободным.
            if item.get("status") != "free":
                item["status"] = "free"
                item["expires_at"] = None
                changed = True

                logger.info(
                    "ACCOUNT %s -> таймер закончился -> СВОБОДЕН",
                    account,
                )

    if changed:
        save_data()

    return changed


# ============================================================
# ФОРМАТИРОВАНИЕ ОСТАВШЕГОСЯ ВРЕМЕНИ
# ============================================================

def format_remaining(expires_at):
    if not expires_at:
        return ""

    try:
        expires = datetime.fromisoformat(expires_at)

    except Exception:
        return ""

    now = datetime.now(timezone.utc)

    remaining = expires - now

    if remaining.total_seconds() <= 0:
        return ""

    total_seconds = int(remaining.total_seconds())

    days = total_seconds // 86400
    total_seconds %= 86400

    hours = total_seconds // 3600
    total_seconds %= 3600

    minutes = total_seconds // 60

    parts = []

    if days:
        parts.append(f"{days}д")

    if hours:
        parts.append(f"{hours}ч")

    # Показываем минуты, если меньше суток
    # или если они есть.
    if minutes or not parts:
        parts.append(f"{minutes}м")

    return " ".join(parts)


# ============================================================
# ТЕКСТ ОДНОЙ СТРОКИ
# ============================================================

def get_status_line(item):
    account = item["account"]
    status = item.get("status")

    if status == "free":
        return (
            f'<a href="{item["source_link"]}">'
            f"ACCOUNT {account}"
            f"</a> — 🟢 СВОБОДЕН"
        )

    if status == "occupied":
        remaining = format_remaining(
            item.get("expires_at")
        )

        if remaining:
            return (
                f'<a href="{item["source_link"]}">'
                f"ACCOUNT {account}"
                f"</a> — 🔴 ЗАНЯТ — осталось {remaining}"
            )

        return (
            f'<a href="{item["source_link"]}">'
            f"ACCOUNT {account}"
            f"</a> — 🔴 ЗАНЯТ"
        )

    if status == "metro_ban":
        remaining = format_remaining(
            item.get("expires_at")
        )

        if remaining:
            return (
                f'<a href="{item["source_link"]}">'
                f"ACCOUNT {account}"
                f"</a> — 🔴 БАН МЕТРО — осталось {remaining}"
            )

        return (
            f'<a href="{item["source_link"]}">'
            f"ACCOUNT {account}"
            f"</a> — 🔴 БАН МЕТРО"
        )

    if status == "unavailable":
        remaining = format_remaining(
            item.get("expires_at")
        )

        if remaining:
            return (
                f'<a href="{item["source_link"]}">'
                f"ACCOUNT {account}"
                f"</a> — 🔴 НЕ ДОСТУПЕН — осталось {remaining}"
            )

        return (
            f'<a href="{item["source_link"]}">'
            f"ACCOUNT {account}"
            f"</a> — 🔴 НЕ ДОСТУПЕН"
        )

    return None


# ============================================================
# СОЗДАНИЕ ИТОГОВОГО ТЕКСТА
# ============================================================

def render_summary():
    lines = [
        "🟢 <b>СТАТУС АРЕНДЫ</b> 🟢",
        "",
    ]

    # Строго ACCOUNT 1 -> ACCOUNT 50
    for account in range(
        MIN_ACCOUNT,
        MAX_ACCOUNT + 1,
    ):

        item = statuses.get(str(account))

        if not item:
            # Если аккаунт пока не найден,
            # не выводим пустую строку.
            continue

        if item.get("status") == "inactive":
            continue

        line = get_status_line(item)

        if line:
            lines.append(line)

    return "\n".join(lines)


# ============================================================
# ПОИСК/СОЗДАНИЕ ИТОГОВОГО СООБЩЕНИЯ
# ============================================================

async def get_or_create_summary_message():
    global summary_message_id

    # Если ID уже сохранён — пытаемся найти сообщение
    if summary_message_id:

        try:
            message = await client.get_messages(
                TARGET_CHAT_ID,
                ids=summary_message_id,
            )

            if message:
                return message

        except Exception:
            logger.exception(
                "Не удалось получить сохранённое итоговое сообщение."
            )

    # Ищем сообщение среди последних сообщений группы
    try:

        async for message in client.iter_messages(
            TARGET_CHAT_ID,
            limit=100,
        ):

            if not message.raw_text:
                continue

            if "🟢 СТАТУС АРЕНДЫ 🟢" in message.raw_text:
                summary_message_id = message.id
                save_data()

                logger.info(
                    "Найдено существующее итоговое сообщение: %s",
                    message.id,
                )

                return message

    except Exception:
        logger.exception(
            "Ошибка поиска итогового сообщения."
        )

    # Создаём новое
    text = render_summary()

    message = await client.send_message(
        TARGET_CHAT_ID,
        text,
        parse_mode="html",
        link_preview=False,
    )

    summary_message_id = message.id

    save_data()

    logger.info(
        "Создано новое итоговое сообщение: %s",
        message.id,
    )

    return message


# ============================================================
# ОБНОВЛЕНИЕ ИТОГОВОГО СООБЩЕНИЯ
# ============================================================

async def update_summary(force=False):
    global last_rendered_text

    normalize_expired_statuses()

    text = render_summary()

    if not force and text == last_rendered_text:
        return

    try:

        message = await get_or_create_summary_message()

        if message.raw_text == text:
            last_rendered_text = text
            return

        await client.edit_message(
            TARGET_CHAT_ID,
            message.id,
            text,
            parse_mode="html",
            link_preview=False,
        )

        last_rendered_text = text

        logger.info(
            "Итоговое сообщение обновлено."
        )

    except FloodWaitError as e:

        logger.warning(
            "Telegram FloodWait: ждём %s секунд",
            e.seconds,
        )

        await asyncio.sleep(e.seconds)

    except Exception:
        logger.exception(
            "Ошибка обновления итогового сообщения."
        )


# ============================================================
# ПЕРВОНАЧАЛЬНАЯ СИНХРОНИЗАЦИЯ
# ============================================================

async def initial_sync():
    logger.info(
        "Начинаем первоначальную синхронизацию первой группы..."
    )

    found = set()

    try:

        async for message in client.iter_messages(
            SOURCE_CHAT_ID,
            limit=None,
        ):

            if not message.raw_text:
                continue

            account = extract_account_number(
                message.raw_text
            )

            if account is None:
                continue

            # Нас интересуют только 1-50
            if not (
                MIN_ACCOUNT
                <= account
                <= MAX_ACCOUNT
            ):
                continue

            # В истории сообщения одного аккаунта могут
            # встречаться много раз.
            #
            # iter_messages идёт от новых к старым,
            # поэтому первое найденное сообщение —
            # самое свежее.
            if account in found:
                continue

            if process_message(message):
                found.add(account)

                logger.info(
                    "Синхронизирован ACCOUNT %s",
                    account,
                )

            if len(found) >= MAX_ACCOUNT:
                # Все 50 нашли.
                break

    except Exception:
        logger.exception(
            "Ошибка первоначальной синхронизации."
        )

    logger.info(
        "Первоначальная синхронизация завершена. Найдено: %s/50",
        len(found),
    )


# ============================================================
# НОВЫЕ СООБЩЕНИЯ
# ============================================================

@client.on(
    events.NewMessage(
        chats=SOURCE_CHAT_ID
    )
)
async def new_source_message(event):

    try:

        message = event.message

        if process_message(message):
            await update_summary(
                force=True
            )

    except Exception:
        logger.exception(
            "Ошибка обработки нового сообщения."
        )


# ============================================================
# ИЗМЕНЕНИЯ СООБЩЕНИЙ
# ============================================================

@client.on(
    events.MessageEdited(
        chats=SOURCE_CHAT_ID
    )
)
async def edited_source_message(event):

    try:

        message = event.message

        if process_message(message):
            await update_summary(
                force=True
            )

    except Exception:
        logger.exception(
            "Ошибка обработки изменённого сообщения."
        )


# ============================================================
# МИНУТНЫЙ ТАЙМЕР
# ============================================================

async def timer_loop():

    global last_rendered_text

    while True:

        try:

            # Пересчитываем окончания
            normalize_expired_statuses()

            # Создаём новый текст с актуальными минутами
            text = render_summary()

            if text != last_rendered_text:
                await update_summary()

        except Exception:
            logger.exception(
                "Ошибка в timer_loop."
            )

        await asyncio.sleep(
            UPDATE_INTERVAL
        )


# ============================================================
# ЗАПУСК
# ============================================================

async def main():

    logger.info(
        "=================================================="
    )

    logger.info(
        "STATUS AGGREGATOR STARTING"
    )

    logger.info(
        "SOURCE_CHAT_ID = %s",
        SOURCE_CHAT_ID,
    )

    logger.info(
        "TARGET_CHAT_ID = %s",
        TARGET_CHAT_ID,
    )

    logger.info(
        "ACCOUNTS = %s-%s",
        MIN_ACCOUNT,
        MAX_ACCOUNT,
    )

    logger.info(
        "=================================================="
    )

    load_data()

    # Подключение через BOT TOKEN
    await client.start(
        bot_token=BOT_TOKEN
    )

    me = await client.get_me()

    logger.info(
        "Бот авторизован: @%s | id=%s",
        me.username,
        me.id,
    )

    # Первоначально читаем существующую историю
    await initial_sync()

    # Создаём/находим одно сообщение во второй группе
    await get_or_create_summary_message()

    # Первое обновление
    await update_summary(
        force=True
    )

    # Запускаем минутный цикл
    asyncio.create_task(
        timer_loop()
    )

    logger.info(
        "Бот запущен и ожидает изменения статусов..."
    )

    # Ждём события Telegram
    await client.run_until_disconnected()


# =========
