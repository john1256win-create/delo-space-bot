"""
buhexpert_scraper.py — сбор семинаров с buhexpert8.ru.

Возможности:
- Парсинг полного списка будущих семинаров (#seminars-list2) с пагинацией.
- Авторизация (платная подписка) для доступа к полному тексту контента события.
- Извлечение контента события (div#seminar-program).
- Хранение семинаров в SQLite (data/buhexpert_seminars.db).

Структура данных семинара:
    {
        "date":      "2026-09-03",     # ISO
        "time":      "11:00",
        "title":     "Название семинара",
        "url":       "https://...",
        "lecturer":  "Имя лектора",
    }
"""
import os
import re
import sqlite3
from datetime import datetime, date
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "buhexpert_seminars.db"
COOKIE_FILE = BASE_DIR / "data" / "buhexpert_cookies.txt"

LIST_URL = "https://buhexpert8.ru/online?razdel="
LOGIN_URL = "https://buhexpert8.ru/login"
WP_LOGIN_URL = "https://buhexpert8.ru/wp-login.php"
AJAX_URL = "https://buhexpert8.ru/wp-admin/admin-ajax.php"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
}

MONTHS_RU = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}


def _env() -> dict:
    """Читает .env проекта без вывода значений."""
    d = {}
    env_path = BASE_DIR / ".env"
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            d[k] = v.strip()
    return d


def get_credentials() -> tuple:
    """Возвращает (login, password) для buhexpert8.ru."""
    env = _env()
    return env.get("BUXEXP_LOGIN", ""), env.get("BUXEXP_PASSWORD", "")


def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def _load_cookies(session: requests.Session) -> bool:
    if not COOKIE_FILE.exists():
        return False
    try:
        import pickle
        with open(COOKIE_FILE, "rb") as f:
            session.cookies.update(pickle.load(f))
        return True
    except Exception:
        return False


def _save_cookies(session: requests.Session):
    import pickle
    COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(COOKIE_FILE, "wb") as f:
        pickle.dump(session.cookies, f)


def login(session: requests.Session) -> bool:
    """
    Авторизация на buhexpert8.ru.
    Возвращает True, если удалось получить доступ к контенту.
    """
    username, password = get_credentials()
    if not username or not password:
        return False

    # 1. Пробуем восстановить сессию из cookies
    if _load_cookies(session):
        if _check_access(session):
            return True
        # cookies протухли — сбрасываем, чтобы не мешали свежему логину
        session.cookies.clear()
        if COOKIE_FILE.exists():
            COOKIE_FILE.unlink(missing_ok=True)

    # 2. Свежий логин
    try:
        r = session.get(LOGIN_URL, timeout=30)
        r.raise_for_status()
    except Exception:
        return False

    soup = BeautifulSoup(r.text, "html.parser")
    form = soup.find(id="login-popup-form")
    nonce = ""
    if form:
        inp = form.find("input", {"name": "nonce"})
        nonce = inp.get("value", "") if inp else ""

    if not nonce:
        # ищем любой nonce на странице
        m = re.search(r'name="nonce"\s+value="([^"]+)"', r.text)
        nonce = m.group(1) if m else ""

    if not nonce:
        return False

    data = {
        "log": username,
        "pwd": password,
        "wp-submit": "Войти",
        "redirect_to": "/",
        "rememberme": "forever",
        "nonce": nonce,
        "force_redirect": "/login",
    }
    try:
        r2 = session.post(WP_LOGIN_URL, data=data, timeout=30, allow_redirects=True)
        r2.raise_for_status()
    except Exception:
        return False

    _save_cookies(session)

    # Проверяем доступ
    return _check_access(session)


def _check_access(session: requests.Session) -> bool:
    """Проверяет доступ к контенту события (пробный запрос)."""
    probe = ("https://buhexpert8.ru/grafik-pryamyh-efirov/grafik-pryamyh-efirov-1s-zup/"
             "zavershaetsya-podderzhka-zup-3-1-34-o-chem-nuzhno-znat-pered-obnovleniem-na-zup-3-1-38.html")
    try:
        r = session.get(probe, timeout=30)
        soup = BeautifulSoup(r.text, "html.parser")
        main = soup.find(class_="main-content-inner")
        if not main:
            return False
        return "нет доступа" not in main.get_text(" ", strip=True).lower()
    except Exception:
        return False


def parse_date(day: str, month_year: str) -> Optional[str]:
    """'03' + 'сентября 2026' -> '2026-09-03'."""
    m = re.search(r"([а-яё]+)\s+(\d{4})", month_year.lower())
    if not m:
        return None
    mon = MONTHS_RU.get(m.group(1))
    if not mon:
        return None
    year = int(m.group(2))
    return f"{year:04d}-{mon:02d}-{int(day):02d}"


def parse_time(time_text: str) -> str:
    """'в 11:00 (МСК)' -> '11:00'."""
    m = re.search(r"(\d{1,2}):(\d{2})", time_text)
    if not m:
        return ""
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def parse_sem_item(item) -> dict:
    """Разбирает один .sem-item в словарь."""
    day_el = item.find(class_="sem_day")
    my_el = item.find(class_="sem_m-y")
    time_el = item.find(class_="sem_time")
    title_el = item.find(class_="sem-title")
    lect_el = item.find(class_="sem-lecturer-name")

    day = day_el.get_text(strip=True) if day_el else ""
    my = my_el.get_text(strip=True) if my_el else ""
    date_str = parse_date(day, my) or ""

    a = title_el.find("a") if title_el else None
    title = a.get_text(strip=True) if a else ""
    url = a["href"] if a and a.get("href") else ""

    lecturer = lect_el.get_text(" ", strip=True) if lect_el else ""
    lecturer = re.sub(r"^Лектор[ыа]?:", "", lecturer).strip()

    time_str = parse_time(time_el.get_text(strip=True)) if time_el else ""

    return {
        "date": date_str,
        "time": time_str,
        "title": title,
        "url": url,
        "lecturer": lecturer,
    }


def fetch_seminars(session: requests.Session = None) -> list[dict]:
    """Собирает все будущие семинары (с пагинацией). Возвращает список dict."""
    own = session is None
    s = session or _make_session()
    all_items = []

    r = s.get(LIST_URL, timeout=30)
    soup = BeautifulSoup(r.text, "html.parser")
    items = soup.find_all(class_="sem-item")
    all_items.extend(items)

    # Пагинация через AJAX
    page = 2
    while True:
        try:
            resp = s.get(
                "https://buhexpert8.ru/wp-admin/admin-ajax.php",
                params={"action": "seminars_future_more2", "razdel": "", "page": str(page)},
                timeout=30,
            )
        except Exception:
            break
        soup2 = BeautifulSoup(resp.text, "html.parser")
        items2 = soup2.find_all(class_="sem-item")
        if not items2:
            break
        all_items.extend(items2)
        more = soup2.find(class_="seminars-more")
        if not more:
            break
        page += 1

    result = [parse_sem_item(it) for it in all_items]
    result = [x for x in result if x and x["url"]]

    if own:
        s.close()
    return result


def fetch_event_content(session: requests.Session, url: str) -> str:
    """Извлекает полный текст контента события (div#seminar-program)."""
    try:
        r = session.get(url, timeout=30)
        soup = BeautifulSoup(r.text, "html.parser")
        main = soup.find(id="seminar-program") or soup.find(class_="seminar-program")
        if not main:
            return ""
        txt = _extract_block_text(main)
        # убираем служебные надписи подписки
        txt = re.sub(r"У вас нет доступа[^.]*", "", txt)
        txt = re.sub(r"Чтобы получить доступ[^.]*подписку", "", txt)
        # Обрезаем всё, начиная с блока комментариев
        for marker in ("Возможность задать вопрос", "Комментарии закрыты", "Добавить файлы"):
            idx = txt.find(marker)
            if idx > 0:
                txt = txt[:idx]
        txt = re.sub(r"\n{3,}", "\n\n", txt).strip()
        return txt
    except Exception:
        return ""


def _extract_block_text(root) -> str:
    """
    Извлекает текст из контента, сохраняя переносы строк.
    Каждый блочный элемент (заголовок, пункт списка, абзац) — с новой строки.
    Вложенные списки/пункты тоже дают свои строки (иерархия через дефисы).
    Пропускает script/style и служебные блоки (комментарии, навигация).
    """
    skip_tags = {"script", "style", "noscript"}
    # Строки, которые не являются контентом события
    noise_patterns = [
        r"^#\w+$",                       # #main, #comments, #primary
        r"^#?[\w-]*\s*row start",           # служебные маркеры темы (с #main или без)
        r"^\s*end\s*-+",
        r"^jQuery\(document\)",
        r"^document\.addEventListener",
        r"^Задайте свой вопрос",
        r"^Перед написанием вопроса",
        r"^Голосуйте с помощью",
        r"^Комментарии закрыты",
        r"^Добавить комментарий",
        r"^Обсуждение",
        r"^Войдите, чтобы оставить комментарий",
        r"^Возможность задать вопрос",
        r"^Добавить файлы",
        r"^Файлы не выбраны",
        r"^Допустимые расширения",
        r"^Чтобы выбрать несколько файлов",
        r"^Вложения будут видны",
        r"^Комментарий",
        r"^Максимальный размер файла",
        r"^\*$",                         # одиночная звёздочка (маркер формы)
        r"^У вас нет доступа",
        r"^Чтобы получить доступ",
    ]
    block_tags = {"h1", "h2", "h3", "h4", "h5", "h6", "li"}
    out_lines = []

    def walk(node):
        for child in node.children:
            name = getattr(child, "name", None)
            if name is None:
                txt = str(child).strip()
                if txt:
                    out_lines.append(txt)
                continue
            if name in skip_tags:
                continue
            if name in block_tags:
                # для li/h1-h6/p берём полный текст один раз (не рекурсивно),
                # чтобы не дублировать текст вложенных <a>/<span>
                full = re.sub(r"\s+", " ", child.get_text(" ", strip=True)).strip()
                if full:
                    prefix = "- " if name == "li" else ""
                    out_lines.append(prefix + full)
                # НЕ заходим рекурсивно в li/h1-h6 — их текст уже взят
            else:
                walk(child)

    walk(root)

    cleaned = []
    for line in out_lines:
        line = re.sub(r"\s+", " ", line).strip()
        if not line:
            continue
        if any(re.match(p, line) for p in noise_patterns):
            continue
        if cleaned and cleaned[-1] == line:
            continue
        cleaned.append(line)
    return "\n".join(cleaned)


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seminars (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            time TEXT,
            title TEXT,
            url TEXT UNIQUE,
            lecturer TEXT,
            fetched_at TEXT
        )
    """)
    conn.commit()
    return conn


def upsert_seminars(items: list[dict]) -> int:
    """Сохраняет семинары (upsert по url). Возвращает число новых."""
    conn = init_db()
    cur = conn.cursor()
    now = datetime.now().isoformat()
    new_count = 0
    for it in items:
        cur.execute(
            "SELECT id FROM seminars WHERE url=?", (it["url"],)
        )
        if cur.fetchone() is None:
            new_count += 1
        cur.execute("""
            INSERT INTO seminars (date, time, title, url, lecturer, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                date=excluded.date, time=excluded.time, title=excluded.title,
                lecturer=excluded.lecturer, fetched_at=excluded.fetched_at
        """, (it["date"], it["time"], it["title"], it["url"], it["lecturer"], now))
    conn.commit()
    conn.close()
    return new_count


def get_seminars_by_date(d: date) -> list[dict]:
    """Семинары на конкретную дату."""
    conn = init_db()
    cur = conn.cursor()
    ds = d.strftime("%Y-%m-%d")
    cur.execute("SELECT date, time, title, url, lecturer FROM seminars WHERE date=?", (ds,))
    rows = [{"date": r[0], "time": r[1], "title": r[2], "url": r[3], "lecturer": r[4]} for r in cur.fetchall()]
    conn.close()
    return rows


def get_seminars_for_month(year: int, month: int) -> list[dict]:
    """Семинары за конкретный месяц."""
    conn = init_db()
    cur = conn.cursor()
    ds = f"{year:04d}-{month:02d}"
    cur.execute(
        "SELECT date, time, title, url, lecturer FROM seminars WHERE date LIKE ? ORDER BY date, time",
        (ds + "%",),
    )
    result = [{"date": r[0], "time": r[1], "title": r[2], "url": r[3], "lecturer": r[4]} for r in cur.fetchall()]
    conn.close()
    return result