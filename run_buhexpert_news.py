#!/usr/bin/env python3
"""
run_buhexpert_news.py — новости buhexpert8.ru → канал «БухЭксперт Новости» (79d09ddd-...).

Формат отправки (по требованию):
  1. Однострочное сообщение на каждую новую новость:
       📰 <Заголовок> — <ссылка>
     Отправляется ТОЛЬКО когда новость появилась (дедуп по id поста).
  2. Обзор новости — прикреплённым PDF-файлом (содержимое div.entry-content,
     обрезанное по фразе «Если вы еще не подписаны:» либо по служебному хвосту).
     HTML → PDF конвертируется Chrome headless: встроенный просмотрщик Delo Space
     открывает PDF нативно, тогда как HTML пришлось бы скачивать.

Логика запуска (launchd, каждые 2 часа 8:45–20:45):
  1. Доотправка «хвостов»: записи с пустым sent_at / digest_sent_at.
  2. Сбор свежих новостей (AJAX, JSON с id) → новые = чьего id нет в БД.
  3. Для каждой новой: запись в БД → однострочное уведомление → PDF-обзор.
     При сбое отправки — повтор каждые 5 минут, до RETRY_ATTEMPTS раз.
  4. Экспорт CSV-реестра.

Если отправка не прошла — запись остаётся с пустым sent_at/digest_sent_at,
и следующий запуск доотправит (персистентный retry).

Тестовый режим:
  python run_buhexpert_news.py --replay-date 2026-09-11 [--interval 60]
    — отправляет новости указанной даты (из БД) с интервалом N секунд,
      имитируя их последовательное появление.
"""
import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent))

import buhexpert_news as bn
import buhexpert_scraper as bs
from buhexpert_send import send_message

# Канал «БухЭксперт Новости»
CHAT_NEWS = UUID("79d09ddd-867e-50ab-996f-0cd39068d15b")

RETRY_ATTEMPTS = 12      # 12 попыток × 5 мин = 1 час максимум в одном запуске
RETRY_DELAY = 300        # 5 минут
FIRST_RUN_POSTS = 10     # при первом запуске (пустая БД) — 10 последних новостей
REGULAR_POSTS = 20       # в обычных запусках — с запасом


def log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] [бухэксперт:news] {msg}")


def fmt_news_line(row) -> str:
    """Однострочное уведомление о новости: заголовок + ссылка."""
    return f"📰 {row['title']} — {row['url']}"


async def send_with_retry(body: str, bot, label: str, chat_id: UUID = CHAT_NEWS) -> bool:
    """Отправляет сообщение, повторяя каждые RETRY_DELAY секунд до успеха."""
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            await send_message(body, bot=bot, chat_id=chat_id, retries=1)
            return True
        except Exception as e:
            if attempt < RETRY_ATTEMPTS:
                log(f"   ⚠ {label}: сбой (попытка {attempt}/{RETRY_ATTEMPTS}): "
                    f"{type(e).__name__}: {e}. Повтор через {RETRY_DELAY // 60} мин...")
                await asyncio.sleep(RETRY_DELAY)
            else:
                log(f"   ❌ {label}: не удалось за {RETRY_ATTEMPTS} попыток — "
                    f"оставляю в отложенных (следующий запуск доотправит)")
    return False


async def send_pdf_file(row, pdf_bytes: bytes, bot, chat_id: UUID = CHAT_NEWS) -> bool:
    """Отправляет обзор новости прикреплённым PDF-файлом (с повторами)."""
    from pybotx import OutgoingAttachment
    from buhexpert_send import BOT_ID

    filename = f"buhexpert_news_{row['id']}.pdf"
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            att = OutgoingAttachment(content=pdf_bytes, filename=filename)
            await bot.send_message(bot_id=UUID(BOT_ID), chat_id=chat_id,
                                   body=f"📄 Обзор: {row['title']}",
                                   file=att, wait_callback=False)
            return True
        except Exception as e:
            if attempt < RETRY_ATTEMPTS:
                log(f"   ⚠ обзор(pdf) {row['id']}: сбой {attempt}/{RETRY_ATTEMPTS}: "
                    f"{type(e).__name__}: {e}")
                await asyncio.sleep(RETRY_DELAY)
            else:
                log(f"   ❌ обзор(pdf) {row['id']}: не удалось — в отложенные")
    return False


async def process_one(conn, session, row, bot, chat_id: UUID = CHAT_NEWS) -> None:
    """Отправляет одну новость: сначала уведомление, затем её PDF-обзор.

    Порядок внутри новости строгий: новость → файл. Именно так, чтобы в чате
    пары «новость + вложение» шли подряд, а не все новости, потом все файлы.
    """
    # 1) Уведомление (если ещё не отправлено)
    if not (row["sent_at"] or "").strip():
        log(f"📤 Уведомление: {row['title'][:50]}")
        if await send_with_retry(fmt_news_line(row), bot, f"уведомление {row['id']}", chat_id):
            bn.mark_sent(conn, row["id"])
            log(f"   ✅ Уведомление отправлено (id={row['id']})")
        else:
            # без уведомления файл слать не имеет смысла — повторим в следующий запуск
            return

    # 2) PDF-обзор этой же новости
    cur = bn.get_news(conn, row["id"])
    pdf_bytes = (cur["digest_text"] or "").strip() if cur else ""
    if pdf_bytes.startswith("PDF:"):
        # уже сконвертирован и сохранён (файл на диске)
        p = Path(pdf_bytes[4:])
        pdf_bytes = p.read_bytes() if p.exists() else ""
    else:
        # собрать HTML и сконвертировать в PDF
        inner, marker = bn.fetch_news_html(session, row["url"])
        if not inner:
            # контента на странице нет (например, только видеозапись эфира) —
            # помечаем, чтобы не пытаться снова при каждом прогоне
            bn.mark_no_content(conn, row["id"])
            log(f"   ⚠ Обзор пуст (id={row['id']}) — помечено no-content, повтор не потребуется")
            return
        html_doc = bn.build_news_html(row, inner, marker)
        pdf_bytes = bn.html_to_pdf(html_doc) or ""
        if not pdf_bytes:
            log(f"   ⚠ PDF не собран (id={row['id']}) — пропускаю")
            return
        bn.save_digest(conn, row["id"], f"PDF:{len(pdf_bytes)}",
                       "paywall" if marker else "public")
        log(f"📄 PDF собран (id={row['id']}, {len(pdf_bytes)} байт, "
            f"{'paywall' if marker else 'public'})")

    if pdf_bytes and await send_pdf_file(row, pdf_bytes, bot, chat_id):
        bn.mark_digest_sent(conn, row["id"])
        log(f"   ✅ PDF-обзор отправлен (id={row['id']})")


async def process_news(conn, session, bot, chat_id: UUID = CHAT_NEWS) -> None:
    """Отправляет неотправленные новости в порядке: новость → её файл → следующая.

    Персистентный retry: сначала хвосты прошлых запусков, затем новые.
    """
    for row in bn.pending_news(conn):
        await process_one(conn, session, row, bot, chat_id)


async def main_replay(target_date: str, interval: int) -> int:
    """Тестовый режим: отправить новости указанной даты с интервалом N секунд."""
    conn = bn.init_db()
    session = bn._make_session()
    # отбор по ISO-дате: колонка datetime вида '2026-09-11 09:46:00'
    rows = list(conn.execute(
        "SELECT * FROM news WHERE datetime LIKE ? ORDER BY datetime, id",
        (f"{target_date}%",)
    ))
    log(f"🔁 REPLAY {target_date}: новостей {len(rows)}, интервал {interval}s")

    if not rows:
        log("   нет новостей за указанную дату")
        conn.close()
        return 0

    if not bs.login(session):
        log("   ⚠ не удалось авторизоваться — обзоры будут неполными")

    from bot import get_bot
    b = get_bot()
    await b.startup()
    try:
        for i, row in enumerate(rows):
            if i:
                await asyncio.sleep(interval)
            # однострочное уведомление
            log(f"📤 [{i+1}/{len(rows)}] {row['title'][:50]}")
            await send_with_retry(fmt_news_line(row), b, f"уведомление {row['id']}")
            # PDF-обзор
            inner, marker = bn.fetch_news_html(session, row["url"])
            if inner:
                html_doc = bn.build_news_html(row, inner, marker)
                pdf_bytes = bn.html_to_pdf(html_doc)
                if pdf_bytes:
                    await send_pdf_file(row, pdf_bytes, b)
                    log(f"   ✅ Уведомление + PDF-обзор отправлены (id={row['id']})")
                else:
                    log(f"   ⚠ PDF не собран (id={row['id']})")
            else:
                log(f"   ⚠ Обзор не собран (id={row['id']})")
    finally:
        await b.shutdown()
    conn.close()
    log("🔁 REPLAY завершён")
    return 0


async def main() -> int:
    conn = bn.init_db()
    session = bn._make_session()

    pending = bn.unsent_news(conn)
    if pending:
        log(f"🔁 Неотправленных уведомлений: {len(pending)}")

    # ── Сбор новостей ──────────────────────────────────────
    known = bn.known_ids(conn)
    first_run = len(known) == 0
    limit = FIRST_RUN_POSTS if first_run else REGULAR_POSTS
    posts = bn.fetch_latest_news(session, max_posts=limit)
    log(f"🔍 Свежих новостей на сайте: {len(posts)} (лимит {limit}"
        f"{', первый запуск' if first_run else ''}), уже известно: {len(known)}")

    new_posts = [p for p in posts if p["id"] not in known]
    new_posts.sort(key=lambda p: p["id"])  # хронологически
    if new_posts:
        log(f"   🆕 Новых: {len(new_posts)}")
    for p in new_posts:
        bn.save_news(conn, p)
    if new_posts or pending:
        bn.export_csv(conn)

    # ── Авторизация для обзоров ────────────────────────────
    if bn.unsent_news(conn) or bn.unsent_digests(conn):
        if not bs.login(session):
            log("   ⚠ Не удалось авторизоваться на buhexpert8.ru — обзоры недоступны")

    from bot import get_bot
    b = get_bot()
    await b.startup()
    try:
        await process_news(conn, session, b)
    finally:
        await b.shutdown()

    bn.export_csv(conn)
    log(f"📊 Итог: всего={conn.execute('SELECT COUNT(*) FROM news').fetchone()[0]}, "
        f"без уведомления={len(bn.unsent_news(conn))}, "
        f"без обзора={len(bn.unsent_digests(conn))}")
    conn.close()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay-date", help="тест: отправить новости даты (ISO, напр. 2026-09-11)")
    ap.add_argument("--interval", type=int, default=60, help="интервал между новостями, сек (replay)")
    args = ap.parse_args()

    if args.replay_date:
        sys.exit(asyncio.run(main_replay(args.replay_date, args.interval)))
    sys.exit(asyncio.run(main()))
