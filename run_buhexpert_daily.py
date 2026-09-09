#!/usr/bin/env python3
"""
run_buhexpert_daily.py — напоминание о семинаре за 1 день.

Запускается ежедневно (launchd). Проверяет в БД семинары на завтра.
Для каждого события — отправляет в канал «БухЭксперт»:
    1. Дата - Время - Название (гиперссылка) - Лектор
    2. Продолжение: полный текст контента события (div#seminar-program)
Silent mode: если на завтра нет семинаров — ничего не отправляет.
"""
import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import buhexpert_scraper as bs
import buhexpert_send as bsend


async def main():
    tomorrow = date.today() + timedelta(days=1)
    seminars = bs.get_seminars_by_date(tomorrow)

    if not seminars:
        print(f"[бухэксперт:daily] За {tomorrow} семинаров нет — silent mode")
        return 0

    print(f"[бухэксперт:daily] Завтра ({tomorrow}) семинаров: {len(seminars)}")

    from bot import get_bot
    b = get_bot()
    await b.startup()
    try:
        for sem in seminars:
            # 1. Напоминание
            reminder = bsend.format_reminder(sem)
            await bsend.send_message(reminder, bot=b)
            print(f"[бухэксперт:daily] ✅ Напоминание: {sem['title'][:50]}")

            # 2. Контент события (полный текст)
            content = bsend.get_event_content_for(sem)
            if content:
                header = f"**{sem['title']}**\n"
                await bsend.send_message(header + content, bot=b)
                print(f"[бухэксперт:daily]   ✅ Контент ({len(content)} символов)")
            else:
                print(f"[бухэксперт:daily]   ⚠ Контент события не получен")
    finally:
        await b.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))