#!/usr/bin/env python3
"""Тестовый прогон: анонс на сентябрь + напоминание о завтрашнем событии в канал «БухЭксперт»."""
import asyncio, sys
from datetime import date, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import buhexpert_scraper as bs
import buhexpert_send as bsend
from bot import get_bot

CHAT_BUHEXPERT = bsend.CHAT_BUHEXPERT


async def main():
    b = get_bot()
    await b.startup()
    try:
        # 1. Анонс на сентябрь 2026
        sept = bs.get_seminars_for_month(2026, 9)
        text = bsend.fmt_announcement(sept, "сентябрь 2026")
        print(f"=== Анонс на сентябрь ({len(sept)} семинаров) ===")
        await bsend.send_message(text, bot=b)
        print("✅ Анонс отправлен в канал «БухЭксперт»")

        # 2. Напоминание о завтрашнем событии (04.09)
        tomorrow = date.today() + timedelta(days=1)
        sems = bs.get_seminars_by_date(tomorrow)
        print(f"\n=== Напоминание на {tomorrow} ({len(sems)} событий) ===")
        for sem in sems:
            rem = bsend.format_reminder(sem)
            await bsend.send_message(rem, bot=b)
            print(f"✅ Напоминание: {sem['title'][:50]}")
    finally:
        await b.shutdown()


asyncio.run(main())