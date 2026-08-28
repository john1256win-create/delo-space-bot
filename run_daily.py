#!/usr/bin/env python3
"""Ежедневный прогон: ИТС -> CSV -> diff -> отправка в Delo Space Info_Bot."""
import sys, asyncio, logging, time
from uuid import UUID

sys.path.insert(0, "/Users/salnikov/ТЗ_Проекты/_Инструменты/1c_releases/delo_space_bot")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

BOT_ID = "d2863b44-7aee-5a07-bc4c-9a6098b5696e"
CHAT_ID = "5bf9bf2e-49eb-5099-85e4-1af65241a3b8"

async def main():
    from scraper import run_scrape
    from formatter import format_changes
    from bot import get_bot

    MAX_RETRIES = 3
    RETRY_DELAY = 300  # 5 минут

    t0 = time.time()
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 🔍 Запускаю полный цикл скрапинга...")

    all_rows, new_rows, removed_rows = None, None, None
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            all_rows, new_rows, removed_rows = await asyncio.to_thread(run_scrape)
            break  # Успех — выходим из цикла
        except Exception as e:
            last_error = e
            if attempt < MAX_RETRIES:
                print(f"⚠️ Попытка {attempt}/{MAX_RETRIES} не удалась: {e}")
                print(f"   Жду {RETRY_DELAY // 60} минут перед повтором...")
                await asyncio.sleep(RETRY_DELAY)
            else:
                print(f"❌ Все {MAX_RETRIES} попытки не удались")

    if all_rows is None:
        error_msg = f"❌ Ошибка скрапинга после {MAX_RETRIES} попыток: {last_error}"
        print(error_msg)
        # Отправляем ошибку в чат
        try:
            b = get_bot()
            await b.startup()
            await b.send_message(
                bot_id=UUID(BOT_ID),
                chat_id=UUID(CHAT_ID),
                body=error_msg,
                wait_callback=False
            )
            await b.shutdown()
        except Exception as send_err:
            print(f"Не удалось отправить ошибку в чат: {send_err}")
        return 1

    assert new_rows is not None and removed_rows is not None
    print(f"   Скрапинг занял {time.time()-t0:.1f}s")
    print(f"   Всего строк: {len(all_rows)} | Новых: {len(new_rows)} | Удалено: {len(removed_rows)}")

    if not all_rows:
        print("❌ Нет данных — прерываю")
        return 1

    # Отправляем в чат только если есть изменения
    if not new_rows and not removed_rows:
        print("✅ Изменений нет — не отправляю в чат")
        return 0

    # Формируем сообщение
    text = format_changes(new_rows, removed_rows)
    body = text

    # Отправка в чат (дробление по 4000 символов)
    b = get_bot()
    await b.startup()
    try:
        chunk = body
        size = 4000
        sent = 0
        while chunk:
            part = chunk[:size]
            if len(chunk) > size:
                cut = part.rfind("\n")
                if cut > 0:
                    part, chunk = chunk[:cut], chunk[cut:].lstrip()
                else:
                    chunk = chunk[size:].lstrip()
            else:
                chunk = ""
            await b.send_message(bot_id=UUID(BOT_ID), chat_id=UUID(CHAT_ID),
                                 body=part, wait_callback=False)
            sent += 1
        print(f"✅ Отправлено в чат {CHAT_ID} частей: {sent}")
    finally:
        await b.shutdown()
    return 0

rc = asyncio.run(main())
print(f">>> exit code {rc}")
sys.exit(rc)