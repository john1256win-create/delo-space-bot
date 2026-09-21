#!/usr/bin/env python3
"""
weekly_release_report.py — еженедельный отчёт по активным релизам (по понедельникам).

Постановка пользователя: каждый понедельник присылать в личный чат (Евгений
Сальников) отчёт по релизам, которые находятся в стадии от 1 до 2 (с начала
разработки до даты наката включительно). В начале отчёта — статистика релиза и
стадия (разработка / тестирование / накат), затем — задачи по статусам
(по аналогии с сообщением контрольной точки релиза).

В отчёт включаются ТОЛЬКО задачи по пяти системам:
  1С: БП КОРП SASCO, 1С: ERP ГП, 1С: БП/Рускон РК, 1С: УХ ТК, 1С: БП (УХ) УКД

Стадия вычисляется по датам релиза (надёжнее, чем поле release_stage):
  today <= dev_end                    → «разработка»
  dev_end < today <= test_end         → «тестирование»
  test_end < today <= rollout_date    → «накат»

Данные — напрямую из SQLite портфеля (как в release_test_report.py): API может
быть остановлен, а отчёт должен уходить по расписанию.

Запуск:
  python3 weekly_release_report.py               # отчёт по активным релизам
  python3 weekly_release_report.py --dry-run     # показать, не отправлять
  python3 weekly_release_report.py --date 2026-09-21
"""
import argparse
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# Переиспользуем инфраструктуру существующего отчёта: порядок статусов,
# округление часов, нормализация пробелов, стрелки план/факт и дробление текста.
from release_test_report import (  # noqa: E402
    MY_CHAT_ID,
    PORTFOLIO_DB,
    chunk,
    labor_arrow,
    num,
)
from release_milestones import STATUS_ORDER as MILESTONE_STATUS_ORDER  # noqa: E402

# ── Пять систем, попадающих в отчёт (требование пользователя) ──────────────
TARGET_SYSTEMS = [
    "1С: БП КОРП SASCO",
    "1С: ERP ГП",
    "1С: БП/Рускон РК",
    "1С: УХ ТК",
    "1С: БП (УХ) УКД",
]

# Стадии релиза: поле release_stage в БД (0 — новый, 3 — прошёл/закрыт).
ACTIVE_STAGES = {"1", "2"}

STAGE_NAMES = ("разработка", "тестирование", "накат")

# Максимальная длина одной части сообщения для BotX.
PART_LIMIT = 3500


def stage_of(rel: dict, today: date) -> str | None:
    """Стадия релиза по датам. None — если релиз вне окна «разработка…накат»."""
    dev_end = _d(rel.get("dev_end"))
    test_end = _d(rel.get("test_end"))
    rollout = _d(rel.get("rollout_date"))
    if today <= (dev_end or date.min) or (dev_end is None and test_end is None):
        # Даты не заполнены — считаем «разработкой» только при явном dev_end.
        return "разработка" if dev_end else None
    if test_end and today <= test_end:
        return "тестирование"
    if rollout and today <= rollout:
        return "накат"
    return None


def _d(s) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s).strip())
    except ValueError:
        return None


def _fmt_d(s) -> str:
    d = _d(s)
    return d.strftime("%d.%m") if d else "—"


def load_active_releases(conn: sqlite3.Connection, today: date) -> list[dict]:
    """Релизы в стадиях 1–2, по каждому — вычисленная стадия и даты этапов."""
    rows = conn.execute(
        """SELECT release_name, dev_start, dev_end, test_start, test_end,
                  rollout_date, release_stage, has_dates
             FROM releases
            WHERE has_dates = 1 AND release_stage IN ('1', '2')
            ORDER BY release_name"""
    ).fetchall()

    out = []
    for r in rows:
        rel = dict(r)
        st = stage_of(rel, today)
        if st is None:
            continue  # релиз уже прошёл накат — в отчёт не попадает
        rel["stage"] = st
        out.append(rel)
    # Порядок: сначала накат (ближе к финишу), затем тестирование, затем разработка.
    order = {name: i for i, name in enumerate(reversed(STAGE_NAMES))}
    out.sort(key=lambda r: (order.get(r["stage"], 9), r["release_name"]))
    return out


def release_tasks(conn: sqlite3.Connection, release: str) -> list[dict]:
    """Задачи релиза по пяти системам, дедуплицированные по номеру заявки.

    Одна заявка может стоять в релизе под несколькими проектами (разные
    project_code) — в отчёте она должна быть один раз. Берём версию с
    наибольшим task_id (самую свежую).
    """
    placeholders = ",".join("?" * len(TARGET_SYSTEMS))
    rows = conn.execute(
        f"""SELECT s.*, e.fact_dev
              FROM task_slices s
              LEFT JOIN task_effort_by_release e
                     ON e.request_code = s.request_code
                    AND e.release_name = s.release_name
             WHERE s.release_name = ?
               AND s.system_name IN ({placeholders})
               AND s.task_id = (SELECT MAX(t2.task_id) FROM task_slices t2
                                 WHERE t2.request_code = s.request_code
                                   AND t2.project_code = s.project_code)
             ORDER BY s.request_code""",
        (release, *TARGET_SYSTEMS),
    ).fetchall()

    best: dict[str, dict] = {}
    for r in rows:
        t = dict(r)
        prev = best.get(t["request_code"])
        if prev is None or (t.get("task_id") or 0) > (prev.get("task_id") or 0):
            best[t["request_code"]] = t
    return [best[c] for c in sorted(best)]


def _status_block(counter) -> list[str]:
    """Статусы в порядке STATUS_ORDER (как в контрольных точках), как есть."""
    parts = []
    for status in MILESTONE_STATUS_ORDER:
        n = counter.get(status, 0)
        if n:
            parts.append(f"{status}: {n}")
    # Незнакомые статусы — в конце, чтобы новые не терялись.
    known = set(MILESTONE_STATUS_ORDER)
    for status, n in sorted(counter.items()):
        if status not in known:
            parts.append(f"{status}: {n}")
    return parts


def build_release_block(conn: sqlite3.Connection, rel: dict, today: date) -> str | None:
    """Блок отчёта по одному релизу: статистика + задачи по статусам."""
    tasks = release_tasks(conn, rel["release_name"])
    if not tasks:
        return None

    plan = sum(t.get("development_plan") or 0 for t in tasks)
    fact = sum(t.get("fact_dev") or 0 for t in tasks)

    # Разбивка по системам — в порядке TARGET_SYSTEMS (стабильно между прогонами).
    by_sys: dict[str, int] = {}
    for t in tasks:
        by_sys[t["system_name"]] = by_sys.get(t["system_name"], 0) + 1

    lines = [
        "─────────────────────────────",
        f"🔄 **{rel['release_name']}** — стадия: *{rel['stage']}*",
        f"Разработка: {_fmt_d(rel.get('dev_start'))} → {_fmt_d(rel.get('dev_end'))} | "
        f"Тест: {_fmt_d(rel.get('test_start'))} → {_fmt_d(rel.get('test_end'))} | "
        f"Накат: {_fmt_d(rel.get('rollout_date'))}",
        "",
        f"📦 Задач (5 систем): {len(tasks)}",
    ]
    for sys_name in TARGET_SYSTEMS:
        if by_sys.get(sys_name):
            lines.append(f"   • {sys_name}: {by_sys[sys_name]}")
    lines.append(
        f"⏱ Часы разработки: план {num(plan)} / факт {num(fact)} ч"
        f"{labor_arrow(plan, fact)}"
    )

    counter: dict[str, int] = {}
    for t in tasks:
        st = (t.get("last_status") or "").strip()
        if st:
            counter[st] = counter.get(st, 0) + 1
    block = _status_block(counter)
    if block:
        lines += ["", "📊 Задачи по статусам:"] + block
    return "\n".join(lines)


def build_report(conn: sqlite3.Connection, today: date) -> str:
    """Полный текст отчёта по всем активным релизам."""
    releases = load_active_releases(conn, today)
    lines = [
        f"📈 **Недельный отчёт по релизам** — {today:%d.%m.%Y}",
        f"Активных релизов (стадии 1–2): {len(releases)}",
        f"Системы: {', '.join(TARGET_SYSTEMS)}",
    ]
    if not releases:
        lines.append("")
        lines.append("Активных релизов нет.")
        return "\n".join(lines)

    for rel in releases:
        block = build_release_block(conn, rel, today)
        if block:
            lines += ["", block]
    return "\n".join(lines)


async def send_to_me(parts: list[str]) -> None:
    """Отправляет части текста в личный чат пользователя в Delo Space."""
    from uuid import UUID

    from bot import get_bot
    from config import BOT_ID as CFG_BOT_ID

    b = get_bot()
    await b.startup()
    try:
        chat = UUID(MY_CHAT_ID)
        for part in parts:
            await b.send_message(
                bot_id=UUID(CFG_BOT_ID), chat_id=chat, body=part, wait_callback=False
            )
    finally:
        await b.shutdown()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="дата отчёта (ISO), по умолчанию сегодня")
    ap.add_argument("--dry-run", action="store_true", help="показать, не отправлять")
    args = ap.parse_args()

    if not PORTFOLIO_DB.exists():
        print(f"❌ Нет базы портфеля: {PORTFOLIO_DB}")
        return 1

    today = _d(args.date) or date.today()
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Еженедельный отчёт по релизам на {today}")

    conn = sqlite3.connect(f"file:{PORTFOLIO_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        text = build_report(conn, today)
    finally:
        conn.close()

    if args.dry_run:
        print("─" * 60)
        print(text)
        print("─" * 60)
        print(f"[dry-run] частей: {len(chunk(text, PART_LIMIT))}, символов: {len(text)}")
        return 0

    import asyncio

    parts = chunk(text, PART_LIMIT)
    asyncio.run(send_to_me(parts))
    print(f"✅ Отправлено в личный чат: частей {len(parts)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
