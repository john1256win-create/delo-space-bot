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
    load_status_order,
    norm,
    num,
)

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


def num_ru(v) -> str:
    """Округление вверх до 1 знака + русский десятичный разделитель (запятая)."""
    return num(v).replace(".", ",")


def _status_line(last: str, mx: str | None) -> str:
    """Строка статуса: текущий | максимальный (если отличается)."""
    line = f"Статус: **{last or '—'}**"
    if mx:
        line += f" | Макс.: *{mx}*"
    return line


def build_release_block(conn: sqlite3.Connection, rel: dict, today: date) -> list[str]:
    """Сообщение(я) по одному релизу: статистика релиза + расшифровка задач.

    Возвращает список частей: обычно одна, но если релиз не влезает в лимит BotX,
    текст делится по границам блоков «Методолог - …» (задача не разрывается).
    """
    tasks = release_tasks(conn, rel["release_name"])
    if not tasks:
        return []

    plan = sum(t.get("development_plan") or 0 for t in tasks)
    fact = sum(t.get("fact_dev") or 0 for t in tasks)

    # Разбивка по системам — в порядке TARGET_SYSTEMS (стабильно между прогонами).
    by_sys: dict[str, int] = {}
    for t in tasks:
        by_sys[t["system_name"]] = by_sys.get(t["system_name"], 0) + 1

    header = [
        f"📈 **{rel['release_name']}** — стадия: *{rel['stage']}*",
        f"Разработка: {_fmt_d(rel.get('dev_start'))} → {_fmt_d(rel.get('dev_end'))} | "
        f"Тест: {_fmt_d(rel.get('test_start'))} → {_fmt_d(rel.get('test_end'))} | "
        f"Накат: {_fmt_d(rel.get('rollout_date'))}",
        f"📦 Задач (5 систем): {len(tasks)}",
    ]
    for sys_name in TARGET_SYSTEMS:
        if by_sys.get(sys_name):
            # Без отступа пробелами: клиент Delo Space ведущие пробелы съедает,
            # отступ не отобразится, а двойной пробел после «•» попадёт в текст.
            header.append(f"• {sys_name}: {by_sys[sys_name]}")
    header.append(
        f"⏱ Часы разработки: план {num_ru(plan)} / факт {num_ru(fact)} ч"
        f"{labor_arrow(plan, fact)}"
    )

    groups = build_task_groups(conn, rel)
    return split_release_message(header, groups)


def build_task_groups(conn: sqlite3.Connection, rel: dict) -> list[list[str]]:
    """Блоки задач, сгруппированные по методологу (каждый блок — список строк).

    Возвращает СПИСОК блоков (не плоский список): по одному на методолога, начиная
    со строки «Методолог - ФИО:» и её задачами. Такая структура нужна, чтобы делить
    большое сообщение только по границам методологов, не разрывая задачу посередине.
    """
    tasks = release_tasks(conn, rel["release_name"])
    if not tasks:
        return []

    order = load_status_order()
    idx = {s: i for i, s in enumerate(order)}
    st_map: dict[str, set] = {}
    for sr in conn.execute(
        "SELECT request_code, last_status FROM task_slices WHERE release_name = ?",
        (rel["release_name"],),
    ):
        st = (sr["last_status"] or "").strip()
        if st:
            st_map.setdefault(sr["request_code"], set()).add(st)

    def max_status(code: str, last: str) -> str | None:
        sts = st_map.get(code) or ({last} if last else set())
        best, best_i = None, -2
        for s in sts:
            i = idx.get(s, -1)
            if i > best_i:
                best, best_i = s, i
        return None if best == last else best

    groups: dict[str, list[dict]] = {}
    for t in tasks:
        groups.setdefault((t.get("methodologist") or "").strip(), []).append(t)

    # Методологи по алфавиту; «без методолога» — в конце.
    names = sorted(groups, key=lambda n: (not n, n))

    out: list[list[str]] = []
    for name in names:
        # Заголовок группы: пиктограмма 🧠 + весь текст полужирным (требование пользователя).
        label = f"Методолог - {name}:" if name else "Методолог - (не указан):"
        block = [f"🧠 **{label}**"]
        items = sorted(groups[name], key=lambda t: t["request_code"])
        for i, t in enumerate(items, 1):
            plan, fact_rel = t.get("development_plan"), t.get("fact_dev")
            total = t.get("development_actual")
            last = (t.get("last_status") or "").strip()
            block.append(f"{i}) **{t['request_code']}** — {norm(t['request_name'])}")
            dev = (
                f"Разработчик: {norm(t.get('programmer')) or '—'} "
                f"(разр. {num_ru(plan)} / {num_ru(fact_rel)} ч)"
                f"{labor_arrow(plan, fact_rel)}"
            )
            # «Всего» — общий факт по задаче; показываем, когда он ОСМЫСЛЕН:
            # есть значение И отличается от факта релиза. При fact_dev=None (часов
            # по релизу в look-выгрузке нет) «Всего 0 ч» было бы шумом.
            if total is not None and float(total) > 0 and (
                fact_rel is None or round(float(total), 1) != round(float(fact_rel), 1)
            ):
                dev += f" Всего {num_ru(total)} ч"
            block.append(dev)
            block.append(
                f"Методолог: (мет. {num_ru(t.get('methodology_plan'))} / "
                f"{num_ru(t.get('methodology_actual'))} ч)"
            )
            block.append(_status_line(last, max_status(t["request_code"], last)))
        out.append(block)
    return out


def build_report(conn: sqlite3.Connection, today: date) -> list[str]:
    """Отчёт по активным релизам: ОДИН релиз = ОДНО сообщение (или несколько частей)."""
    releases = load_active_releases(conn, today)
    if not releases:
        return [
            f"📈 **Недельный отчёт по релизам** — {today:%d.%m.%Y}\n"
            f"Активных релизов (стадии 1–2) нет."
        ]
    out: list[str] = []
    for rel in releases:
        out.extend(build_release_block(conn, rel, today))
    return out


def split_release_message(header: list[str], groups: list[list[str]],
                          limit: int = PART_LIMIT) -> list[str]:
    """Делит сообщение релиза по границам блоков «Методолог - …».

    Задача никогда не разрывается посередине: режем только между методологами.
    Если один блок методолога сам не влезает в лимит — режем его по задачам
    (`chunk`), чтобы сообщение вообще отправилось (лимит BotX ~4000).
    В частях-продолжениях заголовок повторяется с пометкой «продолжение».
    """
    head = "\n".join(header)
    tail_mark = "\n(продолжение)"
    # Первая строка — название релиза; её же повторяем в продолжениях.
    first_line = header[0]

    out: list[str] = []
    cur = head
    for g in groups:
        block = "\n".join(g)
        if len(cur) + 1 + len(block) <= limit:
            cur += "\n" + block
            continue
        if cur.strip():
            out.append(cur)
        if len(block) + len(first_line) + len(tail_mark) + 2 > limit:
            # Блок методолога сам слишком велик — режем по задачам.
            for i, piece in enumerate(chunk(block, limit - len(first_line) - len(tail_mark) - 2)):
                pref = first_line + (tail_mark if (i or out) else "")
                out.append(f"{pref}\n{piece}")
            cur = ""
        else:
            cur = first_line + tail_mark + "\n" + block
    if cur.strip():
        out.append(cur)
    return [p for p in out if p.strip()]


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
        messages = build_report(conn, today)
    finally:
        conn.close()

    # Уже готовые сообщения (build_report сам делит по границам методологов).
    # Повторно дробить нельзя: часть ровно в PART_LIMIT символов chunk() разрежет
    # и оставит крошечный хвост-огрызок. Делим только то, что реально превышает лимит.
    parts: list[str] = []
    for msg in messages:
        if len(msg) <= PART_LIMIT:
            parts.append(msg)
        else:
            parts.extend(chunk(msg, PART_LIMIT))

    if args.dry_run:
        for i, p in enumerate(parts, 1):
            print("─" * 60)
            print(f"— сообщение {i}/{len(parts)} ({len(p)} символов) —")
            print(p)
        print("─" * 60)
        print(f"[dry-run] сообщений: {len(parts)}, символов: {sum(len(p) for p in parts)}")
        return 0

    import asyncio

    asyncio.run(send_to_me(parts))
    print(f"✅ Отправлено в личный чат: сообщений {len(parts)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
