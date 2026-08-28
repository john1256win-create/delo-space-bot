#!/usr/bin/env python3
"""Мониторинг изменений на v8.1c.ru/lawmonitor (временное решение)."""
import hashlib
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import OUT_DIR

LAW_MONITOR_URL = "https://v8.1c.ru/lawmonitor/608af4c5-08ea-11f1-8d02-005056bea45e.htm"

def fetch_law_monitor() -> list[dict]:
    """Загружает страницу и извлекает список продуктов со статусами."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    })
    
    resp = session.get(LAW_MONITOR_URL, timeout=30)
    resp.raise_for_status()
    
    soup = BeautifulSoup(resp.text, "html.parser")
    return parse_products(soup)


def parse_products(soup: BeautifulSoup) -> list[dict]:
    """Парсит список продуктов со статусами изменений."""
    rows = []
    
    # Получаем весь текст страницы
    text = soup.get_text(separator="\n", strip=True)
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    
    # Паттерны для определения продуктов
    product_prefixes = ("1С:", "Бухгалтерия", "Зарплата", "Комплексная", 
                       "Налогоплательщик", "Платежные", "Розница", "Садовод", 
                       "Управление", "Клиент")
    
    # Паттерны для определения статусов
    status_keywords = ("Запланировано", "Не планируется", "Реализовано", "Срок будет указан позднее")
    
    i = 0
    while i < len(lines):
        line = lines[i]
        
        # Проверяем, является ли строка названием продукта
        if any(line.startswith(prefix) for prefix in product_prefixes):
            product_name = line
            
            # Ищем статус в следующих строках
            status = None
            details = ""
            
            for j in range(i + 1, min(i + 5, len(lines))):
                next_line = lines[j]
                
                # Проверяем, является ли строка статусом
                if any(keyword in next_line for keyword in status_keywords):
                    status = next_line
                    
                    # Проверяем, есть ли детали в следующей строке
                    if j + 1 < len(lines):
                        details_line = lines[j + 1]
                        # Если следующая строка не является продуктом или статусом, это детали
                        if not any(details_line.startswith(p) for p in product_prefixes) and \
                           not any(keyword in details_line for keyword in status_keywords):
                            details = details_line
                    break
                
                # Если встретили другой продукт, останавливаемся
                if any(next_line.startswith(p) for p in product_prefixes):
                    break
            
            # Если нашли статус, добавляем запись
            if status:
                row = {
                    "product": product_name,
                    "status": status,
                    "details": details
                }
                row["hash"] = row_hash(row)
                rows.append(row)
        
        i += 1
    
    return rows


def row_hash(row: dict) -> str:
    """Вычисляет хеш строки для сравнения."""
    s = f"{row.get('product', '')}|{row.get('status', '')}|{row.get('details', '')}"
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def save_law_monitor_csv(rows: list[dict]) -> Path:
    """Сохраняет данные в CSV."""
    import csv
    
    today = datetime.now().strftime("%Y-%m-%d")
    path = OUT_DIR / f"lawmonitor_{today}.csv"
    
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["product", "status", "details", "hash"])
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    
    return path


def load_law_monitor_csv(path: Path) -> list[dict]:
    """Загружает данные из CSV."""
    import csv
    
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    
    return rows


def latest_law_monitor_csv() -> Optional[Path]:
    """Возвращает путь к последнему CSV файлу lawmonitor."""
    files = sorted(OUT_DIR.glob("lawmonitor_*.csv"), reverse=True)
    return files[0] if files else None


def run_law_monitor_scrape() -> tuple[list[dict], list[dict], list[dict]]:
    """
    Полный цикл: загрузка → сохранение → diff.
    Возвращает (all_rows, new_rows, removed_rows).
    """
    current = fetch_law_monitor()
    
    if not current:
        raise RuntimeError("Не удалось получить данные с v8.1c.ru/lawmonitor")
    
    # Загружаем предыдущий CSV ДО сохранения текущего
    prev_path = latest_law_monitor_csv()
    prev = load_law_monitor_csv(prev_path) if prev_path else []
    
    # Сохраняем текущий CSV
    save_law_monitor_csv(current)
    
    # Сравниваем с предыдущим
    if not prev:
        return current, current, []
    
    prev_hashes = {r["hash"] for r in prev}
    curr_hashes = {r["hash"] for r in current}
    
    new_rows = [r for r in current if r["hash"] not in prev_hashes]
    removed_rows = [r for r in prev if r["hash"] not in curr_hashes]
    
    return current, new_rows, removed_rows
