import os
import re
import asyncio
from datetime import datetime

from telegram import Bot, Update
from telegram.ext import (
    Application,
    MessageHandler,
    ContextTypes,
    filters,
)

BOT_TOKEN = os.environ["BOT_TOKEN"]
SOURCE_CHAT_ID = int(os.environ["SOURCE_CHAT_ID"])
TARGET_CHAT_ID = int(os.environ["TARGET_CHAT_ID"])

ACCOUNTS_COUNT = 50
UPDATE_INTERVAL = 60

statuses = {
    i: "🟢 СВОБОДЕН"
    for i in range(1, ACCOUNTS_COUNT + 1)
}

target_message_id = None


def parse_account(text: str):
    match = re.search(r"Аккаунт\s+(\d+)", text, re.IGNORECASE)
    if not match:
        return None

    number = int(match.group(1))

    if 1 <= number <= ACCOUNTS_COUNT:
        return number

    return None


def parse_status(text: str):
    text_lower = text.lower()

    # Бан метро
    if "бан метро" in text_lower:
        match = re.search(
            r"Осталось:\s*([^\n]+)",
            text,
            re.IGNORECASE
        )

        remaining = match.group(1).strip() if match else ""

        if remaining:
            return f"🔴 НЕ ДОСТУПЕН — БАН МЕТРО {remaining}"

        return "🔴 НЕ ДОСТУПЕН — БАН МЕТРО"

    # Обычный бан
    if re.search(r"[⚫🔴]\s*Бан\b", text, re.IGNORECASE):
        match = re.search(
            r"Осталось:\s*([^\n]+)",
            text,
            re.IGNORECASE
        )

        remaining = match.group(1).strip() if match else ""

        if remaining:
            return f"🔴 НЕ ДОСТУПЕН {remaining}"

        return "🔴 НЕ ДОСТУПЕН"

    # Занят
    if re.search(r"🔴\s*Занят", text, re.IGNORECASE):
        match = re.search(
            r"Осталось:\s*([^\n]+)",
            text,
            re.IGNORECASE
        )

        remaining = match.group(1).strip() if match else ""

        if remaining:
            return f"🔴 ЗАНЯТ {remaining}"

        return "🔴 ЗАНЯТ"

    # Свободен
    if re.search(r"🟢\s*Свободен", text, re.IGNORECASE):
        return "🟢 СВОБОДЕН"

    return None


def build_text():
    lines = [
        "🟢 СТАТУС АРЕНДЫ 🟢",
        ""
    ]

    for account in range(1, ACCOUNTS_COUNT + 1):
        lines.append(
            f"ACCOUNT {account} — {statuses[account]}"
        )

    return "\n".join(lines)


async def find_target_message(bot: Bot):
    global target_message_id

    try:
        # Проверяем последние сообщения в целевой группе
        # через getUpdates здесь историю получить нельзя,
        # поэтому при первом запуске просто создаём сообщение.
        return None

    except Exception as e:
        print(f"[TARGET SEARCH ERROR] {e}", flush=True)
        return None


async def update_target(bot: Bot):
    global target_message_id

    text = build_text()

    try:
        if target_message_id is None:
            message = await bot.send_message(
                chat_id=TARGET_CHAT_ID,
                text=text
            )

            target_message_id = message.message_id

            print(
                f"[TARGET] Создано сообщение ID={target_message_id}",
                flush=True
            )

        else:
            try:
                await bot.edit_message_text(
                    chat_id=TARGET_CHAT_ID,
                    message_id=target_message_id,
                    text=text
                )

            except Exception as e:
                error_text = str(e).lower()

                if "message is not modified" not in error_text:
                    print(
                        f"[TARGET EDIT ERROR] {e}",
                        flush=True
                    )

    except Exception as e:
        print(f"[TARGET ERROR] {e}", flush=True)


async def process_source_message(message):
    if not message.text:
        return

    text = message.text

    account = parse_account(text)

    if account is None:
        return

    status = parse_status(text)

    if status is None:
        return

    statuses[account] = status

    print(
        f"[SOURCE] ACCOUNT {account} -> {status}",
        flush=True
    )


async def source_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    message = update.effective_message

    if message is None:
        return

    if message.chat_id != SOURCE_CHAT_ID:
        return

    await process_source_message(message)

    await update_target(context.bot)


async def minute_update(context: ContextTypes.DEFAULT_TYPE):
    await update_target(context.bot)


async def post_init(application: Application):
    print("=================================", flush=True)
    print("STATUS TIMER BOT", flush=True)
    print("=================================", flush=True)

    print("[1] BOT_TOKEN загружен", flush=True)
    print(
        f"[2] SOURCE_CHAT_ID = {SOURCE_CHAT_ID}",
        flush=True
    )
    print(
        f"[3] TARGET_CHAT_ID = {TARGET_CHAT_ID}",
        flush=True
    )

    print(
        f"[4] Создано аккаунтов: {ACCOUNTS_COUNT}",
        flush=True
    )

    print(
        "[5] Все аккаунты установлены как СВОБОДЕН",
        flush=True
    )

    try:
        me = await application.bot.get_me()

        print(
            f"[6] Авторизован как @{me.username} | id={me.id}",
            flush=True
        )

    except Exception as e:
        print(
            f"[BOT ERROR] Не удалось подключиться: {e}",
            flush=True
        )
        raise

    print("[7] Проверяем SOURCE...", flush=True)

    try:
        chat = await application.bot.get_chat(SOURCE_CHAT_ID)

        print(
            f"[SOURCE] Найден чат: {chat.title}",
            flush=True
        )

    except Exception as e:
        print(
            f"[SOURCE ERROR] {e}",
            flush=True
        )

    print("[8] Проверяем TARGET...", flush=True)

    try:
        chat = await application.bot.get_chat(TARGET_CHAT_ID)

        print(
            f"[TARGET] Найден чат: {chat.title}",
            flush=True
        )

    except Exception as e:
        print(
            f"[TARGET ERROR] {e}",
            flush=True
        )

    print("[9] Создаём итоговое сообщение...", flush=True)

    await update_target(application.bot)

    print(
        f"[READY] БОТ ЗАПУЩЕН | АККАУНТОВ: {ACCOUNTS_COUNT}",
        flush=True
    )


def main():
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # Новые сообщения из исходной группы
    application.add_handler(
        MessageHandler(
            filters.ALL,
            source_handler
        )
    )

    # Обновление раз в минуту
    application.job_queue.run_repeating(
        minute_update,
        interval=UPDATE_INTERVAL,
        first=UPDATE_INTERVAL
    )

    print("[START] Запуск Telegram Bot...", flush=True)

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
