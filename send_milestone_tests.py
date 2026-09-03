#!/usr/bin/env python3
"""Тестовый прогон контрольных точек — отправка в канал «Релизы субхолдингов по 1С»."""
import sys, asyncio
from uuid import UUID
from datetime import date
sys.path.insert(0, "/Users/salnikov/ТЗ_Проекты/_Инструменты/1c_releases/delo_space_bot")

from bot import get_bot
import release_milestones as rm

BOT_ID = "d2863b44-7aee-5a07-bc4c-9a6098b5696e"
CHAT_ID = "23f62f30-5917-5510-8401-72c9a6df5181"  # «Релизы субхолдингов по 1С»

# Три даты: каждая соответствует стадии
CASES = [
    (date(2026, 9, 1), "1С_RK_v27", "тестирование"),
    (date(2026, 9, 30), "1С_TK_v36", "разработка"),
    (date(2026, 10, 19), "ERP_GP_v64", "накат"),
]


async def main():
    b = get_bot()
    await b.startup()
    try:
        i = 0
        for d, rx, label in CASES:
            msgs = rm.run_milestone_check(target=d, release_regex=rx)
            for msg in msgs:
                i += 1
                print(f"--- Тест {i} ({d}, {label}) ---")
                await b.send_message(
                    bot_id=UUID(BOT_ID),
                    chat_id=UUID(CHAT_ID),
                    body=msg,
                    wait_callback=False
                )
        print(f"\n✅ Отправлено тестовых сообщений: {i}")
    finally:
        await b.shutdown()


asyncio.run(main())