import asyncio
import io
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont
from telegram import Bot, InputMediaPhoto, Update
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
    min(300, int(os.getenv("ACCOUNTS_COUNT", "50")))
)

STATE_FILE = Path(
    os.getenv("STATE_FILE", "bot_state.json")
)

UPDATE_INTERVAL = 60
MAX_ACCOUNTS = 300
ROWS_PER_COLUMN = 50

FREE_STATUS = "🟢 СВОБОДЕН"
DEFAULT_CAPTION = "Статус аренды"


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)
state_lock = asyncio.Lock()


def initial_target() -> int | str | None:
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

    "target_chat_id": initial_target(),
    "target_message_id": None,
    "caption": DEFAULT_CAPTION,
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
                initial_target()
            ),

            "target_message_id": saved.get(
                "target_message_id"
            ),

            "caption": str(
                saved.get(
                    "caption",
                    DEFAULT_CAPTION
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
            "Не удалось загрузить bot_state.json"
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
            "🔴 НЕ ДОСТУПЕН — БАН МЕТРО"
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


def find_font(
    size: int,
    bold: bool = False
):
    if bold:
        names = [
            (
                "/usr/share/fonts/truetype/"
                "dejavu/DejaVuSans-Bold.ttf"
            ),
            (
                "/usr/share/fonts/truetype/"
                "liberation2/"
                "LiberationSans-Bold.ttf"
            ),
        ]

    else:
        names = [
            (
                "/usr/share/fonts/truetype/"
                "dejavu/DejaVuSans.ttf"
            ),
            (
                "/usr/share/fonts/truetype/"
                "liberation2/"
                "LiberationSans-Regular.ttf"
            ),
        ]

    for name in names:
        if Path(name).exists():
            return ImageFont.truetype(
                name,
                size
            )

    return ImageFont.load_default()


def image_status(
    status: str
) -> tuple[
    str,
    tuple[int, int, int]
]:
    if status.startswith("🟢"):
        clean_status = status.replace(
            "🟢",
            "●",
            1
        )

        return (
            clean_status,
            (70, 230, 110)
        )

    if status.startswith(
        ("🔴", "⚫")
    ):
        clean_status = (
            status
            .replace("🔴", "●", 1)
            .replace("⚫", "●", 1)
        )

        return (
            clean_status,
            (255, 90, 90)
        )

    return (
        status,
        (240, 240, 240)
    )


def render_status_image() -> io.BytesIO:
    count = state["accounts_count"]

    columns = max(
        1,
        (
            count
            + ROWS_PER_COLUMN
            - 1
        )
        // ROWS_PER_COLUMN
    )

    rows = min(
        count,
        ROWS_PER_COLUMN
    )

    column_width = 720
    margin = 55
    header_height = 135
    row_height = 43

    width = (
        margin * 2
        + column_width * columns
    )

    height = (
        header_height
        + rows * row_height
        + 55
    )

    image = Image.new(
        "RGB",
        (width, height),
        "black"
    )

    draw = ImageDraw.Draw(image)

    title_font = find_font(
        43,
        bold=True
    )

    row_font = find_font(27)

    draw.text(
        (margin, 38),
        "СТАТУС АРЕНДЫ",
        font=title_font,
        fill="white"
    )

    for account in range(
        1,
        count + 1
    ):
        column = (
            account - 1
        ) // ROWS_PER_COLUMN

        row = (
            account - 1
        ) % ROWS_PER_COLUMN

        status, color = image_status(
            state["statuses"][str(account)]
        )

        x = (
            margin
            + column * column_width
        )

        y = (
            header_height
            + row * row_height
        )

        draw.text(
            (x, y),
            (
                f"ACCOUNT {account} "
                f"— {status}"
            ),
            font=row_font,
            fill=color
        )

    output = io.BytesIO()
    output.name = "status.png"

    image.save(
        output,
        format="PNG",
        optimize=True
    )

    output.seek(0)

    return output


def caption_from_command(
    update: Update
) -> str:
    message = update.effective_message

    if message is None:
        return ""

    # Можно ответить командой
    # на готовое сообщение с текстом.
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


def is_control(
    update: Update
) -> bool:
    message = update.effective_message

    if (
        message
        and message.chat_id
        == CONTROL_CHAT_ID
    ):
        return True

    if message:
        logger.warning(
            "Команда отклонена "
            "из чата %s",
            message.chat_id
        )

    return False


async def publish_status(
    bot: Bot,
    force_new: bool = False
) -> bool:
    async with state_lock:
        target = state[
            "target_chat_id"
        ]

        if target is None:
            return False

        picture = render_status_image()

        if force_new:
            message_id = None
        else:
            message_id = state[
                "target_message_id"
            ]

        # Обновляем существующее сообщение
        if message_id is not None:
            try:
                media = InputMediaPhoto(
                    media=picture,
                    caption=state["caption"]
                )

                await bot.edit_message_media(
                    chat_id=target,
                    message_id=message_id,
                    media=media
                )

                return True

            except BadRequest as error:
                if (
                    "message is not modified"
                    in str(error).lower()
                ):
                    return True

                logger.warning(
                    "Старое сообщение "
                    "нельзя изменить: %s",
                    error
                )

        # Создаём новое сообщение
        picture.seek(0)

        message = await bot.send_photo(
            chat_id=target,
            photo=picture,
            caption=state["caption"]
        )

        state["target_chat_id"] = (
            message.chat_id
        )

        state["target_message_id"] = (
            message.message_id
        )

        save_state()

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
    if not is_control(update):
        return

    message = update.effective_message

    if len(context.args) != 1:
        await message.reply_text(
            "Использование:\n"
            "/set_target @username\n\n"
            "или:\n"
            "/set_target -100123456789"
        )

        return

    value = context.args[0]

    if value.lstrip("-").isdigit():
        target: int | str = int(value)
    else:
        target = value

    try:
        chat = await context.bot.get_chat(
            target
        )

    except TelegramError as error:
        await message.reply_text(
            "Не удалось найти "
            f"целевой чат: {error}"
        )

        return

    async with state_lock:
        state["target_chat_id"] = chat.id
        state["target_message_id"] = None

        save_state()

    await message.reply_text(
        "✅ Цель выбрана: "
        f"{chat.title or chat.id}\n\n"
        "Теперь отправь /status_new"
    )


async def status_new(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not is_control(update):
        return

    try:
        published = await publish_status(
            context.bot,
            force_new=True
        )

        if published:
            answer = (
                "✅ Новое сообщение "
                "опубликовано."
            )
        else:
            answer = (
                "Сначала используй "
                "/set_target."
            )

    except TelegramError as error:
        answer = (
            "Не удалось опубликовать. "
            "Проверь права бота:\n"
            f"{error}"
        )

    await (
        update.effective_message
        .reply_text(answer)
    )


async def set_count(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not is_control(update):
        return

    message = update.effective_message

    if (
        len(context.args) != 1
        or not context.args[0].isdigit()
    ):
        await message.reply_text(
            "Использование: /set_count 75"
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
            f"{MAX_ACCOUNTS} аккаунтов."
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

    await publish_status(
        context.bot
    )

    await message.reply_text(
        "✅ Теперь аккаунтов: "
        f"{new_count}."
    )


async def set_caption(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not is_control(update):
        return

    message = update.effective_message
    caption = caption_from_command(update)

    if not caption:
        await message.reply_text(
            "Напиши текст после команды:\n\n"
            "/set_caption Мой текст и ссылка\n"
            "https://example.com\n\n"
            "Или ответь командой /set_caption "
            "на сообщение с готовым текстом."
        )

        return

    if len(caption) > 1024:
        await message.reply_text(
            "Подпись длиннее 1024 символов.\n"
            f"Сейчас: {len(caption)}."
        )

        return

    async with state_lock:
        state["caption"] = caption

        save_state()

    await publish_status(
        context.bot
    )

    await message.reply_text(
        "✅ Текст и ссылки сохранены."
    )


async def status_refresh(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not is_control(update):
        return

    try:
        updated = await publish_status(
            context.bot
        )

        if updated:
            answer = "✅ Таблица обновлена."
        else:
            answer = (
                "Сначала используй "
                "/set_target и /status_new."
            )

    except TelegramError as error:
        answer = (
            "Ошибка обновления:\n"
            f"{error}"
        )

    await (
        update.effective_message
        .reply_text(answer)
    )


async def status_info(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not is_control(update):
        return

    await (
        update.effective_message
        .reply_text(
            "Аккаунтов: "
            f"{state['accounts_count']}\n"
            f"SOURCE: {SOURCE_CHAT_ID}\n"
            f"CONTROL: {CONTROL_CHAT_ID}\n"
            "TARGET: "
            f"{state['target_chat_id'] or 'не выбран'}\n"
            "MESSAGE: "
            f"{state['target_message_id'] or 'не создано'}\n"
            "Подпись: "
            f"{len(state['caption'])}/1024 символов"
        )
    )


async def source_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.effective_message

    if message is None:
        return

    if (
        message.chat_id
        != SOURCE_CHAT_ID
    ):
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
        await publish_status(
            context.bot
        )

    except TelegramError:
        logger.exception(
            "Не удалось обновить публикацию"
        )


async def minute_update(
    context: ContextTypes.DEFAULT_TYPE
) -> None:
    try:
        await publish_status(
            context.bot
        )

    except TelegramError:
        logger.exception(
            "Ошибка минутного обновления"
        )


async def post_init(
    application: Application
) -> None:
    me = await application.bot.get_me()

    logger.info(
        "Бот запущен как @%s | "
        "CONTROL_CHAT_ID=%s",
        me.username,
        CONTROL_CHAT_ID
    )

    if (
        state["target_chat_id"]
        is not None
        and state["target_message_id"]
        is not None
    ):
        try:
            await publish_status(
                application.bot
            )

        except TelegramError:
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

    # Эту команду можно использовать
    # в любой группе, чтобы узнать её ID.
    application.add_handler(
        CommandHandler(
            "chat_id",
            chat_id_command
        )
    )

    application.add_handler(
        CommandHandler(
            "set_target",
            set_target
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

    application.job_queue.run_repeating(
        minute_update,
        interval=UPDATE_INTERVAL,
        first=UPDATE_INTERVAL
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
