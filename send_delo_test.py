#!/usr/bin/env python3
"""End-to-end тест: Delo Space отправка с гиперссылками + файл «Новое в версии»."""
import sys, asyncio
from uuid import UUID
sys.path.insert(0, "/Users/salnikov/ТЗ_Проекты/_Инструменты/1c_releases/delo_space_bot")

from bot import get_bot
from formatter import format_changes_links
from release_files import download_all_news_files
from pybotx import OutgoingAttachment

BOT_ID = "d2863b44-7aee-5a07-bc4c-9a6098b5696e"
CHAT_ID = "5bf9bf2e-49eb-5099-85e4-1af65241a3b8"  # Info_Bot (основной канал релизов)

new_rows = [
    {"url": "https://releases.1c.ru/project/HRMCorp30", "version": "3.1.38.92ДП",
     "product": "Зарплата и управление персоналом КОРП, редакция 3", "title": "Актуальная", "date": "2026-09-02"},
]
removed_rows = []


async def main():
    text = format_changes_links(new_rows, removed_rows)
    print("=== Сообщение ===")
    print(text)
    print("=== Скачивание файла «Новое в версии» ===")
    files = download_all_news_files(new_rows)
    print(f"Скачано файлов: {len(files)}")

    b = get_bot()
    await b.startup()
    try:
        # Отправляем текст с гиперссылками
        await b.send_message(bot_id=UUID(BOT_ID), chat_id=UUID(CHAT_ID),
                             body=text, wait_callback=False)
        print("✅ Текст с гиперссылками отправлен в Info_Bot")

        # Прикрепляем файл
        for fp in files:
            with open(fp, "rb") as f:
                attachment = OutgoingAttachment(content=f.read(), filename=fp.name)
            await b.send_message(
                bot_id=UUID(BOT_ID), chat_id=UUID(CHAT_ID),
                body=f"📎 {fp.name}", file=attachment, wait_callback=False,
            )
            print(f"✅ Файл отправлен: {fp.name}")
    finally:
        await b.shutdown()


asyncio.run(main())