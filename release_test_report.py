#!/usr/bin/env python3
"""
release_test_report.py — отчёт по задачам релиза, когда начинается период тестирования.

Задача (постановка пользователя): в день старта периода тестирования релиза
присылать в личный чат Delo Space список задач, которые НИЖЕ приоритетом чем
«Ожидает работу QA» (в системе нет статуса «Ожидает проверки QA» — порог берётся
по «Ожидает работу QA», индексу 20 в STATUS_ORDER фронтенда).

Формат строки задачи:
  Номер — название
  Разработчик: ФИО (разр. план / факт ч) со стрелкой
  Методолог: ФИО (мет. план / факт ч) со стрелкой
  Статус: <последний> | Макс.: <максимальный, если отличается>

Задачи сортируются по приоритету статуса (порядок из фильтра Web UI). Задачи
со статусом раньше «Готова к включению в релиз» собираются в один блок как код
(без нумерации и оформления) — там разметка не рендерится.

Данные — напрямую из SQLite портфеля (не через API: API может быть остановлен,
а отчёт должен уходить по расписанию). Порядок статусов парсится из
ReleaseTasksTable.tsx — единственный источник истины, чтобы не дублировать список.

Запуск:
  python3 release_test_report.py              # релизы, у которых тест стартует сегодня
  python3 release_test_report.py --date 2026-09-18
  python3 release_test_report.py --release 1С_TK_v35.35   # внепланово по релизу
  python3 release_test_report.py --dry-run    # показать, не отправлять
"""
import argparse
import json
import math
import re
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# ── Источники данных ─────────────────────────────────────────────────────
PORTFOLIO_DB = Path(
    "/Users/salnikov/Проект с ИИ/Новые проекты/Проекты Web UI/"
    "project-web-ui/runtime/project-web-ui.sqlite3"
)
STATUS_ORDER_TSX = Path(
    "/Users/salnikov/Проект с ИИ/Новые проекты/Проекты Web UI/"
    "project-web-ui/frontend/src/pages/ReleaseTasksTable.tsx"
)
SENT_LOG = HERE / "data" / "test_reports_sent.json"

# Запасной список, если tsx недоступен (порядок утверждён пользователем).
FALLBACK_STATUS_ORDER = [
    "Зарегистрирована", "В ожидании, линия 1", "В работе (1 линия)",
    "В ожидании, линия 2", "В работе (2 линия)", "Ожидание уточнения от автора",
    "Ожидает решения/события", "На согласовании", "Не согласована", "Согласована",
    "Ожидает консультации программиста", "Ожидает подготовки ТЗ", "Подготовка ТЗ",
    "Консультация программиста", "Ожидает проверки ТЗ", "На проверке ТЗ",
    "Установка ответственного программиста для консультации",
    "Готова к включению в релиз", "Ожидает разработки", "В разработке",
    "Ожидает работу QA", "В работе QA", "Ожидает проверки методологом",
    "На проверке у методологов", "На тестировании у ключевых пользователей",
    "На загрузке расширения", "Ожидает обновления системы", "Закрыта",
    "Не актуальна", "Отменена", "Закупка выполнена", "В снабжении",
    "Ожидает проверки 1 линией", "На проверке у 1 линии",
]

# Порог: «Ожидает работу QA» — отправляем задачи со статусом РАНЬШЕ него.
THRESHOLD_STATUS = "Ожидает работу QA"

# Задачи со статусом РАНЬШЕ этого (включая более ранние этапы) выводятся одним
# блоком как код, без нумерации и оформления.
CODE_BLOCK_STATUS = "Готова к включению в релиз"

# Личный чат пользователя в Delo Space (HUID 481cea26-4e3f-55d5-8260-ad05f1d3dc6c,
# получен через bot.personal_chat; бот не может создать чат первым).
MY_CHAT_ID = "9a9ad162-34d1-0fd2-3e2c-37656966b502"


def load_status_order() -> list[str]:
    """Порядок статусов из фронтенда (fallback — встроенный список)."""
    try:
        src = STATUS_ORDER_TSX.read_text(encoding="utf-8")
        m = re.search(r"const STATUS_ORDER:\s*string\[\]\s*=\s*\[(.*?)\]", src, re.S)
        if m:
            order = re.findall(r"'([^']+)'", m.group(1))
            if order:
                return order
    except Exception as e:
        print(f"⚠ Не удалось прочитать STATUS_ORDER из tsx ({e}) — беру встроенный список")
    return FALLBACK_STATUS_ORDER


def num(v) -> str:
    """Округление вверх до 1 знака, как в UI (fmtN)."""
    if v is None:
        return "—"
    try:
        return f"{math.ceil(float(v) * 10) / 10:g}"
    except (TypeError, ValueError):
        return "—"


def norm(s) -> str:
    """Нормализует пробелы: сжимает повторные и убирает краевые.

    В исходных названиях заявок встречаются двойные пробелы и ведущий пробел
    (« Ограничение доступа…», «…из Excel»:  добавить проверку»). В отчёте
    приводим к одному пробелу — читаемость важнее дословности выгрузки.
    """
    return re.sub(r"\s+", " ", s or "").strip()


def labor_arrow(plan, fact) -> str:
    """Стрелка-индикатор рядом с парой план/факт по часам.

    План больше факта → зелёная стрелка вниз (🟢⬇️, часы сэкономлены).
    План меньше факта → красная стрелка вверх (🔴⬆️, перерасход).
    Равны или данных нет → без стрелки.

    В эмодзи нет односимвольной «зелёной стрелки вниз»/«красной вверх»,
    поэтому цвет задаётся кружком, направление — стрелкой.
    """
    if plan is None or fact is None:
        return ""
    try:
        p, f = float(plan), float(fact)
    except (TypeError, ValueError):
        return ""
    if f < p:
        return " 🟢⬇️"
    if f > p:
        return " 🔴⬆️"
    return ""


def load_sent() -> dict:
    if SENT_LOG.exists():
        try:
            return json.loads(SENT_LOG.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def mark_sent(release: str, on: str) -> None:
    SENT_LOG.parent.mkdir(parents=True, exist_ok=True)
    data = load_sent()
    data[release] = on
    SENT_LOG.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def build_report(conn: sqlite3.Connection, release: str, order: list[str]) -> str | None:
    """Формирует текст отчёта по релизу. None — если задач ниже порога нет."""
    idx = {s: i for i, s in enumerate(order)}
    cut = idx.get(THRESHOLD_STATUS)
    if cut is None:
        print(f"⚠ Порог «{THRESHOLD_STATUS}» не найден в STATUS_ORDER — прерываю")
        return None

    # Актуальные задачи релиза (последняя версия по (request_code, project_code)) —
    # та же логика, что в release_tasks() бэкенда. LEFT JOIN к look-трудозатратам:
    # fact_dev — часы разработки ИМЕННО в этом релизе (task_effort_by_release).
    tasks = conn.execute(
        """SELECT s.*, e.fact_dev AS fact_dev
           FROM task_slices s
           LEFT JOIN task_effort_by_release e
                  ON e.request_code = s.request_code
                 AND e.release_name = s.release_name
           WHERE s.release_name = ?
             AND s.task_id = (SELECT MAX(t2.task_id) FROM task_slices t2
                              WHERE t2.request_code = s.request_code
                                AND t2.project_code = s.project_code)
           ORDER BY s.request_code""",
        (release,),
    ).fetchall()

    # Статусы всех версий задач в релизе — для «максимального статуса».
    statuses: dict[tuple, set] = {}
    for sr in conn.execute(
        "SELECT request_code, project_code, last_status FROM task_slices WHERE release_name = ?",
        (release,),
    ):
        key = (sr["request_code"], sr["project_code"])
        statuses.setdefault(key, set())
        if sr["last_status"]:
            statuses[key].add(sr["last_status"])

    def max_status(t) -> str | None:
        """Статус с наибольшим индексом; None, если он совпадает с текущим."""
        key = (t["request_code"], t["project_code"])
        sts = statuses.get(key) or ([t["last_status"]] if t["last_status"] else [])
        best, best_i = None, -2
        for s in sts:
            i = idx.get(s, -1)
            if i > best_i:
                best, best_i = s, i
        return None if best == t["last_status"] else best

    selected = [t for t in tasks if idx.get(t["last_status"], -1) < cut]
    if not selected:
        return None

    # Сортировка по приоритету статуса (индекс в STATUS_ORDER), внутри статуса —
    # по номеру задачи, чтобы порядок был стабильным между прогонами.
    selected.sort(key=lambda t: (idx.get(t["last_status"], -1), t["request_code"]))

    # Задачи ниже статуса «Готова к включению в релиз» — в один блок как код.
    code_cut = idx.get(CODE_BLOCK_STATUS)
    if code_cut is None:
        print(f"⚠ Статус «{CODE_BLOCK_STATUS}» не найден в STATUS_ORDER")
        code_cut = -1

    lines = [
        f"Релиз **{release}** — 📝 стартует период тестирования",
        f"Задачи ниже приоритета «{THRESHOLD_STATUS}»: {len(selected)} из {len(tasks)}",
        "",
    ]

    def task_lines(t, pos: int) -> list[str]:
        """Строки одной задачи; pos — номер в отчёте (0 — без нумерации, код-блок)."""
        ms = max_status(t)
        out = []
        # 1) номер задачи — полужирный. Скобку НЕ экранируем: правило списка в
        # клиенте требует точку («[0-9]{1,9}\.»), поэтому «1)» списком не станет,
        # а обратные слэши клиент не разэкранирует — они видны в чате как есть.
        head = f"**{t['request_code']}** — {norm(t['request_name'])}"
        out.append(f"{pos}) {head}" if pos else head)
        # 2) Разработка: план — из задачи; факт — ЧАСЫ ЭТОГО РЕЛИЗА
        # (task_effort_by_release.fact_dev, как колонка «Разр» в Web UI).
        # Стрелка считается по этой же паре план/факт релиза.
        # «Всего» выносим за скобки — это общий факт по задаче (development_actual),
        # он показывает часы и в других релизах. Показываем, когда отличается
        # от факта релиза, чтобы не дублировать одно и то же число.
        plan, fact_rel = t["development_plan"], t["fact_dev"]
        dev = (
            f"Разработчик: {norm(t['programmer']) or '—'} "
            f"(разр. {num(plan)} / {num(fact_rel)} ч{labor_arrow(plan, fact_rel)})"
        )
        total = t["development_actual"]
        if total is not None and (fact_rel is None or round(total, 1) != round(fact_rel, 1)):
            dev += f" Всего {num(total)} ч"
        out.append(dev)
        # 3) Методология: per-release разбивки в данных НЕТ (look-файл содержит
        # только «Факт разработки»), поэтому план/факт берём из задачи целиком.
        out.append(
            f"Методолог: {norm(t['methodologist']) or '—'} "
            f"(мет. {num(t['methodology_plan'])} / {num(t['methodology_actual'])} ч"
            f"{labor_arrow(t['methodology_plan'], t['methodology_actual'])})"
        )
        # 4) статус — полужирный; 5) максимальный статус — курсивом
        tail = f"Статус: **{t['last_status'] or '—'}**"
        if ms:
            tail += f" | Макс.: *{ms}*"
        out.append(tail)
        return out

    in_code = [t for t in selected if idx.get(t["last_status"], -1) < code_cut]
    normal = [t for t in selected if idx.get(t["last_status"], -1) >= code_cut]

    if in_code:
        lines.append(f"Ниже статуса «{CODE_BLOCK_STATUS}» ({len(in_code)}) — как есть:")
        # Ограда из 4 бэктиков: внутри код-блока разметка не рендерится,
        # строки выводятся «как есть». Эмодзи-стрелки отображаются.
        lines.append("````")
        for t in in_code:
            lines.extend(task_lines(t, 0))
        lines.append("````")
        lines.append("")

    for i, t in enumerate(normal, 1):
        lines.extend(task_lines(t, i))
        lines.append("")

    projects = ", ".join(sorted({t["project_code"] for t in selected}))
    lines.append(f"Проекты: {projects}")
    return "\n".join(lines)


def releases_starting_today(conn: sqlite3.Connection, today: str) -> list[str]:
    """Релизы, у которых период тестирования начинается сегодня."""
    rows = conn.execute(
        """SELECT release_name FROM releases
           WHERE has_dates = 1 AND test_start = ?
           ORDER BY release_name""",
        (today,),
    ).fetchall()
    return [r["release_name"] for r in rows]


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


def chunk(text: str, size: int = 3500) -> list[str]:
    """Дробит текст на части ≤ size по границам строк.

    Строку длиннее size (в отчёте это может дать очень длинное название задачи)
    режем жёстко — иначе часть превысит лимит BotX и сообщение не отправится.
    Пустые части не возвращаются.

    Код-блок (ограда из 4 бэктиков) не разрывается: если граница дроби попадает
    внутрь блока, текущая часть закрывается оградой, а следующая открывается
    заново — иначе остаток блока отрендерится как обычный текст с сырыми «**»/«\\*».
    """
    FENCE = "````"

    def _flush(buf: str) -> tuple[str, str]:
        """Отдаёт готовую часть и затравку следующей, не разрывая код-блок.

        Если в buf осталась незакрытая ограда (мы внутри код-блока) — текущая
        часть закрывается оградой, а следующая начинается с открывающей.
        """
        if buf.count(FENCE) % 2:
            return (buf.rstrip() + "\n" + FENCE).rstrip(), FENCE + "\n"
        return buf.rstrip(), ""

    out: list[str] = []
    cur = ""
    for line in text.split("\n"):
        while len(line) > size:
            part, cur = _flush(cur)
            if part:
                out.append(part)
            out.append(line[:size])
            line = line[size:]
        if cur and len(cur) + len(line) + 1 > size:
            part, cur = _flush(cur)
            out.append(part)
        cur += line + "\n"
    if cur.strip():
        part, _ = _flush(cur)
        out.append(part)
    return [p for p in out if p.strip()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="дата старта теста (ISO), по умолчанию сегодня")
    ap.add_argument("--release", help="релиз вручную, вне расписания")
    ap.add_argument("--dry-run", action="store_true", help="показать, не отправлять")
    ap.add_argument("--force", action="store_true", help="игнорировать журнал отправок")
    args = ap.parse_args()

    if not PORTFOLIO_DB.exists():
        print(f"❌ Нет базы портфеля: {PORTFOLIO_DB}")
        return 1

    today = args.date or date.today().isoformat()
    order = load_status_order()
    conn = sqlite3.connect(f"file:{PORTFOLIO_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    if args.release:
        targets = [args.release]
        print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Ручной прогон по релизу {args.release}")
    else:
        targets = releases_starting_today(conn, today)
        print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Релизы со стартом теста {today}: {targets or 'нет'}")

    sent = load_sent()
    reports: list[str] = []
    for rel in targets:
        if not args.force and not args.release and sent.get(rel) == today:
            print(f"   ⏭ {rel}: отчёт уже отправлен {today} — пропускаю")
            continue
        text = build_report(conn, rel, order)
        if not text:
            print(f"   ✔ {rel}: задач ниже порога нет — отправлять нечего")
            continue
        reports.append(text)

    conn.close()
    if not reports:
        print("Итог: отправлять нечего")
        return 0

    full = "\n\n".join(reports)
    if args.dry_run:
        print("─" * 60)
        print(full)
        print("─" * 60)
        print(f"[dry-run] частей: {len(chunk(full))}, символов: {len(full)}")
        return 0

    import asyncio

    parts = chunk(full)
    asyncio.run(send_to_me(parts))
    for rel in targets:
        mark_sent(rel, today)
    print(f"✅ Отправлено в личный чат: частей {len(parts)}, релизов {len(reports)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
