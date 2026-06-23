import os
import time
import asyncio

from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    ContextTypes,
    filters,
)

timers = {}


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    if not update.message.reply_to_message:
        return

    try:
        hours = int(update.message.text.strip())
    except:
        return

    target = update.message.reply_to_message

    finish_time = time.time() + hours * 3600

    timers[target.message_id] = {
        "chat_id": target.chat_id,
        "message_id": target.message_id,
        "finish_time": finish_time,
    }

    await update.message.reply_text(
        f"Таймер запущен на {hours} ч."
    )


async def timer_loop(app):
    while True:
        now = time.time()

        for key in list(timers.keys()):
            timer = timers[key]

            left = int(timer["finish_time"] - now)

            try:
                if left <= 0:
                    text = "🟢 Свободно"
                    del timers[key]
                else:
                    h = left // 3600
                    m = (left % 3600) // 60
                    text = f"⏳ Осталось {h}ч {m}м"

                await app.bot.edit_message_text(
                    chat_id=timer["chat_id"],
                    message_id=timer["message_id"],
                    text=text,
                )

            except Exception:
                pass

        await asyncio.sleep(60)


async def post_init(app):
    asyncio.create_task(timer_loop(app))


def main():
    token = os.getenv("BOT_TOKEN")

    app = Application.builder().token(token).post_init(post_init).build()

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            message_handler
        )
    )

    app.run_polling()


if __name__ == "__main__":
    main()
