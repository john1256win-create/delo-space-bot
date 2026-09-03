#!/usr/bin/env python3
"""
run_buhexpert_15th.py — анонс семинаров на следующий месяц.

Запускается 15-го числа каждого месяца (launchd). Берёт из БД семинары,
запланированные на следующий месяц, и отправляет анонс в канал «БухЭксперт»:
    Дата - Время - Название (гиперссылка) - Лектор
"""
import asyncio
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import buhexpert_scraper as bs
import buhexpert_send as bsend


async def main():
    today = date.today()
    # Следующий месяц
    if today.month == 12:
        year, month = today.year + 1, 1
    else:
        year, month = today.year, today.month + 1

    seminars = bs.get_seminars_for_month(year, month)
    label = f"{bsend.MONTHS_RU[month]} {year}"
    text = bsend.fmt_announcement(seminars, label)
    print(f"[бухэксперт:15] Анонс на {label}: {len(seminars)} семинаров")

    await bsend.send_message(text)
    print("[бухэксперт:15] ✅ Анонс отправлен в канал «БухЭксперт»")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))