#!/usr/bin/env python3
"""Ежедневный прогон: ИТС -> CSV -> diff -> отправка в Delo Space Info_Bot.

Отдельно от основного мониторинга запускается цикл повторов для файлов
«Новое в версии» (retry_files.py): если сервис releases.1c.ru отдал ошибку,
он делает до 5 попыток с интервалом 1 час — независимо от расписания launchd.
"""
import sys, asyncio, logging, subprocess, time
from pathlib import Path
from uuid import UUID

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

BOT_ID = "d2863b44-7aee-5a07-bc4c-9a6098b5696e"
CHAT_ID = "5bf9bf2e-49eb-5099-85e4-1af65241a3b8"


def start_retry_worker() -> None:
    """Запускает фоновый цикл повторов «Новое в версии» (отдельным процессом).

    Нужен, когда файл не скачался из-за ошибки сервиса releases.1c.ru:
    retry_files.py повторит попытку через час (до 5 раз), не дожидаясь
    следующего запуска launchd (каждые 2 часа).
    """
    from release_files import pending_items
    if not pending_items():
        return
    log = HERE / "downloads" / "retry_files.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.Popen(
            [sys.executable, str(HERE / "retry_files.py")],
            stdout=open(log, "a"), stderr=subprocess.STDOUT,
            start_new_session=True,          # переживает завершение run_daily
        )
        print(f"   🔁 Запущен фоновый цикл повторов «Новое в версии» "
              f"(до 5 попыток, интервал 1 час), лог: {log}")
    except Exception as e:
        print(f"   ⚠ Не удалось запустить цикл повторов: {e}")


async def main():
    from scraper import run_scrape
    from formatter import format_changes
    from bot import get_bot
    from config import ENABLE_LAWMONITOR
    
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
    else:
        # Отправка в Delo Space: гиперссылки + файлы «Новое в версии»
        from formatter import format_changes_links
        from release_files import download_all_news_files
        from pybotx import OutgoingAttachment

        text = format_changes_links(new_rows, removed_rows)
        body = text

        b = get_bot()
        await b.startup()
        try:
            # 1) Скачиваем файлы «Новое в версии» для новых релизов
            files = download_all_news_files(new_rows) if new_rows else []

            # 2) Отправляем текстовое сообщение с гиперссылками (дробление по 4000)
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

            # 3) Прикрепляем файлы «Новое в версии» (PDF — открывается прямо в клиенте)
            for fp in files:
                try:
                    with open(fp, "rb") as f:
                        attachment = OutgoingAttachment(content=f.read(), filename=fp.name)
                    await b.send_message(
                        bot_id=UUID(BOT_ID),
                        chat_id=UUID(CHAT_ID),
                        body=f"📎 {fp.name}",
                        file=attachment,
                        wait_callback=False,
                    )
                    print(f"   ✅ Файл отправлен: {fp.name}")
                except Exception as fe:
                    print(f"   ❌ Ошибка отправки файла {fp.name}: {fe}")
        finally:
            await b.shutdown()
    
    # Если какие-то «Новое в версии» не получены из-за ошибки сервиса
    # releases.1c.ru — запускаем фоновый цикл повторов (5 попыток × 1 час).
    # Проверяем независимо от того, были ли изменения в этом прогоне.
    start_retry_worker()
    
    # Мониторинг v8.1c.ru/lawmonitor (временное решение)
    if ENABLE_LAWMONITOR:
        print("\n📜 Запускаю мониторинг v8.1c.ru/lawmonitor...")
        try:
            from scraper_lawmonitor import run_law_monitor_scrape
            from formatter import format_law_monitor_changes
            
            lm_all, lm_new, lm_removed = await asyncio.to_thread(run_law_monitor_scrape)
            print(f"   LawMonitor: всего={len(lm_all)} | новых={len(lm_new)} | удалено={len(lm_removed)}")
            
            if lm_new or lm_removed:
                lm_text = format_law_monitor_changes(lm_new, lm_removed)
                b = get_bot()
                await b.startup()
                try:
                    await b.send_message(
                        bot_id=UUID(BOT_ID),
                        chat_id=UUID(CHAT_ID),
                        body=lm_text,
                        wait_callback=False
                    )
                    print(f"✅ LawMonitor изменения отправлены в чат")
                finally:
                    await b.shutdown()
            else:
                print("✅ LawMonitor: изменений нет")
        except Exception as e:
            print(f"⚠️ Ошибка LawMonitor: {e}")
            # Не прерываем выполнение, просто логируем
    
    return 0


if __name__ == "__main__":
    rc = asyncio.run(main())
    print(f">>> exit code {rc}")
    sys.exit(rc)