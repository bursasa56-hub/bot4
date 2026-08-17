# TikTok Video Finder Bot

Telegram-бот ищет свежие русские вирусные ролики TikTok и присылает ссылку, лайки и самый залайканный комментарий.

Команды:
- `/start` — описание и поток роликов
- `/stop` — остановить поток

## Что класть в GitHub

Заливай только это:

- `bot.py`
- `tiktok.py`
- `app.py`
- `requirements.txt`
- `.gitignore`
- `.env.example`
- `README.md`

**Не заливай** `.env` — там токен бота. Он уже в `.gitignore`.

## Локальный запуск

```bash
py -3 -m pip install -r requirements.txt
```

Создай `.env`:

```
BOT_TOKEN=токен_от_BotFather
```

```bash
py -3 bot.py
```

## GitHub

1. Создай репозиторий на GitHub (можно приватный).
2. В папке проекта:

```bash
git init
git add bot.py tiktok.py app.py requirements.txt .gitignore .env.example README.md
git commit -m "Telegram bot"
git branch -M main
git remote add origin https://github.com/ТВОЙ_НИК/ИМЯ_РЕПО.git
git push -u origin main
```

Проверь на GitHub, что файла `.env` там нет.

## Деплой на Timeweb Cloud Apps

Обычный хостинг Timeweb для сайтов не подойдёт: бот должен работать постоянно. Нужен [Timeweb Cloud → App Platform](https://timeweb.cloud/my/apps).

1. Открой [App Platform](https://timeweb.cloud/my/apps) → **Добавить** → тип **Backend**.
2. Язык **Python**, фреймворк можно **Other / Python**.
3. Подключи GitHub, выбери репозиторий и ветку `main`. Включи сборку по последнему коммиту.
4. Выбери регион и тариф.
5. В настройках приложения:

   **Команда сборки:**

   ```
   pip3 install --upgrade -r requirements.txt
   ```

   **Команда запуска:**

   ```
   python3 bot.py
   ```

   **Переменные окружения** (токен не в GitHub, а здесь):

   | Ключ | Значение |
   |---|---|
   | `BOT_TOKEN` | токен от BotFather |
   | `PORT` | `80` |

   **Путь проверки состояния:** `/health`

6. Перед деплоем останови локальный `py -3 bot.py`. Два процесса с одним токеном конфликтуют.
7. Запусти деплой и смотри **логи приложения**. Должны появиться строки `bot.py: процесс стартовал` и `Бот запущен`.
8. В Telegram напиши боту `/start`.

Если в логах нет `Бот запущен`:

- команда запуска всё ещё Flask (`flask run` / `gunicorn ...`) — поставь `python3 bot.py`, либо оставь Flask, но залей `app.py`;
- нет переменной `BOT_TOKEN` — тогда будет ошибка про токен, а не `Бот запущен`;
- бот ещё крутится у тебя на компьютере — в логах будет конфликт `getUpdates`.

После каждого `git push` в `main` Timeweb сам пересоберёт приложение, если автодеплой включён.

Если Apps капризничает с долгим polling-ботом, запасной вариант — облачный сервер Timeweb (VPS): клонировать репозиторий, поставить зависимости, запустить `python3 bot.py` через systemd.
