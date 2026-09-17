"""
release_files.py — построение ссылок на релизы и скачивание файла «Новое в версии».

Для каждого нового релиза:
1. Определяет nick продукта из row["url"]
2. Строит URL страницы версии: /version_files?nick=...&ver=...
3. Проваливается в страницу, ищет ссылку с текстом, содержащим «Новое в версии»
4. Скачивает файл (обычно news.htm) во временную папку
5. Конвертирует HTML -> PDF (Chrome headless), т.к. встроенный просмотрщик
   Delo Space открывает PDF нативно, а HTML пришлось бы скачивать

Если сервис файлов releases.1c.ru отдаёт заглушку «Ошибка на нашем сервере»
(«временно недоступен»), релиз ставится в очередь повторов и retry_loop()
делает до MAX_ATTEMPTS=5 попыток с интервалом RETRY_DELAY=1 час.
"""
import json
import os
import re
import sys
import requests
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
import scraper
from bs4 import BeautifulSoup
from html_pdf import html_file_to_pdf, HTML_TO_PDF_AVAILABLE

DOWNLOAD_DIR = Path(__file__).parent / "downloads"
PENDING_FILE = DOWNLOAD_DIR / "pending_files.json"

# Повтор при недоступности сервиса файлов releases.1c.ru:
# 5 попыток с интервалом 1 час (заглушка «Ошибка на нашем сервере»).
MAX_ATTEMPTS = 5
RETRY_DELAY = 3600          # 1 час между попытками
RETRY_TOLERANCE = 300       # допуск: запуск в :05 и :00 — это одна и та же попытка
RETRY_LOCK = DOWNLOAD_DIR / ".retry.lock"


def _is_service_unavailable(txt: str) -> bool:
    """Проверяет, что страница — заглушка «сервис временно недоступен»."""
    low = txt.lower()
    return "временно недоступен" in low or "ошибка на нашем сервере" in low


def _load_pending() -> list[dict]:
    """Загружает отложенные релизы (сервис файлов был недоступен)."""
    if not PENDING_FILE.exists():
        return []
    try:
        items = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    for it in items:
        it.setdefault("attempts", 1)
    return items


def _save_pending(rows: list[dict]) -> None:
    """Сохраняет отложенные релизы (атомарно, чтобы не побить файл при гонке)."""
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    tmp = PENDING_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, PENDING_FILE)


def pending_items() -> list[dict]:
    """Отложенные релизы, ожидающие восстановления сервиса."""
    return _load_pending()


def _key(row: dict) -> tuple:
    """Ключ релиза для дедупликации отложенных записей."""
    return (row.get("product", ""), row.get("version", ""))


def acquire_retry_lock() -> bool:
    """Захватывает lock цикла повторов, чтобы не запускать его параллельно.

    launchd гоняет run_daily каждые 2 часа, а цикл повторов может работать дольше —
    без lock два процесса дублировали бы попытки и отправку файлов.
    """
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(RETRY_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            # lock есть: если процесс-владелец мёртв — снимаем и пробуем снова
            try:
                pid = int(RETRY_LOCK.read_text(encoding="utf-8").strip() or 0)
                os.kill(pid, 0)
                return False            # владелец жив — цикл уже идёт
            except (ValueError, ProcessLookupError, OSError):
                RETRY_LOCK.unlink(missing_ok=True)
    return False


def release_retry_lock() -> None:
    """Освобождает lock цикла повторов."""
    RETRY_LOCK.unlink(missing_ok=True)


def enqueue_pending(row: dict) -> None:
    """Ставит релиз в очередь на повтор (первая попытка уже была неудачной).

    attempts = сколько попыток получить файл уже сделано (первая — в
    download_all_news_files). next_retry_at = плановое время следующей попытки,
    т.е. ровно через RETRY_DELAY (1 час) — как требует постановка.
    """
    items = _load_pending()
    for it in items:
        if _key(it) == _key(row):
            return                      # уже в очереди — не дублируем
    entry = dict(row)
    entry["attempts"] = 1
    entry["next_retry_at"] = (datetime.now() + timedelta(seconds=RETRY_DELAY)).strftime(
        "%Y-%m-%d %H:%M:%S")
    items.append(entry)
    _save_pending(items)


def attempt_pending(force: bool = False) -> tuple[list[Path], bool]:
    """Одна попытка скачать все отложенные файлы.

    Возвращает (скачанные файлы, есть ли ещё ожидающие).
    Всего делается до MAX_ATTEMPTS попыток (первая — в download_all_news_files,
    остальные — здесь). После исчерпания запись снимается, чтобы не зацикливаться.

    Записи, у которых с прошлой попытки не прошёл час, пропускаются: повторы
    запускаются и launchd'ом (:05), и из run_daily (:00, 7/9/11/13/15/17/19/21)
    — без этого попытки тратились бы вдвое быстрее. force=True игнорирует
    интервал (для ручного/тестового прогона).
    """
    items = _load_pending()
    if not items:
        return [], False

    try:
        session = _get_session()
    except Exception as e:
        print(f"   ⚠ Не удалось авторизоваться для повтора: {e}")
        return [], True

    downloaded: list[Path] = []
    keep: list[dict] = []
    now = datetime.now()
    for row in items:
        done = int(row.get("attempts", 1))     # попыток уже сделано
        n = done + 1                            # номер текущей попытки

        if not force and row.get("next_retry_at"):
            try:
                last = datetime.strptime(row["next_retry_at"], "%Y-%m-%d %H:%M:%S")
                wait = RETRY_DELAY - (now - last).total_seconds()
                if wait > RETRY_TOLERANCE:
                    keep.append(row)            # час ещё не прошёл
                    print(f"   ⏸ {row.get('product')} {row.get('version')}: "
                          f"до следующей попытки {int(wait // 60)} мин — пропускаю")
                    continue
            except ValueError:
                pass

        print(f"   🔎 Повтор «Новое в версии» для {row.get('product')} "
              f"{row.get('version')} (попытка {n}/{MAX_ATTEMPTS})...")
        fp, unavailable = download_news(session, row)
        if fp:
            downloaded.append(fp)
            print(f"      ✅ Скачан: {fp.name}")
            continue
        if not unavailable:
            print("      ⏭ Ссылка «Новое в версии» не найдена — снимаю с повторов")
            continue
        if n >= MAX_ATTEMPTS:
            print(f"      ⛔ Сервис недоступен, попыток исчерпано ({MAX_ATTEMPTS}) — снимаю")
            continue
        row["attempts"] = n
        row["next_retry_at"] = (now + timedelta(seconds=RETRY_DELAY)).strftime(
            "%Y-%m-%d %H:%M:%S")
        keep.append(row)
        print(f"      ⏳ Сервис недоступен — остаётся в очереди "
              f"(сделано попыток {n}/{MAX_ATTEMPTS})")

    _save_pending(keep)
    return downloaded, bool(keep)


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
    except Exception as e:
        print(f"   ⚠ Ошибка скачивания {news_url}: {e}")
        return None, False

    # Конвертируем HTML -> PDF: встроенный просмотрщик Delo Space открывает
    # PDF нативно, а HTML пришлось бы скачивать.
    if ext in (".htm", ".html") and HTML_TO_PDF_AVAILABLE:
        pdf_bytes = html_file_to_pdf(filepath, verbose=False)
        if pdf_bytes:
            pdf_path = filepath.with_suffix(".pdf")
            pdf_path.write_bytes(pdf_bytes)
            filepath.unlink(missing_ok=True)  # промежуточный .htm больше не нужен
            return pdf_path, False
        print(f"   ⚠ PDF не собран из {filepath.name} — отправляю HTML")

    return filepath, False


def download_all_news_files(new_rows: list[dict]) -> list[Path]:
    """Скачивает «Новое в версии» для новых релизов.

    Если сервис файлов недоступен (заглушка «временно недоступен»), релиз
    ставится в очередь повторов (pending_files.json) — повтор выполняет
    retry_loop() через час, до MAX_ATTEMPTS попыток.
    """
    if not new_rows:
        return []
    try:
        session = _get_session()
    except Exception as e:
        print(f"   ⚠ Не удалось авторизоваться для скачивания: {e}")
        for row in new_rows:
            enqueue_pending(row)
        print(f"   📌 Отложено до восстановления доступа: {len(new_rows)}")
        return []

    downloaded: list[Path] = []
    for row in new_rows:
        print(f"   🔎 Ищу «Новое в версии» для {row['product']} {row['version']}...")
        fp, unavailable = download_news(session, row)
        if fp:
            downloaded.append(fp)
            print(f"      ✅ Скачан: {fp.name}")
        elif unavailable:
            enqueue_pending(row)
            print(f"      ⏳ Сервис файлов недоступен — в очередь повторов "
                  f"(через {RETRY_DELAY // 60} мин, до {MAX_ATTEMPTS} попыток)")
        else:
            print(f"      ⏭ Ссылка «Новое в версии» не найдена")

    return downloaded


def run_once(on_files=None) -> int:
    """Делает ОДНУ попытку получить отложенные файлы.

    Вызывается по расписанию launchd (раз в час, см. agent
    com.salnikov.1c-release-retry) — поэтому не содержит sleep и не висит
    процессом: счётчик попыток живёт в pending_files.json, а после
    MAX_ATTEMPTS запись снимается. Это надёжнее долгоживущего процесса,
    который может умереть при перезагрузке или сне Mac.

    При появлении файлов вызывает on_files(список путей) — для отправки в чат.
    Возвращает число полученных файлов.
    """
    if not _load_pending():
        return 0

    if not acquire_retry_lock():
        print("   ⏸ Попытка уже выполняется в другом процессе — выходим")
        return 0

    try:
        files, still = attempt_pending()
        n = len(files)
        if n and on_files:
            try:
                on_files(files)
            except Exception as e:
                print(f"   ⚠ Не удалось отправить файлы в чат: {e}")
        if still:
            left = _load_pending()
            done = max(int(i.get("attempts", 1)) for i in left)
            if done < MAX_ATTEMPTS:
                print(f"   💤 Следующая попытка — через час "
                      f"({done}/{MAX_ATTEMPTS} попыток сделано)")
            else:
                print(f"   ⛔ Попытки исчерпаны ({MAX_ATTEMPTS})")
        else:
            print("   ✔ Очередь повторов пуста")
        return n
    finally:
        release_retry_lock()