import os
import re
import asyncio
import logging
from datetime import datetime

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

# Сколько аккаунтов показываем во второй группе
MAX_ACCOUNTS = 50

# Как часто обновлять итоговое сообщение
UPDATE_INTERVAL = 60


# ============================================================
# ЛОГИ
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger("status-bot")


# ============================================================
# TELEGRAM CLIENT
# ============================================================

client = TelegramClient(
    "status_timer_bot",
    API_ID,
    API_HASH
)


# ============================================================
# ДАННЫЕ
# ============================================================

# account_number -> информация
statuses = {}

# ID сообщения во второй группе
target_message_id = None


# ============================================================
# РАЗБОР СТАТУСА
# ============================================================

def parse_status(text: str):
    """
    Пример входного сообщения:

    Аккаунт 10

    🟢 Свободен
    email
    password

    или:

    Аккаунт 12

    🟢 Свободен
    🟡 Бан метро
    Осталось: 22ч

    или:

    Аккаунт 37

    🔴 Занят
    Осталось: 2ч 52м

    или:

    Аккаунт 27

    🟢 Свободен
    ⚫ Бан
    Осталось: 1д 11ч
    """

    if not text:
        return None

    # Ищем номер аккаунта
    match = re.search(
        r"(?:аккаунт|account)\s*#?\s*(\d+)",
        text,
        re.IGNORECASE
    )

    if not match:
        return None

    account = int(match.group(1))

    # Сейчас показываем 1-50
    if account < 1 or account > MAX_ACCOUNTS:
        return None

    # --------------------------------------------------------
    # СВОБОДЕН
    # --------------------------------------------------------

    if re.search(r"🟢\s*(?:свободен|свободно)", text, re.IGNORECASE):

        # Бан метро
        metro = re.search(
            r"🟡\s*(?:бан\s+метро|метро).*?(?:осталось:\s*)?([^\n]+)",
            text,
            re.IGNORECASE
        )

        if metro:
            remaining = metro.group(1).strip()

            return {
                "account": account,
                "type": "metro",
                "remaining": remaining,
                "priority": 2
            }

        # Обычный бан / недоступен
        unavailable = re.search(
            r"(?:⚫|🔴)\s*(?:бан|не\s*доступен).*?(?:осталось:\s*)?([^\n]+)",
            text,
            re.IGNORECASE
        )

        if unavailable:
            remaining = unavailable.group(1).strip()

            return {
                "account": account,
                "type": "unavailable",
                "remaining": remaining,
                "priority": 3
            }

        return {
            "account": account,
            "type": "free",
            "remaining": "",
            "priority": 1
        }

    # --------------------------------------------------------
    # ЗАНЯТ
    # --------------------------------------------------------

    if re.search(r"🔴\s*занят", text, re.IGNORECASE):

        remaining_match = re.search(
            r"осталось:\s*([^\n]+)",
            text,
            re.IGNORECASE
        )

        remaining = ""

        if remaining_match:
            remaining = remaining_match.group(1).strip()

        return {
            "account": account,
            "type": "busy",
            "remaining": remaining,
            "priority": 4
        }

    # --------------------------------------------------------
    # НЕАКТИВЕН / ДРУГИЕ СТАТУСЫ
    # --------------------------------------------------------
    # Нам специально не нужен "Неактивен".
    # Если исходный бот выставил неизвестный статус,
    # просто игнорируем его.

    return None


# ============================================================
# ФОРМАТИРОВАНИЕ
# ============================================================

def format_status(item):
    account = item["account"]
    status_type = item["type"]
    remaining = item["remaining"]

    # Пока ссылка на аккаунт не задана.
    # Позже сделаем отдельную систему ссылок.
    account_text = f"ACCOUNT {account}"

    if status_type == "free":
        return f"{account_text} — 🟢 СВОБОДЕН"

    if status_type == "metro":
        return (
            f"{account_text} — 🔴 НЕ ДОСТУПЕН — "
            f"БАН МЕТРО {remaining}"
        )

    if status_type == "unavailable":
        if remaining:
            return (
                f"{account_text} — 🔴 НЕ ДОСТУПЕН "
                f"{remaining}"
            )

        return f"{account_text} — 🔴 НЕ ДОСТУПЕН"

    if status_type == "busy":
        if remaining:
            return (
                f"{account_text} — 🔴 ЗАНЯТ "
                f"{remaining}"
            )

        return f"{account_text} — 🔴 ЗАНЯТ"

    return None


# ============================================================
# СОБИРАЕМ ИТОГОВОЕ СООБЩЕНИЕ
# ============================================================

def build_message():

    lines = [
        "🟢 СТАТУС АРЕНДЫ 🟢",
        ""
    ]

    for account in sorted(statuses.keys()):

        item = statuses[account]

        line = format_status(item)

        if line:
            lines.append(line)

    return "\n".join(lines)


# ============================================================
# ПОИСК СТАРЫХ СТАТУСОВ
# ============================================================

async def load_old_messages():

    log.info("========================================")
    log.info("НАЧАЛО ЗАГРУЗКИ СТАРЫХ СООБЩЕНИЙ")
    log.info("SOURCE_CHAT_ID = %s", SOURCE_CHAT_ID)
    log.info("========================================")

    count = 0
    found = 0

    try:

        async for message in client.iter_messages(
            SOURCE_CHAT_ID,
            limit=3000
        ):

            count += 1

            if not message.text:
                continue

            parsed = parse_status(message.text)

            if parsed:

                account = parsed["account"]

                # Последнее найденное сообщение для аккаунта
                # считаем актуальным.
                if account not in statuses:
                    statuses[account] = parsed
                    found += 1

                    log.info(
                        "НАЙДЕН АККАУНТ %s | %s",
                        account,
                        format_status(parsed)
                    )

        log.info(
            "ПРОСМОТРЕНО СООБЩЕНИЙ: %s",
            count
        )

        log.info(
            "НАЙДЕНО АККАУНТОВ: %s",
            found
        )

    except Exception:

        log.exception(
            "ОШИБКА ПРИ ЧТЕНИИ ИСТОРИИ SOURCE_CHAT"
        )

        raise


# ============================================================
# СОЗДАНИЕ / ОБНОВЛЕНИЕ ИТОГОВОГО СООБЩЕНИЯ
# ============================================================

async def update_target_message():

    global target_message_id

    text = build_message()

    log.info(
        "Обновление итогового сообщения. Аккаунтов: %s",
        len(statuses)
    )

    try:

        # Если сообщение ещё неизвестно —
        # ищем последнее сообщение нашего бота
        if target_message_id is None:

            me = await client.get_me()

            log.info(
                "Бот: @%s",
                me.username
            )

            async for message in client.iter_messages(
                TARGET_CHAT_ID,
                limit=30
            ):

                if (
                    message.sender_id == me.id
                    and message.text
                    and "🟢 СТАТУС АРЕНДЫ 🟢" in message.text
                ):

                    target_message_id = message.id

                    log.info(
                        "НАЙДЕНО СТАРОЕ ИТОГОВОЕ СООБЩЕНИЕ: %s",
                        target_message_id
                    )

                    break

        # Если сообщения ещё нет — создаём
        if target_message_id is None:

            message = await client.send_message(
                TARGET_CHAT_ID,
                text
            )

            target_message_id = message.id

            log.info(
                "СОЗДАНО ИТОГОВОЕ СООБЩЕНИЕ: %s",
                target_message_id
            )

        else:

            await client.edit_message(
                TARGET_CHAT_ID,
                target_message_id,
                text
            )

            log.info(
                "ИТОГОВОЕ СООБЩЕНИЕ ОБНОВЛЕНО: %s",
                target_message_id
            )

    except FloodWaitError as e:

        log.warning(
            "FloodWait: ждём %s секунд",
            e.seconds
        )

        await asyncio.sleep(e.seconds)

    except Exception:

        log.exception(
            "ОШИБКА ОБНОВЛЕНИЯ TARGET"
        )


# ============================================================
# НОВОЕ / ИЗМЕНЁННОЕ СООБЩЕНИЕ
# ============================================================

async def process_message(message):

    if not message:
        return

    if not message.text:
        return

    parsed = parse_status(message.text)

    if not parsed:
        return

    account = parsed["account"]

    statuses[account] = parsed

    log.info(
        "ИЗМЕНЕНИЕ | ACCOUNT %s | %s",
        account,
        format_status(parsed)
    )

    await update_target_message()


# ============================================================
# НОВЫЕ СООБЩЕНИЯ
# ============================================================

@client.on(events.NewMessage(chats=SOURCE_CHAT_ID))
async def new_message_handler(event):

    log.info(
        "Получено НОВОЕ сообщение SOURCE | id=%s",
        event.message.id
    )

    await process_message(event.message)


# ============================================================
# ИЗМЕНЕНИЯ СООБЩЕНИЙ
# ============================================================

@client.on(events.MessageEdited(chats=SOURCE_CHAT_ID))
async def edited_message_handler(event):

    log.info(
        "Получено ИЗМЕНЕНИЕ SOURCE | id=%s",
        event.message.id
    )

    await process_message(event.message)


# ============================================================
# ПЕРИОДИЧЕСКОЕ ОБНОВЛЕНИЕ
# ============================================================

async def periodic_update():

    while True:

        try:

            await asyncio.sleep(UPDATE_INTERVAL)

            log.info(
                "МИНУТНЫЙ ЦИКЛ | аккаунтов: %s",
                len(statuses)
            )

            await update_target_message()

        except asyncio.CancelledError:

            raise

        except Exception:

            log.exception(
                "Ошибка минутного цикла"
            )


# ============================================================
# ЗАПУСК
# ============================================================

async def main():

    log.info("========================================")
    log.info("STATUS TIMER BOT")
    log.info("========================================")

    log.info("[1] API_ID загружен")
    log.info("[2] API_HASH загружен")
    log.info("[3] BOT_TOKEN загружен")
    log.info("[4] SOURCE_CHAT_ID = %s", SOURCE_CHAT_ID)
    log.info("[5] TARGET_CHAT_ID = %s", TARGET_CHAT_ID)

    log.info("[6] Подключение к Telegram...")

    await client.start(
        bot_token=BOT_TOKEN
    )

    log.info("[7] Telegram подключен")

    me = await client.get_me()

    log.info(
        "[8] Авторизован как @%s | id=%s | bot=%s",
        me.username,
        me.id,
        me.bot
    )

    # Проверяем доступ к SOURCE
    log.info("[9] Проверяем SOURCE...")

    source = await client.get_entity(SOURCE_CHAT_ID)

    log.info(
        "[10] SOURCE найден: %s",
        getattr(source, "title", "unknown")
    )

    # Проверяем TARGET
    log.info("[11] Проверяем TARGET...")

    target = await client.get_entity(TARGET_CHAT_ID)

    log.info(
        "[12] TARGET найден: %s",
        getattr(target, "title", "unknown")
    )

    # Загружаем существующие статусы
    log.info("[13] Загружаем существующие статусы...")

    await load_old_messages()

    # Создаём первое итоговое сообщение
    log.info("[14] Создаём итоговое сообщение...")

    await update_target_message()

    log.info("========================================")
    log.info(
        "[READY] БОТ ЗАПУЩЕН | АККАУНТОВ: %s",
        len(statuses)
    )
    log.info("========================================")

    # Запускаем минутное обновление
    asyncio.create_task(
        periodic_update()
    )

    # Оставляем Telegram connection открытым
    await client.run_until_disconnected()


if __name__ == "__main__":

    try:
        asyncio.run(main())

    except KeyboardInterrupt:

        log.info("Остановка бота")

    except Exception:

        log.exception(
            "КРИТИЧЕСКАЯ ОШИБКА"
                    )
