import asyncio
import io
import json
import logging
import os
import re
from pathlib import Path

from PIL import Image
from telegram import Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


BOT_TOKEN = os.environ["BOT_TOKEN"]
SOURCE_CHAT_ID = int(os.environ["SOURCE_CHAT_ID"])
CONTROL_CHAT_ID = int(os.environ["CONTROL_CHAT_ID"])
TARGET_CHAT_ID = int(os.environ["TARGET_CHAT_ID"])

ACCOUNTS_COUNT = int(
    os.getenv("ACCOUNTS_COUNT", "50")
)

STATE_FILE = Path(
    os.getenv("STATE_FILE", "bot_state.json")
)

FIRST_MESSAGE_COUNT = 30
MAX_ACCOUNTS = 150

FREE_STATUS = "🟢 СВОБОДЕН"
DEFAULT_TEXT = "🟢 СТАТУС АРЕНДЫ 🟢"


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger(__name__)
state_lock = asyncio.Lock()


state = {
    "accounts_count": ACCOUNTS_COUNT,
    "statuses": {
        str(account): FREE_STATUS
        for account in range(
            1,
            ACCOUNTS_COUNT + 1
        )
    },
    "target_chat_id": TARGET_CHAT_ID,
    "photo_message_id": None,
    "text_message_id": None,
    "custom_text": DEFAULT_TEXT,
}


def load_state():
    if not STATE_FILE.exists():
        return

    try:
        saved = json.loads(
            STATE_FILE.read_text(
                encoding="utf-8"
            )
        )

        count = int(
            saved.get(
                "accounts_count",
                ACCOUNTS_COUNT
            )
        )

        old_statuses = saved.get(
            "statuses",
            {}
        )

        state["accounts_count"] = count

        state["statuses"] = {
            str(account): old_statuses.get(
                str(account),
                FREE_STATUS
            )
            for account in range(
                1,
                count + 1
            )
        }

        state["target_chat_id"] = saved.get(
            "target_chat_id",
            TARGET_CHAT_ID
        )

        state["photo_message_id"] = saved.get(
            "photo_message_id"
        )

        state["text_message_id"] = saved.get(
            "text_message_id"
        )

        state["custom_text"] = saved.get(
            "custom_text",
            DEFAULT_TEXT
        )

        logger.info("Состояние загружено")

    except Exception:
        logger.exception(
            "Ошибка загрузки состояния"
        )


def save_state():
    temporary = STATE_FILE.with_suffix(
        ".tmp"
    )

    temporary.write_text(
        json.dumps(
            state,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    temporary.replace(STATE_FILE)


def parse_account(text):
    match = re.search(
        r"(?:Аккаунт|Account)"
        r"\s*[#№:]?\s*(\d+)",
        text,
        re.IGNORECASE
    )

    if not match:
        return None

    account = int(
        match.group(1)
    )

    if (
        1
        <= account
        <= state["accounts_count"]
    ):
        return account

    return None


def parse_status(text):
    remaining_match = re.search(
        r"Осталось:\s*([^\n]+)",
        text,
        re.IGNORECASE
    )

    remaining = ""

    if remaining_match:
        remaining = (
            remaining_match
            .group(1)
            .strip()
        )

    if "бан метро" in text.lower():
        result = (
            "🔴 НЕ ДОСТУПЕН — БАН МЕТРО"
        )

        if remaining:
            result += f" {remaining}"

        return result

    if re.search(
        r"(?:⚫|🔴)?\s*Бан\b",
        text,
        re.IGNORECASE
    ):
        result = "🔴 НЕ ДОСТУПЕН"

        if remaining:
            result += f" {remaining}"

        return result

    if re.search(
        r"🔴\s*Занят",
        text,
        re.IGNORECASE
    ):
        result = "🔴 ЗАНЯТ"

        if remaining:
            result += f" {remaining}"

        return result

    if re.search(
        r"🟢\s*Свободен",
        text,
        re.IGNORECASE
    ):
        return FREE_STATUS

    return None


def build_rows(first, last):
    lines = []

    for account in range(
        first,
        last + 1
    ):
        status = state["statuses"][
            str(account)
        ]

        lines.append(
            f"ACCOUNT {account} — {status}"
        )

    return "\n".join(lines)


def build_messages():
    count = state["accounts_count"]

    first_last = min(
        FIRST_MESSAGE_COUNT,
        count
    )

    first_rows = build_rows(
        1,
        first_last
    )

    first_message = (
        f"{state['custom_text']}"
        f"\n\n{first_rows}"
    )

    if len(first_message) > 1024:
        raise ValueError(
            "Первое сообщение слишком длинное. "
            "Сократи текст и ссылки."
        )

    second_message = None

    if count > FIRST_MESSAGE_COUNT:
        second_message = build_rows(
            FIRST_MESSAGE_COUNT + 1,
            count
        )

        if len(second_message) > 4096:
            raise ValueError(
                "Второе сообщение слишком длинное."
            )

    return (
        first_message,
        second_message
    )


def create_black_photo():
    file = io.BytesIO()
    file.name = "black.png"

    image = Image.new(
        "RGB",
        (1280, 720),
        "black"
    )

    image.save(
        file,
        format="PNG"
    )

    file.seek(0)

    return file


def is_control_chat(update):
    message = update.effective_message

    if message is None:
        return False

    return (
        message.chat_id
        == CONTROL_CHAT_ID
    )


def get_caption_text(update):
    message = update.effective_message

    if message is None:
        return ""

    if message.reply_to_message:
        replied = message.reply_to_message

        return (
            replied.text
            or replied.caption
            or ""
        ).strip()

    text = message.text or ""
    parts = text.split(maxsplit=1)

    if len(parts) < 2:
        return ""

    return parts[1].strip()


async def create_board(bot):
    first_text, second_text = (
        build_messages()
    )

    photo_message = await bot.send_photo(
        chat_id=state["target_chat_id"],
        photo=create_black_photo(),
        caption=first_text
    )

    state["photo_message_id"] = (
        photo_message.message_id
    )

    state["target_chat_id"] = (
        photo_message.chat_id
    )

    if second_text:
        text_message = await bot.send_message(
            chat_id=state["target_chat_id"],
            text=second_text
        )

        state["text_message_id"] = (
            text_message.message_id
        )

    else:
        state["text_message_id"] = None

    save_state()


async def update_board(bot):
    async with state_lock:
        first_text, second_text = (
            build_messages()
        )

        photo_id = state[
            "photo_message_id"
        ]

        text_id = state[
            "text_message_id"
        ]

        chat_id = state[
            "target_chat_id"
        ]

        if photo_id is None:
            await create_board(bot)
            return

        try:
            await bot.edit_message_caption(
                chat_id=chat_id,
                message_id=photo_id,
                caption=first_text
            )

        except BadRequest as error:
            if (
                "message is not modified"
                not in str(error).lower()
            ):
                logger.error(
                    "Ошибка первого сообщения: %s",
                    error
                )

        if second_text:
            if text_id is None:
                message = await bot.send_message(
                    chat_id=chat_id,
                    text=second_text
                )

                state["text_message_id"] = (
                    message.message_id
                )

                save_state()

            else:
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=text_id,
                        text=second_text
                    )

                except BadRequest as error:
                    if (
                        "message is not modified"
                        not in str(error).lower()
                    ):
                        logger.error(
                            "Ошибка второго сообщения: %s",
                            error
                        )


async def chat_id_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    message = update.effective_message

    if message:
        await message.reply_text(
            f"ID этого чата: {message.chat_id}"
        )


async def status_new(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not is_control_chat(update):
        return

    try:
        async with state_lock:
            await create_board(
                context.bot
            )

        await (
            update.effective_message
            .reply_text(
                "✅ Два сообщения опубликованы."
            )
        )

    except Exception as error:
        await (
            update.effective_message
            .reply_text(
                f"Ошибка:\n{error}"
            )
        )


async def set_count(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not is_control_chat(update):
        return

    message = update.effective_message

    if (
        len(context.args) != 1
        or not context.args[0].isdigit()
    ):
        await message.reply_text(
            "Использование: /set_count 50"
        )

        return

    new_count = int(
        context.args[0]
    )

    if not (
        1 <= new_count <= MAX_ACCOUNTS
    ):
        await message.reply_text(
            "Количество должно быть "
            f"от 1 до {MAX_ACCOUNTS}."
        )

        return

    async with state_lock:
        old_count = state[
            "accounts_count"
        ]

        if new_count > old_count:
            for account in range(
                old_count + 1,
                new_count + 1
            ):
                state["statuses"][
                    str(account)
                ] = FREE_STATUS

        else:
            state["statuses"] = {
                key: value
                for key, value
                in state["statuses"].items()
                if int(key) <= new_count
            }

        state["accounts_count"] = (
            new_count
        )

        save_state()

    try:
        await update_board(
            context.bot
        )

        await message.reply_text(
            f"✅ Аккаунтов: {new_count}"
        )

    except Exception as error:
        await message.reply_text(
            f"Ошибка:\n{error}"
        )


async def set_caption(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not is_control_chat(update):
        return

    message = update.effective_message
    new_text = get_caption_text(update)

    if not new_text:
        await message.reply_text(
            "Пример:\n\n"
            "/set_caption 🟢 СТАТУС АРЕНДЫ 🟢\n"
            "https://t.me/example"
        )

        return

    old_text = state["custom_text"]
    state["custom_text"] = new_text

    try:
        build_messages()

    except ValueError as error:
        state["custom_text"] = old_text

        await message.reply_text(
            str(error)
        )

        return

    save_state()

    try:
        await update_board(
            context.bot
        )

        await message.reply_text(
            "✅ Текст и ссылки обновлены."
        )

    except Exception as error:
        await message.reply_text(
            f"Ошибка:\n{error}"
        )


async def status_refresh(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not is_control_chat(update):
        return

    try:
        await update_board(
            context.bot
        )

        await (
            update.effective_message
            .reply_text(
                "✅ Оба сообщения обновлены."
            )
        )

    except Exception as error:
        await (
            update.effective_message
            .reply_text(
                f"Ошибка:\n{error}"
            )
        )


async def status_info(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not is_control_chat(update):
        return

    await (
        update.effective_message
        .reply_text(
            "Аккаунтов: "
            f"{state['accounts_count']}\n"
            "SOURCE: "
            f"{SOURCE_CHAT_ID}\n"
            "CONTROL: "
            f"{CONTROL_CHAT_ID}\n"
            "TARGET: "
            f"{state['target_chat_id']}\n"
            "Сообщение 1: "
            f"{state['photo_message_id'] or 'нет'}\n"
            "Сообщение 2: "
            f"{state['text_message_id'] or 'нет'}"
        )
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

    if not message.text:
        return

    account = parse_account(
        message.text
    )

    status = parse_status(
        message.text
    )

    if (
        account is None
        or status is None
    ):
        return

    async with state_lock:
        current = state["statuses"].get(
            str(account)
        )

        if current == status:
            return

        state["statuses"][
            str(account)
        ] = status

        save_state()

    try:
        await update_board(
            context.bot
        )

    except Exception:
        logger.exception(
            "Ошибка обновления таблицы"
        )


async def post_init(
    application: Application
):
    bot = await application.bot.get_me()

    logger.info(
        "Бот запущен: @%s",
        bot.username
    )

    if state["photo_message_id"]:
        try:
            await update_board(
                application.bot
            )

        except Exception:
            logger.exception(
                "Ошибка первого обновления"
            )


def main():
    load_state()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "chat_id",
            chat_id_command
        )
    )

    application.add_handler(
        CommandHandler(
            "status_new",
            status_new
        )
    )

    application.add_handler(
        CommandHandler(
            "set_count",
            set_count
        )
    )

    application.add_handler(
        CommandHandler(
            "set_caption",
            set_caption
        )
    )

    application.add_handler(
        CommandHandler(
            "status_refresh",
            status_refresh
        )
    )

    application.add_handler(
        CommandHandler(
            "status_info",
            status_info
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            source_handler
        )
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
