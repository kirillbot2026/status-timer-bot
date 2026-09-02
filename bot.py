import asyncio
import io
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from PIL import Image
from telegram import Bot, Update
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

INITIAL_TARGET = os.getenv("TARGET_CHAT_ID")

INITIAL_COUNT = max(
    1,
    int(os.getenv("ACCOUNTS_COUNT", "50"))
)

STATE_FILE = Path(
    os.getenv("STATE_FILE", "bot_state.json")
)

FIRST_PAGE_COUNT = 30
MAX_ACCOUNTS = 150

FREE_STATUS = "🟢 СВОБОДЕН"
DEFAULT_CUSTOM_TEXT = "🟢 СТАТУС АРЕНДЫ 🟢"


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)
state_lock = asyncio.Lock()


def env_target() -> int | str | None:
    if not INITIAL_TARGET:
        return None

    if INITIAL_TARGET.lstrip("-").isdigit():
        return int(INITIAL_TARGET)

    return INITIAL_TARGET


state: dict[str, Any] = {
    "accounts_count": INITIAL_COUNT,

    "statuses": {
        str(account): FREE_STATUS
        for account in range(
            1,
            INITIAL_COUNT + 1
        )
    },

    "target_chat_id": env_target(),
    "photo_message_id": None,
    "text_message_id": None,
    "custom_text": DEFAULT_CUSTOM_TEXT,
}


def load_state() -> None:
    global state

    if not STATE_FILE.exists():
        return

    try:
        saved = json.loads(
            STATE_FILE.read_text(
                encoding="utf-8"
            )
        )

        count = max(
            1,
            min(
                MAX_ACCOUNTS,
                int(
                    saved.get(
                        "accounts_count",
                        INITIAL_COUNT
                    )
                )
            )
        )

        old_statuses = saved.get(
            "statuses",
            {}
        )

        state = {
            "accounts_count": count,

            "statuses": {
                str(account): str(
                    old_statuses.get(
                        str(account),
                        FREE_STATUS
                    )
                )
                for account in range(
                    1,
                    count + 1
                )
            },

            "target_chat_id": saved.get(
                "target_chat_id",
                env_target()
            ),

            "photo_message_id": saved.get(
                "photo_message_id",
                saved.get("target_message_id")
            ),

            "text_message_id": saved.get(
                "text_message_id"
            ),

            "custom_text": str(
                saved.get(
                    "custom_text",
                    saved.get(
                        "caption",
                        DEFAULT_CUSTOM_TEXT
                    )
                )
            ),
        }

        logger.info(
            "Настройки загружены"
        )

    except (
        OSError,
        ValueError,
        TypeError,
        json.JSONDecodeError
    ):
        logger.exception(
            "Не удалось загрузить состояние"
        )


def save_state() -> None:
    STATE_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    temporary = STATE_FILE.with_suffix(
        STATE_FILE.suffix + ".tmp"
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


def parse_account(
    text: str
) -> int | None:
    match = re.search(
        r"(?:Аккаунт|Account)"
        r"\s*[#№:]?\s*(\d+)",
        text,
        re.IGNORECASE
    )

    if not match:
        return None

    number = int(
        match.group(1)
    )

    if (
        1
        <= number
        <= state["accounts_count"]
    ):
        return number

    return None


def parse_status(
    text: str
) -> str | None:
    match = re.search(
        r"Осталось:\s*([^\n]+)",
        text,
        re.IGNORECASE
    )

    remaining = (
        match.group(1).strip()
        if match
        else ""
    )

    # Бан метро
    if "бан метро" in text.lower():
        status = (
            "🔴 НЕ ДОСТУПЕН — "
            "БАН МЕТРО"
        )

        if remaining:
            status += f" {remaining}"

        return status

    # Обычный бан
    if re.search(
        r"(?:⚫|🔴)?\s*Бан\b",
        text,
        re.IGNORECASE
    ):
        status = "🔴 НЕ ДОСТУПЕН"

        if remaining:
            status += f" {remaining}"

        return status

    # Занят
    if re.search(
        r"🔴\s*Занят",
        text,
        re.IGNORECASE
    ):
        status = "🔴 ЗАНЯТ"

        if remaining:
            status += f" {remaining}"

        return status

    # Свободен
    if re.search(
        r"🟢\s*Свободен",
        text,
        re.IGNORECASE
    ):
        return FREE_STATUS

    return None


def build_rows(
    first: int,
    last: int
) -> str:
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


def build_messages() -> tuple[
    str,
    str | None
]:
    count = state["accounts_count"]

    first_last = min(
        FIRST_PAGE_COUNT,
        count
    )

    first_rows = build_rows(
        1,
        first_last
    )

    caption = (
        f"{state['custom_text']}"
        f"\n\n{first_rows}"
    )

    if count > FIRST_PAGE_COUNT:
        second_message = build_rows(
            FIRST_PAGE_COUNT + 1,
            count
        )
    else:
        second_message = None

    # Подпись к фотографии Telegram:
    # максимум 1024 символа.
    if len(caption) > 1024:
        raise ValueError(
            "Первое сообщение занимает "
            f"{len(caption)} символов "
            "при лимите 1024. "
            "Сократи текст командой "
            "/set_caption."
        )

    # Обычное сообщение Telegram:
    # максимум 4096 символов.
    if (
        second_message
        and len(second_message) > 4096
    ):
        raise ValueError(
            "Второе сообщение длиннее "
            "лимита Telegram. "
            "Уменьши количество аккаунтов."
        )

    return caption, second_message


def create_black_photo() -> io.BytesIO:
    output = io.BytesIO()
    output.name = "black.png"

    image = Image.new(
        "RGB",
        (1280, 720),
        "black"
    )

    image.save(
        output,
        format="PNG",
        optimize=True
    )

    output.seek(0)

    return output


def is_control_chat(
    update: Update
) -> bool:
    message = update.effective_message

    if message is None:
        return False

    if message.chat_id != CONTROL_CHAT_ID:
        logger.warning(
            "Команда отклонена "
            "из чата %s",
            message.chat_id
        )

        return False

    return True


def get_command_text(
    update: Update
) -> str:
    message = update.effective_message

    if message is None:
        return ""

    # Можно написать готовый текст,
    # а затем ответить на него
    # командой /set_caption
    if message.reply_to_message:
        replied = message.reply_to_message

        return (
            replied.text
            or replied.caption
            or ""
        ).strip()

    text = (
        message.text
        or message.caption
        or ""
    )

    parts = text.split(
        maxsplit=1
    )

    if len(parts) == 2:
        return parts[1].strip()

    return ""


async def create_board(
    bot: Bot
) -> bool:
    target_chat_id = state[
        "target_chat_id"
    ]

    if target_chat_id is None:
        return False

    caption, second_text = (
        build_messages()
    )

    photo_message = await bot.send_photo(
        chat_id=target_chat_id,
        photo=create_black_photo(),
        caption=caption
    )

    if second_text:
        text_message = await bot.send_message(
            chat_id=target_chat_id,
            text=second_text
        )

        text_message_id = (
            text_message.message_id
        )

    else:
        text_message_id = None

    state["target_chat_id"] = (
        photo_message.chat_id
    )

    state["photo_message_id"] = (
        photo_message.message_id
    )

    state["text_message_id"] = (
        text_message_id
    )

    save_state()

    return True


async def update_board(
    bot: Bot
) -> bool:
    async with state_lock:
        target_chat_id = state[
            "target_chat_id"
        ]

        photo_message_id = state[
            "photo_message_id"
        ]

        if target_chat_id is None:
            return False

        # Если сообщения ещё не созданы,
        # создаём новую пару.
        if photo_message_id is None:
            return await create_board(bot)

        caption, second_text = (
            build_messages()
        )

        # Обновляем подпись первого
        # сообщения с фотографией.
        try:
            await bot.edit_message_caption(
                chat_id=target_chat_id,
                message_id=photo_message_id,
                caption=caption
            )

        except BadRequest as error:
            error_text = str(error).lower()

            if (
                "message is not modified"
                not in error_text
            ):
                logger.warning(
                    "Первое сообщение "
                    "недоступно. Создаём "
                    "новую пару: %s",
                    error
                )

                return await create_board(bot)

        text_message_id = state[
            "text_message_id"
        ]

        # Обновляем второе сообщение.
        if second_text:
            if text_message_id:
                try:
                    await bot.edit_message_text(
                        chat_id=target_chat_id,
                        message_id=text_message_id,
                        text=second_text
                    )

                except BadRequest as error:
                    error_text = (
                        str(error).lower()
                    )

                    if (
                        "message is not modified"
                        not in error_text
                    ):
                        sent = (
                            await bot.send_message(
                                chat_id=(
                                    target_chat_id
                                ),
                                text=second_text
                            )
                        )

                        state[
                            "text_message_id"
                        ] = sent.message_id

                        save_state()

            else:
                sent = await bot.send_message(
                    chat_id=target_chat_id,
                    text=second_text
                )

                state["text_message_id"] = (
                    sent.message_id
                )

                save_state()

        # Если аккаунтов стало 30 или меньше,
        # второе сообщение больше не нужно.
        elif text_message_id:
            try:
                await bot.edit_message_text(
                    chat_id=target_chat_id,
                    message_id=text_message_id,
                    text=(
                        "⚪ Вторая часть таблицы "
                        "сейчас не используется."
                    )
                )

            except BadRequest:
                pass

        return True


async def chat_id_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> None:
    if update.effective_message:
        await (
            update.effective_message
            .reply_text(
                "ID этого чата: "
                f"{update.effective_message.chat_id}"
            )
        )


async def set_target(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not is_control_chat(update):
        return

    message = update.effective_message

    if len(context.args) != 1:
        await message.reply_text(
            "Использование:\n\n"
            "/set_target @username\n\n"
            "или:\n\n"
            "/set_target -100123456789"
        )

        return

    raw_target = context.args[0]

    if raw_target.lstrip("-").isdigit():
        target: int | str = int(
            raw_target
        )
    else:
        target = raw_target

    try:
        chat = await context.bot.get_chat(
            target
        )

    except TelegramError as error:
        await message.reply_text(
            "Не удалось найти группу:\n"
            f"{error}"
        )

        return

    async with state_lock:
        state["target_chat_id"] = chat.id
        state["photo_message_id"] = None
        state["text_message_id"] = None

        save_state()

    await message.reply_text(
        "✅ Выбрана группа: "
        f"{chat.title or chat.id}\n\n"
        "Теперь напиши /status_new"
    )


async def status_new(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not is_control_chat(update):
        return

    try:
        async with state_lock:
            created = await create_board(
                context.bot
            )

        if created:
            answer = (
                "✅ Два новых сообщения "
                "опубликованы."
            )
        else:
            answer = (
                "Сначала используй "
                "/set_target."
            )

    except (
        TelegramError,
        ValueError
    ) as error:
        answer = f"Ошибка:\n{error}"

    await (
        update.effective_message
        .reply_text(answer)
    )


async def set_count(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> None:
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
        1
        <= new_count
        <= MAX_ACCOUNTS
    ):
        await message.reply_text(
            "Допустимо от 1 до "
            f"{MAX_ACCOUNTS}."
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
            "✅ Теперь аккаунтов: "
            f"{new_count}."
        )

    except (
        TelegramError,
        ValueError
    ) as error:
        await message.reply_text(
            "Количество сохранено, "
            "но публикация "
            "не обновилась:\n"
            f"{error}"
        )


async def set_caption(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not is_control_chat(update):
        return

    message = update.effective_message
    new_text = get_command_text(update)

    if not new_text:
        await message.reply_text(
            "Напиши текст после команды:\n\n"
            "/set_caption Мой текст\n"
            "https://example.com\n\n"
            "Также можно ответить командой "
            "/set_caption на готовое сообщение."
        )

        return

    old_text = state["custom_text"]

    async with state_lock:
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

    except (
        TelegramError,
        ValueError
    ) as error:
        await message.reply_text(
            f"Ошибка обновления:\n{error}"
        )


async def status_refresh(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not is_control_chat(update):
        return

    try:
        updated = await update_board(
            context.bot
        )

        if updated:
            answer = (
                "✅ Оба сообщения обновлены."
            )
        else:
            answer = (
                "Сначала используй "
                "/set_target."
            )

    except (
        TelegramError,
        ValueError
    ) as error:
        answer = f"Ошибка:\n{error}"

    await (
        update.effective_message
        .reply_text(answer)
    )


async def status_info(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not is_control_chat(update):
        return

    await (
        update.effective_message
        .reply_text(
            "Аккаунтов: "
            f"{state['accounts_count']}\n"
            "TARGET: "
            f"{state['target_chat_id'] or 'не выбран'}\n"
            "Сообщение 1: "
            f"{state['photo_message_id'] or 'нет'}\n"
            "Сообщение 2: "
            f"{state['text_message_id'] or 'нет'}"
        )
    )


async def source_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> None:
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
        current_status = (
            state["statuses"].get(
                str(account)
            )
        )

        if current_status == status:
            return

        state["statuses"][
            str(account)
        ] = status

        save_state()

    logger.info(
        "ACCOUNT %s -> %s",
        account,
        status
    )

    try:
        await update_board(
            context.bot
        )

    except (
        TelegramError,
        ValueError
    ):
        logger.exception(
            "Ошибка обновления статуса"
        )


async def post_init(
    application: Application
) -> None:
    me = await application.bot.get_me()

    logger.info(
        "Запущен @%s",
        me.username
    )

    if (
        state["target_chat_id"]
        and state["photo_message_id"]
    ):
        try:
            await update_board(
                application.bot
            )

        except (
            TelegramError,
            ValueError
        ):
            logger.exception(
                "Первое обновление "
                "не удалось"
            )


def main() -> None:
    load_state()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    application.add_handler(
        CommandHandler(
           
