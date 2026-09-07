"""
release_milestones.py — мониторинг контрольных точек релизов Портфеля проектов.

Каждый день (8:00) проверяет релизы из releases_r5l.csv. Если сегодня —
контрольная точка релиза (начало разработки, начало тестирования, накат),
формирует и отправляет в канал «Релизы субхолдингов по 1С» сообщение:
«Сегодня первый день <этапа> по релизу <код>, N задач в статусе <статус>, M в статусе <статус>…»

Источники:
  - releases_r5l.csv: релизы и даты контрольных точек
  - tasks_r5l.csv: задачи релиза и их статусы
"""
import csv
import io
import os
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Optional

# Пути к данным портфеля (тот же каталог, что и скрипты automation_project)
AUTOMATION_DIR = Path(os.path.expanduser("~/automation_project"))
RELEASES_CSV = AUTOMATION_DIR / "releases_r5l.csv"
TASKS_CSV = AUTOMATION_DIR / "tasks_r5l.csv"

# Контрольные точки: (колонка в releases CSV → название этапа)
MILESTONES = {
    "Начало разраб": "разработки",
    "Начало тест": "тестирования",
    "Накат": "наката",
}

# Группировка статусов задач для сообщения больше не используется —
# статусы показываются как есть (по фактическим) в group_task_statuses().


def load_releases() -> list[dict]:
    """Загружает releases_r5l.csv в список словарей."""
    if not RELEASES_CSV.exists():
        return []
    with open(RELEASES_CSV, "r", encoding="utf-8") as f:
        content = f.read()
    reader = csv.reader(io.StringIO(content), delimiter=";")
    rows = list(reader)
    if not rows:
        return []
    header = [h.strip() for h in rows[0]]
    result = []
    for row in rows[1:]:
        if len(row) < len(header):
            row += [""] * (len(header) - len(row))
        rec = dict(zip(header, [c.strip() for c in row]))
        if rec.get("Релиз"):
            result.append(rec)
    return result


def load_tasks() -> list[dict]:
    """Загружает tasks_r5l.csv в список словарей."""
    if not TASKS_CSV.exists():
        return []
    with open(TASKS_CSV, "r", encoding="utf-8") as f:
        content = f.read()
    reader = csv.reader(io.StringIO(content), delimiter=";", quotechar='"')
    rows = list(reader)
    if not rows:
        return []
    header = [h.strip() for h in rows[0]]
    result = []
    for row in rows[1:]:
        if len(row) < len(header):
            row += [""] * (len(header) - len(row))
        rec = dict(zip(header, [c.strip() for c in row]))
        if rec.get("Релиз"):
            result.append(rec)
    return result


def _parse_date(s: str) -> Optional[date]:
    """Парсит дату из CSV. Возвращает None, если пусто/невалидно."""
    if not s:
        return None
    try:
        return date.fromisoformat(s.strip())
    except ValueError:
        return None


def group_task_statuses(tasks: list[dict]) -> Counter:
    """Считает задачи по фактическим статусам (как есть, без группировки)."""
    counter = Counter()
    status_col = None
    # Заголовок: ищем колонку «Последний статус»
    if tasks:
        first = tasks[0]
        for key in first:
            if key.strip() == "Последний статус":
                status_col = key
                break
    for t in tasks:
        st = (t.get(status_col) or "").strip() if status_col else ""
        if st:
            counter[st] += 1  # показываем статус как есть, не сокращая в группы
    return counter


def find_milestones_for_date(
    releases: list[dict], target: Optional[date] = None
) -> list[dict]:
    """
    Возвращает релизы, у которых сегодня (или в target) — контрольная точка.
    Каждый элемент: {release, milestone (ключ), milestone_name, subs, date}
    """
    if target is None:
        target = date.today()

    found = []
    for rel in releases:
        rel_name = rel.get("Релиз", "").strip()
        for key, name in MILESTONES.items():
            dstr = rel.get(key, "").strip()
            d = _parse_date(dstr)
            if d == target:
                found.append({
                    "release": rel_name,
                    "milestone": key,
                    "milestone_name": name,
                    "subs": rel.get("СХ", "").strip(),
                    "date": d,
                    "stads": rel.get("стад релиза", "").strip(),
                })
    return found


def aggregate_tasks_for_release(tasks: list[dict], release_name: str) -> Counter:
    """Возвращает агрегированные статусы задач по релизу."""
    release_tasks = [t for t in tasks if t.get("Релиз", "").strip() == release_name]
    return group_task_statuses(release_tasks)


def format_milestone_message(release: str, milestone_name: str, counter: Counter,
                             when: Optional[date] = None) -> str:
    """Формирует текст сообщения о контрольной точке релиза."""
    today = (when or date.today()).strftime("%d.%m.%Y")
    lines = [
        f"📌 Контрольная точка релиза — {today}",
        "",
        f"🔄 Сегодня первый день «{milestone_name}» по релизу: {release}",
    ]

    # Сводка по статусам задач
    if counter:
        parts = []
        for group, count in counter.most_common():
            if count > 0:
                parts.append(f"{group}: {count}")
        if parts:
            lines.append("")
            lines.append("📊 Задачи по статусам:")
            lines.extend(parts)

    return "\n".join(lines)


def run_milestone_check(
    target: Optional[date] = None,
    release_regex: Optional[str] = None,
) -> list[str]:
    """
    Проверяет релизы и возвращает список сообщений для контрольных точек,
    совпадающих с target (по умолчанию — сегодня).

    release_regex — опциональный фильтр по имени релиза (для теста).
    """
    releases = load_releases()
    tasks = load_tasks()

    if release_regex:
        import re
        releases = [r for r in releases if re.search(release_regex, r.get("Релиз", ""))]

    milestones = find_milestones_for_date(releases, target)

    # Дедупликация: один релиз = одно сообщение (релиз может быть в нескольких СХ)
    seen_releases = set()
    messages = []
    for m in milestones:
        rel_name = m["release"]
        if rel_name in seen_releases:
            continue
        seen_releases.add(rel_name)
        task_counter = aggregate_tasks_for_release(tasks, rel_name)
        msg = format_milestone_message(rel_name, m["milestone_name"], task_counter, when=target)
        messages.append(msg)
    return messages