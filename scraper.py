"""
scraper.py — парсинг releases.1c.ru/total.
Адаптировано из 1c_release_scraper.py для использования в боте Delo Space.
"""
import csv
import hashlib
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

from config import CRED_FILE, COOKIE_FILE, OUT_DIR, ITS_USERNAME, ITS_PASSWORD

# ── константы ──────────────────────────────────────────────
LOGIN_URL = "https://login.1c.ru/login"
LOGIN_SERVICE = "https://releases.1c.ru/public/security_check"
TOTAL_URL = "https://releases.1c.ru/total"
TIMEOUT = 45
MAX_RETRIES = 3

CSV_COLUMNS = [
    "product", "group", "version", "title", "date", "type_rel", "url", "hash",
]


# ═══════════════════════════════════════════════════════════
#  хеширование
# ═══════════════════════════════════════════════════════════

def row_hash(row: dict) -> str:
    s = f"{row.get('product','')}|{row.get('version','')}|{row.get('title','')}|{row.get('group','')}|{row.get('date','')}"
    return hashlib.sha256(s.encode()).hexdigest()[:16]


# ═══════════════════════════════════════════════════════════
#  cookies
# ═══════════════════════════════════════════════════════════

def save_cookies(session: requests.Session):
    safe = {c.name: c.value for c in session.cookies}
    COOKIE_FILE.write_text(json.dumps(safe, ensure_ascii=False), encoding="utf-8")


def load_cookies(session: requests.Session) -> bool:
    if not COOKIE_FILE.exists():
        return False
    try:
        safe = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
        for name, value in safe.items():
            session.cookies.set(name, value, domain=".1c.ru", path="/")
        return len(safe) > 0
    except Exception:
        return False


def cookies_valid(session: requests.Session) -> bool:
    try:
        r = session.head(TOTAL_URL, timeout=TIMEOUT, allow_redirects=False)
        if r.status_code < 400:
            return True
        if "login" in r.headers.get("Location", "").lower():
            return False
        r2 = session.get(TOTAL_URL, timeout=TIMEOUT, allow_redirects=True)
        return "login" not in r2.url.lower()
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════
#  авторизация
# ═══════════════════════════════════════════════════════════

def get_credentials() -> tuple[str, str]:
    """Возвращает логин/пароль из .env или credentials.json."""
    if ITS_USERNAME and ITS_PASSWORD:
        return ITS_USERNAME, ITS_PASSWORD
    if CRED_FILE.exists():
        try:
            c = json.loads(CRED_FILE.read_text(encoding="utf-8"))
            if c.get("username") and c.get("password"):
                return c["username"], c["password"]
        except Exception:
            pass
    return "", ""


def auth(session: requests.Session) -> bool:
    """Авторизация на портале ИТС."""
    username, password = get_credentials()
    if not username or not password:
        return False

    # пробуем восстановить сессию
    if load_cookies(session):
        if cookies_valid(session):
            return True

    # CAS-логин
    params = {"service": LOGIN_SERVICE}
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(LOGIN_URL, params=params, timeout=TIMEOUT, allow_redirects=True)
            resp.raise_for_status()
            break
        except Exception:
            if attempt == MAX_RETRIES:
                return False
            time.sleep(2 ** attempt)
    else:
        return False

    soup = BeautifulSoup(resp.text, "html.parser")
    exec_input = soup.find("input", {"name": "execution"})
    execution = exec_input.get("value", "") if exec_input else ""
    if not execution:
        m = re.search(r'name="execution"\s+value="([^"]+)"', resp.text)
        if m:
            execution = m.group(1)
    if not execution:
        return False

    data = {
        "username": username,
        "password": password,
        "execution": execution,
        "_eventId": "submit",
        "geolocation": "",
    }
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            login_resp = session.post(
                LOGIN_URL, params=params, data=data, timeout=TIMEOUT, allow_redirects=True
            )
            login_resp.raise_for_status()
            break
        except Exception:
            if attempt == MAX_RETRIES:
                return False
            time.sleep(2 ** attempt)
    else:
        return False

    if "login" in login_resp.url.lower() and "releases" not in login_resp.url.lower():
        COOKIE_FILE.unlink(missing_ok=True)
        return False

    save_cookies(session)
    return True


# ═══════════════════════════════════════════════════════════
#  парсинг
# ═══════════════════════════════════════════════════════════

def fetch_releases(session: requests.Session) -> list[dict]:
    """Загружает /total и парсит таблицу."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(TOTAL_URL, timeout=TIMEOUT)
            resp.raise_for_status()
            if "login" in resp.url.lower():
                COOKIE_FILE.unlink(missing_ok=True)
                return []
            break
        except Exception:
            if attempt == MAX_RETRIES:
                return []
            time.sleep(2 ** attempt)
    else:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    return parse_releases(soup)


def parse_releases(soup: BeautifulSoup) -> list[dict]:
    releases: list[dict] = []
    current_group = ""
    table = soup.find("table", class_="customTable")
    if table is None:
        return []

    for tr in table.find_all("tr")[1:]:
        tds = tr.find_all("td")
        if not tds:
            continue
        colspan = int(tds[0].get("colspan", 0))
        if colspan >= 3:
            current_group = tds[0].get_text(strip=True)
            continue
        if len(tds) < 3:
            continue

        product_name = tds[0].get_text(strip=True)
        project_url = ""
        for a in tds[0].find_all("a"):
            href = a.get("href", "")
            if href and "project" in href:
                project_url = "https://releases.1c.ru" + href
                break

        sections = [
            (tds[1], tds[2], "Актуальная"),
            (tds[3], tds[4], "Планируемая"),
        ]
        for ver_td, date_td, ver_type in sections:
            vers = extract_versions(ver_td)
            dates = extract_dates(date_td)
            for idx in range(min(len(vers), len(dates))):
                row = {
                    "product": product_name,
                    "group": current_group,
                    "version": vers[idx],
                    "title": ver_type,
                    "date": parse_date(dates[idx]),
                    "type_rel": ver_type,
                    "url": project_url,
                }
                row["hash"] = row_hash(row)
                releases.append(row)

    # дедупликация
    seen = set()
    uniq = []
    for rel in releases:
        if rel["hash"] not in seen:
            seen.add(rel["hash"])
            uniq.append(rel)
    return uniq


def extract_versions(td) -> list[str]:
    if td is None:
        return []
    lines = list(td.stripped_strings)
    if not lines:
        return []
    result = []
    for line in lines:
        line = line.strip()
        if not line or line.lower() in ("не определена", "не определено", "—"):
            continue
        if re.match(r"^\d{2}\.\d{2}\.\d{2,4}$", line):
            continue
        if len(line) <= 4 and result and not re.match(r"^\d+\.", line):
            result[-1] = result[-1] + line
        else:
            result.append(line)
    return result


def extract_dates(td) -> list[str]:
    if td is None:
        return []
    return [s.strip() for s in td.stripped_strings if re.match(r"^\d{2}\.\d{2}\.\d{2,4}$", s.strip())]


def parse_date(raw: str) -> str:
    raw = raw.strip()
    for fmt in ["%d.%m.%Y", "%Y-%m-%d", "%d.%m.%y", "%d/%m/%Y"]:
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw


# ═══════════════════════════════════════════════════════════
#  CSV
# ═══════════════════════════════════════════════════════════

def csv_filename() -> Path:
    today = datetime.now().strftime("%Y-%m-%d")
    return OUT_DIR / f"releases_{today}.csv"


def last_csv() -> Optional[Path]:
    today = datetime.now().strftime("%Y-%m-%d")
    files = sorted(OUT_DIR.glob("releases_*.csv"), reverse=True)
    for f in files:
        if today not in f.name:
            return f
    return None


def latest_csv() -> Optional[Path]:
    files = sorted(OUT_DIR.glob("releases_*.csv"), reverse=True)
    return files[0] if files else None


def save_csv(rows: list[dict]) -> Path:
    path = csv_filename()
    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS + ["scraped_at"], delimiter=";")
        f.write(f"# scraped: {today}\n")
        writer.writeheader()
        for row in rows:
            row["scraped_at"] = today
            writer.writerow(row)
    return path


def load_csv(path: Path) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        lines = [ln for ln in f if not ln.startswith("#")]
    if not lines:
        return rows
    reader = csv.DictReader(lines, delimiter=";")
    for row in reader:
        rows.append(row)
    return rows


# ═══════════════════════════════════════════════════════════
#  основная логика
# ═══════════════════════════════════════════════════════════

def run_scrape() -> tuple[list[dict], list[dict], list[dict]]:
    """
    Запускает полный цикл: авторизация → парсинг → сохранение → diff.
    Возвращает (all_rows, new_rows, removed_rows).
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    })

    # Авторизация с автоматическим ре-логином при протухших cookies.
    # За ночь cookies протухают: fetch_releases удаляет их и возвращает пусто.
    # Тогда сбрасываем cookies, логинимся заново и повторяем (до 2 попыток).
    if not auth(session):
        raise RuntimeError("Не удалось авторизоваться на releases.1c.ru")

    current = fetch_releases(session)
    for _ in range(2):
        if current:
            break
        # cookies протухли (fetch вернул пусто) — сброс и повторная авторизация
        COOKIE_FILE.unlink(missing_ok=True)
        if not auth(session):
            raise RuntimeError("Не удалось авторизоваться на releases.1c.ru (после сброса cookies)")
        current = fetch_releases(session)

    if not current:
        raise RuntimeError("Не удалось получить список релизов")

    save_csv(current)

    prev_path = last_csv()
    if prev_path is None:
        return current, current, []

    prev = load_csv(prev_path)
    prev_hashes = {r["hash"] for r in prev}
    curr_hashes = {r["hash"] for r in current}

    new_rows = [r for r in current if r["hash"] not in prev_hashes]
    removed_rows = [r for r in prev if r["hash"] not in curr_hashes]

    return current, new_rows, removed_rows


def query_product(name: str) -> list[dict]:
    """Ищет продукт по подстроке в последнем CSV."""
    path = latest_csv()
    if path is None:
        return []
    rows = load_csv(path)
    name_lower = name.lower()
    return [r for r in rows if name_lower in r["product"].lower()]
