"""Конфигурация бота — загрузка из .env."""
import os
from pathlib import Path
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
ENV_FILE = HERE / ".env"

if ENV_FILE.exists():
    load_dotenv(ENV_FILE)

# BotX credentials (выдаёт админ Delo Space)
BOT_ID = os.getenv("BOT_ID", "")
CTS_URL = os.getenv("CTS_URL", "")
SECRET_KEY = os.getenv("SECRET_KEY", "")

# 1C:ИТС
ITS_USERNAME = os.getenv("ITS_USERNAME", "")
ITS_PASSWORD = os.getenv("ITS_PASSWORD", "")

# Куда слать уведомления (ID чата в Delo Space)
CHAT_ID = os.getenv("CHAT_ID", "")

# Мониторинг v8.1c.ru/lawmonitor (временное решение)
ENABLE_LAWMONITOR = os.getenv("ENABLE_LAWMONITOR", "true").lower() == "true"

# Пути
DATA_DIR = HERE / "data"
OUT_DIR = HERE / "output"
COOKIE_FILE = HERE / "cookies_safe.json"
CRED_FILE = HERE / "credentials.json"

DATA_DIR.mkdir(parents=True, exist_ok=True)
OUT_DIR.mkdir(parents=True, exist_ok=True)