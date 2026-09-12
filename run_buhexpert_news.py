#!/usr/bin/env python3
"""
run_buhexpert_news.py — новости buhexpert8.ru → канал «БухЭксперт Новости» (79d09ddd-...).

Логика одного запуска (launchd, каждые 2 часа 8:45–20:45):
  1. Доотправка «хвостов»: записи с пустым sent_at / digest_sent_at
     (мог упасть прошлый запуск, Mac был выключен) — персистентный retry.
  2. Сбор свежих новостей (AJAX, JSON с id) → новые = чьего id нет в БД.
  3. Для каждой новой:
     a. уведомление (заголовок + дата + ссылка) → при сбое повтор каждые 5 мин;
     b. текст div.entry-content (обрезка по «Если вы еще не подписаны:» либо
        по служебному хвосту) → сохранение в БД → отправка расшифровки
        → при сбое повтор каждые 5 мин.
  4. Экспорт CSV-реестра.

Если отправка не прошла за RETRY_ATTEMPTS попыток — запись остаётся с пустым
sent_at/digest_sent_at, и следующий запуск (через 2 часа) доотправит.
"""
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
MAX_DIGEST_CHARS = 20000  # больше — отправляем .txt файлом, а не сообщением
FIRST_RUN_POSTS = 10     # при первом запуске (пустая БД) — 10 последних новостей
REGULAR_POSTS = 20       # в обычных запусках — с запасом


def log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] [бухэксперт:news] {msg}")


def fmt_news_message(row) -> str:
    """Уведомление о новости: заголовок + дата + ссылка."""
    return (
        f"📰 Новость на buhexpert8.ru\n"
        f"**{row['title']}**\n"
        f"📅 {row['date']}\n"
        f"🔗 {row['url']}"
    )


def fmt_digest_message(row, text: str) -> str:
    """Расшифровка новости с заголовком в начале."""
    return f"📄 **{row['title']}** — расшифровка\n\n{text}"


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


async def send_digest_with_retry(row, text: str, bot, chat_id: UUID = CHAT_NEWS) -> bool:
    """Отправляет расшифровку. Длинный текст (> MAX_DIGEST_CHARS) — файлом .txt."""
    from pybotx import OutgoingAttachment
    from buhexpert_send import BOT_ID

    if len(text) <= MAX_DIGEST_CHARS:
        return await send_with_retry(fmt_digest_message(row, text), bot, f"расшифровка {row['id']}",
                                     chat_id=chat_id)

    # Длинный текст — отправляем .txt файлом
    body = fmt_digest_message(row, f"({len(text)} символов — во вложении текстовым файлом)")
    filename = f"buhexpert_news_{row['id']}.txt"
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            att = OutgoingAttachment(content=text.encode("utf-8"), filename=filename)
            await bot.send_message(bot_id=UUID(BOT_ID), chat_id=chat_id,
                                   body=body, wait_callback=False)
            await bot.send_message(bot_id=UUID(BOT_ID), chat_id=chat_id,
                                   body=f"📎 {filename}", file=att, wait_callback=False)
            return True
        except Exception as e:
            if attempt < RETRY_ATTEMPTS:
                log(f"   ⚠ расшифровка(файл) {row['id']}: сбой {attempt}/{RETRY_ATTEMPTS}: {e}")
                await asyncio.sleep(RETRY_DELAY)
            else:
                log(f"   ❌ расшифровка(файл) {row['id']}: не удалось — в отложенные")
    return False


async def main() -> int:
    conn = bn.init_db()
    session = bn._make_session()

    # ── 1. Доотправка уведомлений ──────────────────────────
    pending = bn.unsent_news(conn)
    if pending:
        log(f"🔁 Неотправленных уведомлений: {len(pending)}")

    # ── 2. Сбор новостей ───────────────────────────────────
    known = bn.known_ids(conn)
    first_run = len(known) == 0
    limit = FIRST_RUN_POSTS if first_run else REGULAR_POSTS
    posts = bn.fetch_latest_news(session, max_posts=limit)
    log(f"🔍 Свежих новостей на сайте: {len(posts)} (лимит {limit}"
        f"{', первый запуск' if first_run else ''}), уже известно: {len(known)}")

    new_posts = [p for p in posts if p["id"] not in known]
    # Сначала старые (по возрастанию id), чтобы канал читался хронологически
    new_posts.sort(key=lambda p: p["id"])
    if new_posts:
        log(f"   🆕 Новых: {len(new_posts)}")
    for p in new_posts:
        bn.save_news(conn, p)
    if new_posts or pending:
        bn.export_csv(conn)

    # ── 3. Авторизация для текстов новостей ───────────────—
    if bn.unsent_news(conn):
        if not bs.login(session):
            log("   ⚠ Не удалось авторизоваться на buhexpert8.ru — тексты недоступны")

    from bot import get_bot
    b = get_bot()
    await b.startup()
    try:
        # ── 3a. Уведомления ────────────────────────────────
        for row in bn.unsent_news(conn):
            log(f"📤 Уведомление: {row['title'][:50]}")
            if await send_with_retry(fmt_news_message(row), b, f"уведомление {row['id']}"):
                bn.mark_sent(conn, row["id"])
                log(f"   ✅ Уведомление отправлено (id={row['id']})")

        # ── 3b. Расшифровки ────────────────────────────────
        for row in bn.unsent_digests(conn):
            cur = bn.get_news(conn, row["id"])
            text = (cur["digest_text"] or "").strip() if cur else ""
            if not text:
                text, marker = bn.fetch_news_text(session, row["url"])
                note = "paywall" if marker else "public"
                if not text:
                    log(f"   ⚠ Расшифровка пуста (id={row['id']}) — пропускаю")
                    continue
                bn.save_digest(conn, row["id"], text, note)
                log(f"📄 Расшифровка получена (id={row['id']}, {len(text)} симв, {note})")
            if await send_digest_with_retry(row, text, b):
                bn.mark_digest_sent(conn, row["id"])
                log(f"   ✅ Расшифровка отправлена (id={row['id']})")
    finally:
        await b.shutdown()

    bn.export_csv(conn)
    log(f"📊 Итог: всего={conn.execute('SELECT COUNT(*) FROM news').fetchone()[0]}, "
        f"без уведомления={len(bn.unsent_news(conn))}, "
        f"без расшифровки={len(bn.unsent_digests(conn))}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
