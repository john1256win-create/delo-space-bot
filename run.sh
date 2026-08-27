#!/bin/bash
# run.sh — запуск бота в правильном venv

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Проверяем наличие venv
if [ ! -d ".venv" ]; then
    echo "❌ venv не найден. Создаю..."
    python3.11 -m venv .venv
    source .venv/bin/activate
    pip install --upgrade pip
    pip install -r requirements.txt
else
    source .venv/bin/activate
fi

# Проверяем .env
if [ ! -f ".env" ]; then
    echo "⚠ .env не найден. Копирую из .env.example..."
    cp .env.example .env
    echo "📝 Заполни .env перед запуском!"
    exit 1
fi

# Запускаем бота
echo "🚀 Запускаю бота..."
exec python bot.py
