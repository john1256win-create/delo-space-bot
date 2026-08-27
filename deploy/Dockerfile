# Dockerfile — образ бота 1C Info Bot для Delo Space
FROM python:3.11-slim

# Системные зависимости (для lxml/bs4 и работы сети)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Сначала зависимости (кэшируется слоями)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Код бота (без .env и .venv — секреты не в образ)
COPY bot.py scraper.py formatter.py config.py run_daily.py ./
COPY output/ ./output/
COPY data/ ./data/

# Директории для данных (смонтируются volume'ами)
RUN mkdir -p /app/output /app/data

# Внутренний порт бота
EXPOSE 8000

# Запуск бота (FastAPI + uvicorn)
CMD ["python", "bot.py"]