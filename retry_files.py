#!/usr/bin/env python3
"""
retry_files.py — цикл повторов для файлов «Новое в версии».

Запускается из run_daily.py в фоне, когда сервис files.releases.1c.ru
отдал заглушку «Ошибка на нашем сервере» и файл не скачался.

Логика: до 5 попыток с интервалом 1 час. Как только файл получен —
конвертируется в PDF и отправляется в Info_Bot (канал релизов).

Процесс самостоятельный (start_new_session), поэтому переживает завершение
run_daily; lock-файл в downloads/ не даёт запустить два цикла одновременно.
"""
import asyncio
import sys
from pathlib import Path
from uuid import UUID

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

BOT_ID = "d2863b44-7aee-5a07-bc4c-9a6098b5696e"
CHAT_ID = "5bf9bf2e-49eb-5099-85e4-1af65241a3b8"


async def send_files(files: list[Path]) -> None:
    """Отправляет полученные PDF-файлы в канал релизов Info_Bot."""
    if not files:
        return
    from bot import get_bot
    from pybotx import OutgoingAttachment

    b = get_bot()
    await b.startup()
    try:
        for fp in files:
            try:
                with open(fp, "rb") as f:
                    attachment = OutgoingAttachment(content=f.read(), filename=fp.name)
                await b.send_message(
                    bot_id=UUID(BOT_ID), chat_id=UUID(CHAT_ID),
                    body=f"📎 {fp.name}", file=attachment, wait_callback=False,
                )
                print(f"   ✅ Файл отправлен в чат: {fp.name}")
            except Exception as e:
                print(f"   ❌ Ошибка отправки {fp.name}: {e}")
    finally:
        await b.shutdown()


def main() -> int:
    from release_files import retry_loop, pending_items, MAX_ATTEMPTS

    pending = pending_items()
    if not pending:
        print("[retry] Очередь повторов пуста — выходим")
        return 0

    print(f"[retry] В очереди релизов: {len(pending)} — "
          f"до {MAX_ATTEMPTS} попыток с интервалом 1 час")

    total = retry_loop(on_files=lambda files: asyncio.run(send_files(files)))

    left = pending_items()
    print(f"[retry] Итог: получено файлов {total}, осталось в очереди {len(left)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
