"""
buhexpert_news.py — новости buhexpert8.ru (блок #nos-1) для канала «БухЭксперт Новости».

Источники:
  - AJAX wp-admin/admin-ajax.php (action=multibox_new, type=allLastPostsLive)
    → JSON [{id, datetime, date, permalink, title}] (свежие первыми).
    Даёт стабильный числовой id → надёжная дедупликация.
  - Страница новости: div.entry-content; текст обрезается по фразе
    «Если вы еще не подписаны:» (точное совпадение, первое вхождение).

Хранилище: SQLite data/buhexpert_news.db + авто-экспорт реестра
           output/buhexpert_news.csv (без текста расшифровки).
"""
import csv
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from html_pdf import html_to_pdf as _html_to_pdf, HTML_TO_PDF_AVAILABLE as _PDF_OK

DATA_DIR = HERE / "data"
OUT_DIR = HERE / "output"
DB_PATH = DATA_DIR / "buhexpert_news.db"
CSV_PATH = OUT_DIR / "buhexpert_news.csv"

BASE_URL = "https://buhexpert8.ru"
AJAX_URL = f"{BASE_URL}/wp-admin/admin-ajax.php"
NEWS_ACTION = "multibox_new"
NEWS_TYPE = "allLastPostsLive"

# Конвертация HTML → PDF вынесена в общий модуль html_pdf.py (используется
# также релизным ботом Info_Bot). См. html_to_pdf ниже — тонкая обёртка.

# Фраза-маркер: текст расшифровки обрезается до неё (точное совпадение)
MARKER = "Если вы еще не подписаны:"
# Служебный хвост (шум) — обрезается, если основного маркера нет
TAIL_MARKER = "Подписывайтесь на наши каналы"

TIMEOUT = 30
MAX_PAGES = 6  # предохранитель пагинации (на страницу ~10 постов)


# ═══════════════════════════════════════════════════════════
#  сбор
# ═══════════════════════════════════════════════════════════

def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept-Language": "ru-RU,ru;q=0.9",
    })
    return s


def _fetch_page(session: requests.Session, last_datetime: str, last_id: str) -> list[dict]:
    """Один AJAX-запрос списка новостей. Возвращает список постов или []."""
    try:
        r = session.post(AJAX_URL, data={
            "action": NEWS_ACTION,
            "type": NEWS_TYPE,
            "last_datetime": last_datetime,
            "last_id": last_id,
        }, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"   ⚠ AJAX-ошибка: {type(e).__name__}: {e}")
        return []


def fetch_latest_news(session: Optional[requests.Session] = None,
                      max_posts: Optional[int] = None) -> list[dict]:
    """Возвращает свежие новости (свежие первыми) с числовым id."""
    session = session or _make_session()
    posts: list[dict] = []
    seen_ids: set[int] = set()
    # «Будущая» дата/ID → сервер отдаёт самые свежие посты
    last_dt, last_id = "2030-01-01 00:00:00", "99999999"

    for _ in range(MAX_PAGES):
        batch = _fetch_page(session, last_dt, last_id)
        if not batch:
            break
        added = 0
        for p in batch:
            pid = p.get("id")
            if pid is None or pid in seen_ids:
                continue
            seen_ids.add(int(pid))
            posts.append({
                "id": int(pid),
                "datetime": (p.get("datetime") or "").strip(),
                "date": (p.get("date") or "").strip(),
                "title": (p.get("title") or "").strip(),
                "url": (p.get("permalink") or "").strip(),
            })
            added += 1
        if max_posts and len(posts) >= max_posts:
            break
        if added == 0:
            break
        last = batch[-1]
        last_dt = last.get("datetime") or last_dt
        last_id = str(last.get("id") or last_id)

    if max_posts:
        posts = posts[:max_posts]
    return posts


def _extract_block_text(root) -> str:
    """Текст блока с переносами: абзац/заголовок/пункт/строка — с новой строки."""
    lines = []
    for el in root.find_all(["h1", "h2", "h3", "h4", "h5", "p", "li", "blockquote", "tr"]):
        t = el.get_text(" ", strip=True)
        if t:
            lines.append(t)
    if not lines:
        return root.get_text("\n", strip=True)
    return "\n".join(lines)


def fetch_news_html(session: requests.Session, url: str) -> tuple[str, bool]:
    """
    Сырой HTML содержимого новости для файла-обзора.

    Селекторы (по приоритету):
      1. div.entry-content — обычные новости/статьи (основной случай);
      2. .container.main-content-area — записи прямых эфиров и семинаров:
         у них НЕТ entry-content, но есть этот контейнер (лектор, дата,
         программа, отзывы; без футера).

    Обрезка:
      1. по фразе MARKER «Если вы еще не подписаны:» — если найдена;
      2. иначе по служебному хвосту TAIL_MARKER.
    Возвращает (html, marker_found). Если контента нет вовсе — ("", False).
    """
    try:
        r = session.get(url, timeout=TIMEOUT)
        r.raise_for_status()
    except Exception as e:
        print(f"   ⚠ Ошибка загрузки новости {url}: {type(e).__name__}: {e}")
        return "", False

    soup = BeautifulSoup(r.text, "html.parser")
    # 1) обычные новости
    node = soup.find(class_="entry-content")
    if node is None:
        # 2) записи эфиров/семинаров — контейнер страницы события
        node = soup.select_one(".container.main-content-area")
    if node is None:
        return "", False

    # убираем служебные вставки, чтобы PDF был чистым
    for junk in node.find_all(["script", "style", "noscript"]):
        junk.decompose()

    html = str(node)
    marker_found = False
    idx = html.find(MARKER)
    if idx >= 0:
        html = html[:idx]
        marker_found = True
    else:
        idx2 = html.find(TAIL_MARKER)
        if idx2 >= 0:
            html = html[:idx2]
    # закрываем возможные незакрытые теги
    return html.strip(), marker_found


def _row_get(row, key: str, default: str = "") -> str:
    """Безопасно достаёт значение из sqlite3.Row (или dict)."""
    try:
        val = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return val if val is not None else default


def build_news_html(row, inner_html: str, marker_found: bool) -> str:
    """Оборачивает содержимое новости в автономный HTML-документ (для вложения)."""
    title = _row_get(row, "title", "Новость")
    date = _row_get(row, "date", "")
    url = _row_get(row, "url", "")
    note = ("Показана публичная часть (далее требуется подписка)"
            if marker_found else "Полный текст новости")
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  body {{ font-family: -apple-system, 'Segoe UI', Roboto, Arial, sans-serif;
         max-width: 820px; margin: 24px auto; padding: 0 18px; color: #17212b;
         line-height: 1.6; font-size: 16px; }}
  h1 {{ font-size: 22px; margin: 0 0 6px; }}
  .meta {{ color: #5b6b78; font-size: 14px; margin-bottom: 4px; }}
  .meta a {{ color: #108f76; }}
  .note {{ background: #f4f9f7; border-left: 3px solid #108f76;
           padding: 8px 12px; font-size: 13px; color: #3d5a54; margin: 14px 0; }}
  img {{ max-width: 100%; height: auto; }}
  table {{ border-collapse: collapse; width: 100%; }}
  td, th {{ border: 1px solid #dbe3e8; padding: 6px 10px; }}
  a {{ color: #108f76; }}
</style>
</head>
<body>
<h1>{title}</h1>
<div class="meta">📅 {date} &nbsp;·&nbsp; Источник: <a href="{url}">buhexpert8.ru</a></div>
<div class="note">{note}</div>
{inner_html}
</body>
</html>"""


def html_to_pdf(html_doc: str) -> Optional[bytes]:
    """Конвертирует HTML-документ в PDF (Chrome headless). См. html_pdf.py."""
    return _html_to_pdf(html_doc)


def fetch_news_text(session: requests.Session, url: str) -> tuple[str, bool]:
    """
    Текст новости из div.entry-content.

    Обрезка:
      1. по фразе MARKER «Если вы еще не подписаны:» — если найдена;
      2. иначе по служебному хвосту TAIL_MARKER (шум «Подписывайтесь на наши каналы»).

    Возвращает (текст, marker_found), где marker_found=True, если сработал
    основной MARKER (страница была за пейволлом).
    """
    try:
        r = session.get(url, timeout=TIMEOUT)
        r.raise_for_status()
    except Exception as e:
        print(f"   ⚠ Ошибка загрузки новости {url}: {type(e).__name__}: {e}")
        return "", False

    soup = BeautifulSoup(r.text, "html.parser")
    ec = soup.find(class_="entry-content")
    if ec is None:
        return "", False

    txt = _extract_block_text(ec)
    marker_found = False
    idx = txt.find(MARKER)
    if idx >= 0:
        txt = txt[:idx]
        marker_found = True
    else:
        # публичная новость: убираем только служебный хвост
        idx2 = txt.find(TAIL_MARKER)
        if idx2 >= 0:
            txt = txt[:idx2]
    return txt.strip(), marker_found


# ═══════════════════════════════════════════════════════════
#  хранилище
# ═══════════════════════════════════════════════════════════

def init_db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS news (
            id             INTEGER PRIMARY KEY,   -- id поста на сайте
            datetime       TEXT,
            date           TEXT,
            title          TEXT,
            url            TEXT,
            first_seen     TEXT,
            sent_at        TEXT,
            digest_text    TEXT,
            digest_sent_at TEXT,
            note           TEXT
        )
    """)
    conn.commit()
    return conn


def known_ids(conn: sqlite3.Connection) -> set[int]:
    return {row[0] for row in conn.execute("SELECT id FROM news")}


def get_news(conn: sqlite3.Connection, news_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM news WHERE id=?", (news_id,)).fetchone()


def save_news(conn: sqlite3.Connection, post: dict) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO news (id, datetime, date, title, url, first_seen)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (post["id"], post["datetime"], post["date"], post["title"], post["url"],
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit()


def unsent_news(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Новости, чьё уведомление ещё не отправлено (персистентный retry)."""
    return list(conn.execute(
        "SELECT * FROM news WHERE sent_at IS NULL OR sent_at='' ORDER BY id"
    ))


def unsent_digests(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Новости с отправленным уведомлением, но неотправленным обзором.

    Записи с note='no-content' исключены: на странице нет пригодного контента
    (например, видеозапись эфира без текста) — повторять сбор бессмысленно.
    """
    return list(conn.execute(
        "SELECT * FROM news WHERE sent_at IS NOT NULL AND sent_at<>'' "
        "AND (digest_sent_at IS NULL OR digest_sent_at='') "
        "AND (note IS NULL OR note<>'no-content') ORDER BY id"
    ))


def mark_no_content(conn: sqlite3.Connection, news_id: int) -> None:
    """Помечает новость как «контента для обзора нет» — исключает из повторных попыток."""
    conn.execute("UPDATE news SET note='no-content' WHERE id=?", (news_id,))
    conn.commit()


def save_digest(conn: sqlite3.Connection, news_id: int, text: str, note: str = "") -> None:
    conn.execute("UPDATE news SET digest_text=?, note=? WHERE id=?", (text, note, news_id))
    conn.commit()


def mark_sent(conn: sqlite3.Connection, news_id: int) -> None:
    conn.execute("UPDATE news SET sent_at=? WHERE id=?",
                 (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), news_id))
    conn.commit()


def mark_digest_sent(conn: sqlite3.Connection, news_id: int) -> None:
    conn.execute("UPDATE news SET digest_sent_at=? WHERE id=?",
                 (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), news_id))
    conn.commit()


def export_csv(conn: sqlite3.Connection) -> Path:
    """Экспорт реестра новостей в CSV (без текста расшифровки)."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = list(conn.execute("""
        SELECT id, datetime, date, title, url, first_seen, sent_at, digest_sent_at,
               LENGTH(COALESCE(digest_text,'')) AS digest_len, COALESCE(note,'') AS note
        FROM news ORDER BY id DESC
    """))
    with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["id", "datetime", "date", "title", "url",
                    "first_seen", "sent_at", "digest_sent_at", "digest_len", "note"])
        for r in rows:
            w.writerow([r["id"], r["datetime"], r["date"], r["title"], r["url"],
                        r["first_seen"], r["sent_at"] or "", r["digest_sent_at"] or "",
                        r["digest_len"], r["note"]])
    return CSV_PATH
