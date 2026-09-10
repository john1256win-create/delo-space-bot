"""
release_files.py — построение ссылок на релизы и скачивание файла «Новое в версии».

Для каждого нового релиза:
1. Определяет nick продукта из row["url"]
2. Строит URL страницы версии: /version_files?nick=...&ver=...
3. Проваливается в страницу, ищет ссылку с текстом, содержащим «Новое в версии»
4. Скачивает файл (обычно news.htm) во временную папку
"""
import json
import os
import re
import sys
import requests
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
import scraper
from bs4 import BeautifulSoup

DOWNLOAD_DIR = Path(__file__).parent / "downloads"
PENDING_FILE = DOWNLOAD_DIR / "pending_files.json"


def _is_service_unavailable(txt: str) -> bool:
    """Проверяет, что страница — заглушка «сервис временно недоступен»."""
    low = txt.lower()
    return "временно недоступен" in low or "ошибка на нашем сервере" in low


def _load_pending() -> list[dict]:
    """Загружает отложенные релизы (сервис файлов был недоступен)."""
    if not PENDING_FILE.exists():
        return []
    try:
        return json.loads(PENDING_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_pending(rows: list[dict]) -> None:
    """Сохраняет отложенные релизы в PENDING_FILE."""
    PENDING_FILE.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def _clean_version(version: str) -> str:
    """
    Убирает буквенные суффиксы из номера версии для построения URL.
    Пример: '3.1.38.92ДП' -> '3.1.38.92'
    На странице релиза номер указан без суффикса.
    """
    import re
    # Оставляем только числовые части и точки: убираем не-цифровой хвост
    m = re.match(r'^([\d.]+)', version)
    return m.group(1).rstrip('.') if m else version


def version_url(row: dict) -> str:
    """
    Строит URL страницы релиза из row.
    row['url'] = https://releases.1c.ru/project/EnterpriseERP20
    row['version'] = 2.5.27.81ДП
    -> https://releases.1c.ru/version_files?nick=EnterpriseERP20&ver=2.5.27.81
    """
    project_url = row.get("url", "")
    version = _clean_version(row.get("version", ""))
    if not project_url or not version:
        return ""
    # nick = последний сегмент project URL
    nick = project_url.rstrip("/").split("/")[-1]
    return f"https://releases.1c.ru/version_files?nick={nick}&ver={version}"


def _get_session() -> requests.Session:
    """Возвращает авторизованную сессию с гарантированным доступом к страницам версий."""
    import os as _os

    def _make_session() -> requests.Session:
        s = requests.Session()
        s.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })
        return s

    session = _make_session()
    if not scraper.auth(session):
        raise RuntimeError("Не удалось авторизоваться на releases.1c.ru")

    # Проверяем реальный доступ к странице версии: если auth вернул True
    # по старым cookies, а они протухли для version_files — редирект на login.
    probe = session.get(
        "https://releases.1c.ru/version_files?nick=HRMCorp30&ver=3.1.38.92",
        timeout=45, allow_redirects=False,
    )
    if probe.status_code in (301, 302) and "login" in (probe.headers.get("Location", "")):
        # cookies протухли — сбрасываем и логинимся заново
        _os.remove(scraper.COOKIE_FILE) if _os.path.exists(scraper.COOKIE_FILE) else None
        session = _make_session()
        if not scraper.auth(session):
            raise RuntimeError("Не удалось авторизоваться на releases.1c.ru (после сброса cookies)")
        probe2 = session.get(
            "https://releases.1c.ru/version_files?nick=HRMCorp30&ver=3.1.38.92",
            timeout=45, allow_redirects=False,
        )
        if probe2.status_code in (301, 302):
            raise RuntimeError("Не удалось получить доступ к страницам версии после повторного логина")

    return session


def find_news_url(session: requests.Session, row: dict) -> tuple[Optional[str], bool]:
    """Проваливается в страницу релиза и ищет ссылку «Новое в версии».

    Возвращает (url, service_unavailable). service_unavailable=True, если
    сервис файлов отдал заглушку «временно недоступен» (файл надо отложить).
    """
    vurl = version_url(row)
    if not vurl:
        return None, False
    try:
        resp = session.get(vurl, timeout=45)
        resp.raise_for_status()
    except Exception as e:
        print(f"   ⚠ Ошибка загрузки {vurl}: {e}")
        return None, False

    if _is_service_unavailable(resp.text):
        return None, True

    soup = BeautifulSoup(resp.text, "html.parser")
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True).lower()
        href = str(a["href"])
        if "новое в версии" in text:
            # href может быть относительным (/version_file?...)
            if href.startswith("/"):
                href = "https://releases.1c.ru" + href
            return href, False
    return None, False


def download_news(session: requests.Session, row: dict) -> tuple[Optional[Path], bool]:
    """
    Скачивает файл «Новое в версии» для релиза.
    Возвращает (путь к файлу, service_unavailable). service_unavailable=True,
    если сервис файлов недоступен — файл следует отложить и повторить позже.
    """
    news_url, unavailable = find_news_url(session, row)
    if unavailable:
        return None, True
    if not news_url:
        return None, False

    version = row.get("version", "unknown")
    product = row.get("product", "product")
    # безопасное имя файла
    safe_product = re.sub(r'[\\/:*?"<>|]', "_", product)[:40]
    safe_ver = re.sub(r'[\\/:*?"<>|]', "_", version)[:30]

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    # определяем расширение из URL
    ext = ".htm"
    m = re.search(r'\.([a-z0-9]+)$', news_url.lower().split("?")[0])
    if m:
        ext = "." + m.group(1)

    filepath = DOWNLOAD_DIR / f"{safe_product}_{safe_ver}{ext}"

    try:
        resp = session.get(news_url, timeout=60)
        resp.raise_for_status()
        filepath.write_bytes(resp.content)
        return filepath, False
    except Exception as e:
        print(f"   ⚠ Ошибка скачивания {news_url}: {e}")
        return None, False


def download_all_news_files(new_rows: list[dict]) -> list[Path]:
    """Скачивает «Новое в версии» для всех новых релизов.

    Если сервис файлов недоступен (заглушка «временно недоступен»), релиз
    сохраняется в pending_files.json и будет повторён при следующем запуске
    (launchd запускает run_daily каждые ~2 часа, т.е. повтор ~через 120 минут).
    """
    # 1) Сначала пробуем докачать отложенные релизы (сервис мог восстановиться)
    pending = _load_pending()
    downloaded = []
    if pending:
        print(f"   🔁 Отложенных релизов (сервис был недоступен): {len(pending)}")
        try:
            session = _get_session()
        except Exception as e:
            print(f"   ⚠ Не удалось авторизоваться для отложенных: {e}")
            session = None
        if session is not None:
            still_pending = []
            for row in pending:
                print(f"   🔎 Повтор «Новое в версии» для {row['product']} {row['version']}...")
                fp, unavailable = download_news(session, row)
                if fp:
                    downloaded.append(fp)
                    print(f"      ✅ Скачан (отложенный): {fp.name}")
                elif unavailable:
                    still_pending.append(row)
                    print(f"      ⏳ Сервис всё ещё недоступен — оставляю в отложенных")
                else:
                    print(f"      ⏭ Ссылка «Новое в версии» не найдена (отложенный)")
            _save_pending(still_pending)

    # 2) Новые релизы из текущего diff
    if not new_rows:
        return downloaded
    try:
        session = _get_session()
    except Exception as e:
        print(f"   ⚠ Не удалось авторизоваться для скачивания: {e}")
        return downloaded

    new_pending = []
    for row in new_rows:
        print(f"   🔎 Ищу «Новое в версии» для {row['product']} {row['version']}...")
        fp, unavailable = download_news(session, row)
        if fp:
            downloaded.append(fp)
            print(f"      ✅ Скачан: {fp.name}")
        elif unavailable:
            new_pending.append(row)
            print(f"      ⏳ Сервис файлов недоступен — откладываю (повтор через ~120 мин)")
        else:
            print(f"      ⏭ Ссылка «Новое в версии» не найдена")

    # 3) Сохраняем отложенные (добавляем к оставшимся старым)
    if new_pending:
        remaining = _load_pending() + new_pending
        _save_pending(remaining)
        print(f"   📌 Отложено файлов для повторной попытки: {len(new_pending)}")

    return downloaded