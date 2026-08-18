"""Telegram-бот для поиска свежих русских вирусных видео TikTok."""

from __future__ import annotations

import asyncio
import html
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

print("bot.py: процесс стартовал", flush=True)

from dotenv import load_dotenv
from telegram import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import Forbidden
from telegram.ext import Application, CommandHandler, ContextTypes

from tiktok import TikTokVideo, VideoPool

load_dotenv(Path(__file__).resolve().parent / ".env")

logging.basicConfig(
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_DESCRIPTION = (
    "👋 Привет! Я бот для поиска залётных роликов TikTok "
    "из <b>России, Беларуси и Казахстана</b>.\n\n"
    "Ищу в фоне ещё до твоего сообщения, поэтому ссылки прилетают сразу, "
    "как только в запасе есть ролики:\n"
    "• стримеры, блогеры, тиктокеры, пранки, хайп\n"
    "• младше 24 часов — от 5 000 лайков\n"
    "• 24–36 часов — от 10 000 лайков\n"
    "• новое видео примерно каждые 2–3 минуты\n\n"
    "Остановить поток: /stop"
)

_stop_events: dict[int, asyncio.Event] = {}
_seen_ids: dict[int, set[str]] = {}


def format_number(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def format_age(seconds: int) -> str:
    if seconds < 60:
        return "только что"
    if seconds < 3600:
        minutes = seconds // 60
        return f"{minutes} мин. назад"
    hours = seconds // 3600
    return f"{hours} ч. назад"


def likes_word(value: int) -> str:
    n = abs(value) % 100
    n1 = n % 10
    if 11 <= n <= 14:
        return "лайков"
    if n1 == 1:
        return "лайк"
    if 2 <= n1 <= 4:
        return "лайка"
    return "лайков"


def video_caption(video: TikTokVideo) -> str:
    title = html.escape(video.title)
    if len(title) > 180:
        title = title[:177] + "..."
    author = html.escape(video.author_id or video.author_name or "неизвестно")
    parts = [
        f"❤️ {format_number(video.likes)} лайков",
        f"⏱ {format_age(video.age_seconds)}",
        f"👤 @{author}",
    ]
    if title:
        parts.append(f"\n{title}")
    if video.top_comment:
        comment = html.escape(video.top_comment)
        if len(comment) > 400:
            comment = comment[:397] + "..."
        parts.append(
            f"\n💬 Самый залайканный комментарий · {format_number(video.top_comment_likes)} "
            f"{likes_word(video.top_comment_likes)}:\n<i>{comment}</i>"
        )
    parts.append(f'\n<a href="{html.escape(video.tiktok_url, quote=True)}">Ссылка на ролик</a>')
    parts.append(video.tiktok_url)
    return "\n".join(parts)


def video_keyboard(video: TikTokVideo) -> InlineKeyboardMarkup | None:
    username = (video.author_id or "").strip().lstrip("@")
    if not username:
        return None
    label = f"📋 Скопировать @{username}"
    if len(label) > 64:
        label = "📋 Скопировать юзернейм"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    text=label,
                    copy_text=CopyTextButton(text=username),
                )
            ]
        ]
    )


async def send_one_video(bot, chat_id: int, video: TikTokVideo) -> None:
    await bot.send_message(
        chat_id=chat_id,
        text=video_caption(video),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
        reply_markup=video_keyboard(video),
    )


def _is_running(chat_id: int) -> bool:
    event = _stop_events.get(chat_id)
    return event is not None and not event.is_set()


def _pool(context: ContextTypes.DEFAULT_TYPE) -> VideoPool:
    return context.application.bot_data["pool"]


async def stream_videos(bot, chat_id: int, stop_event: asyncio.Event, pool: VideoPool) -> None:
    seen = _seen_ids.setdefault(chat_id, set())
    try:
        while not stop_event.is_set():
            sent_now = False
            for video in pool.snapshot():
                if stop_event.is_set():
                    return
                if video.video_id in seen:
                    continue
                seen.add(video.video_id)
                try:
                    await send_one_video(bot, chat_id, video)
                except Forbidden:
                    logger.info("Пользователь %s заблокировал бота", chat_id)
                    stop_event.set()
                    return
                sent_now = True
                await asyncio.sleep(0.25)

            await pool.wait_change(timeout=0.5 if sent_now else 0.6)
    except asyncio.CancelledError:
        stop_event.set()
        raise
    except Exception:
        logger.exception("Ошибка потока видео для чата %s", chat_id)
    finally:
        if _stop_events.get(chat_id) is stop_event:
            _stop_events.pop(chat_id, None)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat:
        return

    chat_id = update.effective_chat.id
    if _is_running(chat_id):
        await update.message.reply_text(
            "Я уже присылаю видео.\nОстановить: /stop"
        )
        return

    stop_event = asyncio.Event()
    _stop_events[chat_id] = stop_event
    _seen_ids.setdefault(chat_id, set())
    pool = _pool(context)

    await update.message.reply_text(
        BOT_DESCRIPTION,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )
    context.application.create_task(
        stream_videos(context.bot, chat_id, stop_event, pool)
    )


async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat:
        return

    chat_id = update.effective_chat.id
    event = _stop_events.get(chat_id)
    if event is None or event.is_set():
        await update.message.reply_text(
            "Сейчас я ничего не присылаю. Напиши /start, чтобы снова запустить поток."
        )
        return

    event.set()
    await update.message.reply_text("Остановил поток. Чтобы снова начать — /start")


async def post_init(application: Application) -> None:
    pool = VideoPool()
    application.bot_data["pool"] = pool
    asyncio.get_running_loop().create_task(pool.run())


async def post_shutdown(application: Application) -> None:
    pool = application.bot_data.get("pool")
    if isinstance(pool, VideoPool):
        pool.stop_event.set()


def _health_port() -> int | None:
    if os.getenv("SKIP_HEALTH", "").strip().lower() in {"1", "true", "yes"}:
        return None
    port_raw = os.getenv("PORT", "").strip()
    if port_raw:
        try:
            return int(port_raw)
        except ValueError:
            return None
    # Timeweb иногда не задаёт PORT, но ищет HTTP-порт у контейнера.
    if Path("/.dockerenv").exists():
        return 80
    return None


def start_health_server() -> None:
    """Нужен Timeweb Apps: отвечает 200 на /health, чтобы контейнер считали живым."""
    port = _health_port()
    if port is None:
        return

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("Health-сервер слушает 0.0.0.0:%s", port)


def main() -> None:
    logger.info("Бот: старт main()")
    start_health_server()
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token or token == "your_telegram_bot_token":
        logger.error("BOT_TOKEN не задан. Добавь его в переменные приложения Timeweb.")
        raise SystemExit(
            "Укажи токен бота в переменных окружения: BOT_TOKEN=токен_от_BotFather"
        )

    logger.info("BOT_TOKEN найден, подключаюсь к Telegram")
    application = (
        Application.builder()
        .token(token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stop", stop))
    application.add_handler(CommandHandler("help", start))

    logger.info("Бот запущен")
    # Python 3.14 больше не создаёт event loop сам — Timeweb как раз на 3.14.
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
