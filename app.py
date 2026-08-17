"""Вход для шаблона Flask на Timeweb: HTTP /health + бот в фоне."""

from __future__ import annotations

import os
import threading
import traceback

os.environ["SKIP_HEALTH"] = "1"

from flask import Flask

app = Flask(__name__)
_bot_lock = threading.Lock()
_bot_thread: threading.Thread | None = None


@app.get("/")
@app.get("/health")
def health() -> tuple[str, int]:
    return "ok", 200


def _run_bot() -> None:
    print("app.py: запускаю Telegram-бота", flush=True)
    try:
        from bot import main

        main()
    except SystemExit as exc:
        print(f"app.py: бот остановился: {exc}", flush=True)
    except Exception:
        traceback.print_exc()


def start_bot_thread() -> None:
    global _bot_thread
    with _bot_lock:
        if _bot_thread is not None and _bot_thread.is_alive():
            return
        _bot_thread = threading.Thread(
            target=_run_bot,
            daemon=True,
            name="telegram-bot",
        )
        _bot_thread.start()


start_bot_thread()
