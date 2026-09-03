#!/usr/bin/env python3
"""
run_buhexpert_weekly.py — еженедельный сбор семинаров с buhexpert8.ru.

Запускается по понедельникам (launchd). Собирает полный список будущих
семинаров (#seminars-list2) и сохраняет в SQLite (upsert по url).
Молчит, если новых семинаров нет (silent mode). Отчёт о новых — в консоль/лог.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import buhexpert_scraper as bs


def main():
    print("[бухэксперт:weekly] Запуск сбора семинаров...")
    try:
        seminars = bs.fetch_seminars()
    except Exception as e:
        print(f"[бухэксперт] ❌ Ошибка сбора списка: {e}")
        return 1

    new_count = bs.upsert_seminars(seminars)
    print(f"[бухэксперт] Собрано {len(seminars)} семинаров, новых: {new_count}")

    if new_count > 0:
        print(f"[бухэксперт] ✅ Обнаружено {new_count} новых семинаров")
    else:
        print("[бухэксперт] Новых семинаров нет — БД актуальна")
    return 0


if __name__ == "__main__":
    sys.exit(main())