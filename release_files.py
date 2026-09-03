"""
release_files.py — построение ссылок на релизы и скачивание файла «Новое в версии».

Для каждого нового релиза:
1. Определяет nick продукта из row["url"]
2. Строит URL страницы версии: /version_files?nick=...&ver=...
3. Проваливается в страницу, ищет ссылку с текстом, содержащим «Новое в версии»
4. Скачивает файл (обычно news.htm) во временную папку
"""
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
    """Возвращает авторизованную сессию."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    })
    if not scraper.auth(session):
        raise RuntimeError("Не удалось авторизоваться на releases.1c.ru")
    return session


def find_news_url(session: requests.Session, row: dict) -> Optional[str]:
    """Проваливается в страницу релиза и ищет ссылку «Новое в версии»."""
    vurl = version_url(row)
    if not vurl:
        return None
    try:
        resp = session.get(vurl, timeout=45)
        resp.raise_for_status()
    except Exception as e:
        print(f"   ⚠ Ошибка загрузки {vurl}: {e}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True).lower()
        href = str(a["href"])
        if "новое в версии" in text:
            # href может быть относительным (/version_file?...)
            if href.startswith("/"):
                href = "https://releases.1c.ru" + href
            return href
    return None


def download_news(session: requests.Session, row: dict) -> Optional[Path]:
    """
    Скачивает файл «Новое в версии» для релиза.
    Возвращает путь к файлу или None, если ссылки нет.
    """
    news_url = find_news_url(session, row)
    if not news_url:
        return None

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
        return filepath
    except Exception as e:
        print(f"   ⚠ Ошибка скачивания {news_url}: {e}")
        return None


def download_all_news_files(new_rows: list[dict]) -> list[Path]:
    """Скачивает «Новое в версии» для всех новых релизов."""
    if not new_rows:
        return []
    try:
        session = _get_session()
    except Exception as e:
        print(f"   ⚠ Не удалось авторизоваться для скачивания: {e}")
        return []

    downloaded = []
    for row in new_rows:
        print(f"   🔎 Ищу «Новое в версии» для {row['product']} {row['version']}...")
        fp = download_news(session, row)
        if fp:
            downloaded.append(fp)
            print(f"      ✅ Скачан: {fp.name}")
        else:
            print(f"      ⏭ Ссылка «Новое в версии» не найдена")

    return downloaded