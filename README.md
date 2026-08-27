# 1C Releases Bot для Delo Space (eXpress)

Бот для мониторинга релизов 1С на портале releases.1c.ru. Адаптация Telegram-бота `1c_release_scraper` для корпоративного мессенджера Delo Space (eXpress).

## Функциональность

- `/check` — проверить изменения релизов и отправить новые/удалённые
- `/report` — полная сводка по всем продуктам
- `/query <название>` — найти продукт по названию (например, `/query ERP`)
- `/help` — справка по командам

## Структура проекта

```
delo_space_bot/
├── bot.py              # Основной бот (pybotx + FastAPI)
├── scraper.py          # Парсинг releases.1c.ru
├── formatter.py        # Форматирование сообщений
├── config.py           # Загрузка настроек из .env
├── requirements.txt    # Зависимости Python
├── .env.example        # Шаблон конфигурации
├── .env                # Реальные настройки (создать вручную)
├── output/             # CSV-файлы с релизами
├── data/               # Временные данные
└── cookies_safe.json   # Кеш сессии ИТС (создаётся автоматически)
```

## Установка

### 1. Клонировать и установить зависимости

```bash
cd ~/ТЗ_Проекты/_Инструменты/1c_releases/delo_space_bot
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Настроить .env

Скопировать `.env.example` в `.env` и заполнить:

```bash
cp .env.example .env
nano .env
```

**Параметры:**

- `BOT_ID`, `CTS_URL`, `SECRET_KEY` — выдаёт администратор Delo Space после регистрации бота
- `ITS_USERNAME`, `ITS_PASSWORD` — логин/пароль от портала 1С:ИТС (releases.1c.ru)
- `CHAT_ID` — ID чата в Delo Space, куда слать уведомления (опционально)

### 3. Получить credentials от админа Delo Space

Подать заявку администраторам с описанием:
- Название бота: `1C Releases Monitor`
- Описание: мониторинг релизов 1С на releases.1c.ru
- URL backend'а: `https://your-server.com/command` (после деплоя)
- Команды: `/check`, `/report`, `/query`, `/help`

Админы выдадут:
- `BOT_ID` (UUID)
- `CTS_URL` (URL корпоративного сервера)
- `SECRET_KEY`

### 4. Запустить бота

```bash
# Вручную
python bot.py

# Или через run.sh
bash run.sh
```

Бот запустится на `http://0.0.0.0:8000`.

## Деплой

### Вариант 1: Локальный сервер (для теста)

```bash
python bot.py
```

### Вариант 2: Docker

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
CMD ["python", "bot.py"]
```

```bash
docker build -t 1c-releases-bot .
docker run -d -p 8000:8000 --env-file .env --name 1c-bot 1c-releases-bot
```

### Вариант 3: Systemd service

Создать `/etc/systemd/system/1c-releases-bot.service`:

```ini
[Unit]
Description=1C Releases Bot for Delo Space
After=network.target

[Service]
Type=simple
User=salnikov
WorkingDirectory=/Users/salnikov/ТЗ_Проекты/_Инструменты/1c_releases/delo_space_bot
Environment="PATH=/Users/salnikov/ТЗ_Проекты/_Инструменты/1c_releases/delo_space_bot/.venv/bin"
ExecStart=/Users/salnikov/ТЗ_Проекты/_Инструменты/1c_releases/delo_space_bot/.venv/bin/python bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable 1c-releases-bot
sudo systemctl start 1c-releases-bot
sudo systemctl status 1c-releases-bot
```

### Вариант 4: Nginx reverse proxy

```nginx
server {
    listen 443 ssl;
    server_name bot.yourcompany.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

## Автоматическая проверка (cron)

Добавить в crontab (`crontab -e`):

```bash
# Ежедневно в 08:00 проверять релизы
0 8 * * * cd /Users/salnikov/ТЗ_Проекты/_Инструменты/1c_releases/delo_space_bot && .venv/bin/python -c "from bot import bot; import asyncio; from scraper import run_scrape; from formatter import format_changes; asyncio.run(bot.startup()); rows, new, removed = run_scrape(); text = format_changes(new, removed); asyncio.run(bot.send_message(chat_id='YOUR_CHAT_ID', text=text)); asyncio.run(bot.shutdown())"
```

Или создать отдельный скрипт `cron_check.py`:

```python
#!/usr/bin/env python3
import asyncio
from bot import bot
from scraper import run_scrape
from formatter import format_changes

async def main():
    await bot.startup()
    try:
        all_rows, new_rows, removed_rows = run_scrape()
        if new_rows or removed_rows:
            text = format_changes(new_rows, removed_rows)
            await bot.send_message(chat_id="YOUR_CHAT_ID", text=text)
    finally:
        await bot.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
```

## Отладка

### Логи

```bash
# Смотреть логи бота
journalctl -u 1c-releases-bot -f

# Или если запущен вручную
python bot.py  # логи в stdout
```

### Проверка авторизации ИТС

```python
from scraper import auth, get_credentials
import requests

session = requests.Session()
if auth(session):
    print("✅ Авторизация успешна")
else:
    print("❌ Ошибка авторизации")
```

### Проверка парсинга

```python
from scraper import run_scrape

all_rows, new_rows, removed_rows = run_scrape()
print(f"Всего релизов: {len(all_rows)}")
print(f"Новых: {len(new_rows)}")
print(f"Удалено: {len(removed_rows)}")
```

## Отличия от Telegram-бота

| Параметр | Telegram | Delo Space |
|----------|----------|------------|
| Фреймворк | python-telegram-bot | pybotx |
| API | Telegram Bot API | BotX API |
| Разметка | HTML/Markdown | HTML (ограниченная) |
| Лимит сообщения | 4096 символов | ~4000 символов |
| Деплой | Любой сервер | Нужен доступ из корпоративной сети |
| Регистрация | @BotFather | Через админа CTS/eCTS |

## Подводные камни

1. **Cookies ИТС протухают** — скрипт автоматически перелогинивается, но если упорно падает, удалить `cookies_safe.json`.
2. **CSS-селекторы** — если 1С поменяет вёрстку `/total`, править `_parse_releases()` в `scraper.py`.
3. **venv** — использовать только `.venv` проекта, не hermes-venv (там сломан urllib3).
4. **BotX credentials** — без `BOT_ID`, `CTS_URL`, `SECRET_KEY` бот не запустится. Сначала получить у админов.
5. **Сетевой доступ** — сервер с ботом должен быть доступен из корпоративной сети (откуда работает BotX).

## Лицензия

MIT

## Автор

Адаптация: Hermes Agent по запросу пользователя.
Оригинальный Telegram-бот: `1c_release_scraper.py`.
