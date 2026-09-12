"""
html_pdf.py — конвертация HTML → PDF через Chrome/Chromium headless.

Зачем: встроенный просмотрщик Delo Space (DocViewer) нативно открывает PDF
(а также docx/xlsx/pptx/txt до 50 МБ), но НЕ HTML — HTML пришлось бы скачивать.
Поэтому документы, которые бот отправляет в чат, конвертируются в PDF.

Использование:
    from html_pdf import html_to_pdf, find_chrome, HTML_TO_PDF_AVAILABLE
    pdf_bytes = html_to_pdf(html_string)   # bytes | None
"""
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

# Пути к браузерам (macOS и Linux)
CHROME_PATHS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/snap/bin/chromium",
]

# Заголовок/подвал Chrome отключён; таймаут на конвертацию
PRINT_ARGS = ["--headless", "--disable-gpu", "--no-pdf-header-footer", "--no-sandbox"]
TIMEOUT = 120


def find_chrome() -> Optional[str]:
    """Возвращает путь к Chrome/Chromium или None, если не найден."""
    for p in CHROME_PATHS:
        if Path(p).exists():
            return p
    return (shutil.which("google-chrome")
            or shutil.which("google-chrome-stable")
            or shutil.which("chromium")
            or shutil.which("chromium-browser"))


HTML_TO_PDF_AVAILABLE = find_chrome() is not None


def html_to_pdf(html_doc: str, *, verbose: bool = True) -> Optional[bytes]:
    """
    Конвертирует HTML-строку в PDF через Chrome headless (--print-to-pdf).
    Возвращает байты PDF или None при ошибке/отсутствии браузера.
    """
    chrome = find_chrome()
    if not chrome:
        if verbose:
            print("   ⚠ Chrome/Chromium не найден — PDF не будет собран")
        return None
    with tempfile.TemporaryDirectory() as td:
        html_path = Path(td) / "doc.html"
        pdf_path = Path(td) / "doc.pdf"
        html_path.write_text(html_doc, encoding="utf-8")
        try:
            subprocess.run(
                [chrome, *PRINT_ARGS, f"--print-to-pdf={pdf_path}", html_path.as_uri()],
                capture_output=True, timeout=TIMEOUT, check=False,
            )
        except Exception as e:
            if verbose:
                print(f"   ⚠ Ошибка Chrome: {type(e).__name__}: {e}")
            return None
        if not pdf_path.exists() or pdf_path.stat().st_size == 0:
            return None
        return pdf_path.read_bytes()


def html_file_to_pdf(src: Path, *, verbose: bool = True) -> Optional[bytes]:
    """Конвертирует существующий HTML-файл в PDF (байты)."""
    try:
        html_doc = src.read_text(encoding="utf-8-sig", errors="replace")
    except Exception as e:
        if verbose:
            print(f"   ⚠ Не удалось прочитать {src.name}: {e}")
        return None
    return html_to_pdf(html_doc, verbose=verbose)
