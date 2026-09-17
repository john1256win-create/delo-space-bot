#!/usr/bin/env python3
"""
retry_files.py — попытка получить файл «Новое в версии» из очереди повторов.

Запускается по расписанию launchd-агента com.salnikov.1c-release-retry —
раз в час, с 00:05 до 23:05 (до 5 попыток на релиз, счётчик живёт
в downloads/pending_files.json).

Появление очереди означает, что сервис files.releases.1c.ru отдал заглушку
«Ошибка на нашем сервере» и файл не скачался. Как только файл получен —
конвертируется в PDF и отправляется в Info_Bot (канал релизов).

Процесс короткоживущий (одна попытка, без sleep): долгоживущий цикл на Mac
может умереть при перезагрузке или сне, а счётчик попыток должен сохраняться.
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
    from release_files import run_once, pending_items, MAX_ATTEMPTS

    pending = pending_items()
    if not pending:
        print("[retry] Очередь пуста — выходим")
        return 0

    done = max(int(i.get("attempts", 1)) for i in pending)
    print(f"[retry] В очереди релизов: {len(pending)} — попыток сделано {done}/{MAX_ATTEMPTS}")

    total = run_once(on_files=lambda files: asyncio.run(send_files(files)))

    left = pending_items()
    print(f"[retry] Итог: получено файлов {total}, осталось в очереди {len(left)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
