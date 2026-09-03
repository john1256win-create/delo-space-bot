#!/usr/bin/env python3
"""
run_milestones_daily.py — ежедневный прогон мониторинга контрольных точек релизов.

Запускается по расписанию (launchd, 8:00). Проверяет все релизы Портфеля проектов:
если сегодня первый день какого-то этапа (разработка, тестирование, накат) —
отправляет сообщение в канал «Релизы субхолдингов по 1С».

Если контрольных точек сегодня нет — ничего не отправляет (silent mode).
"""
import sys
import asyncio
from uuid import UUID

sys.path.insert(0, "/Users/salnikov/ТЗ_Проекты/_Инструменты/1c_releases/delo_space_bot")

from datetime import date

# Канал «Релизы субхолдингов по 1С» — куда шлёт уведомления этот бэк
BOT_ID = "d2863b44-7aee-5a07-bc4c-9a6098b5696e"
CHAT_ID = "23f62f30-5917-5510-8401-72c9a6df5181"


def main():
    import release_milestones as rm

    today = date.today()
    print(f"[{today}] 🔍 Проверяю контрольные точки релизов...")

    messages = rm.run_milestone_check(target=today)

    if not messages:
        print("✅ Контрольных точек сегодня нет — не отправляю в канал")
        return 0

    print(f"   Найдено контрольных точек: {len(messages)}")

    async def _send_all():
        from bot import get_bot
        b = get_bot()
        await b.startup()
        try:
            for msg in messages:
                print(f"   Отправляю: {msg.splitlines()[0]}")
                await b.send_message(
                    bot_id=UUID(BOT_ID),
                    chat_id=UUID(CHAT_ID),
                    body=msg,
                    wait_callback=False
                )
            print(f"   ✅ Отправлено сообщений в канал «Релизы субхолдингов по 1С»: {len(messages)}")
        finally:
            await b.shutdown()

    try:
        asyncio.run(_send_all())
    except Exception as e:
        print(f"❌ Ошибка отправки: {e}")
        return 1

    return 0


rc = main()
print(f">>> exit code {rc}")
sys.exit(rc)