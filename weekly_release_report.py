#!/usr/bin/env python3
"""
weekly_release_report.py — отчёт по активным релизам (понедельники + смена этапа).

Постановка пользователя: присылать в личный чат отчёт по релизам, которые
находятся в стадии от 1 до 2 (с начала разработки до даты наката включительно).
В начале отчёта — статистика релиза и стадия (разработка / тестирование / накат),
затем — расшифровка задач, сгруппированная по методологу.

Отправляется ДВАЖДЫ по разным поводам:
  1) каждый понедельник в 09:00 — сводка по всем активным релизам;
  2) в день смены этапа релиза (Начало разработки / Начало тестирования / Накат) —
     отчёт по этому релизу с пометкой о смене стадии.
Если понедельник и дата смены этапа совпали — по релизу уходит ОДНО сообщение
(повод один, дедупликация по паре «релиз + дата», журнал data/weekly_reports_sent.json).

Поэтому launchd запускает скрипт ЕЖЕДНЕВНО в 09:00, а решение «слать или нет»
и дедупликацию принимает сам скрипт — два отдельных агента на один отчёт дали бы
гонку и двойную отправку.

В отчёт включаются ТОЛЬКО задачи по пяти системам:
  1С: БП КОРП SASCO, 1С: ERP ГП, 1С: БП/Рускон РК, 1С: УХ ТК, 1С: БП (УХ) УКД

Стадия вычисляется по датам релиза (надёжнее, чем поле release_stage):
  today <= dev_end                    → «разработка»
  dev_end < today <= test_end         → «тестирование»
  test_end < today <= rollout_date    → «накат»

Данные — напрямую из SQLite портфеля (как в release_test_report.py): API может
быть остановлен, а отчёт должен уходить по расписанию.

Запуск:
  python3 weekly_release_report.py               # что положено на сегодня
  python3 weekly_release_report.py --dry-run     # показать, не отправлять
  python3 weekly_release_report.py --date 2026-09-29   # проверить другую дату
  python3 weekly_release_report.py --force       # игнорировать журнал отправок
  python3 weekly_release_report.py --chat-id <UUID>    # другой получатель
"""
import argparse
import json
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# Переиспользуем инфраструктуру существующего отчёта: порядок статусов,
# округление часов, нормализация пробелов, стрелки план/факт и дробление текста.
from release_test_report import (  # noqa: E402
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

# Стадии релиза: порядок подписей (накат ближе к финишу — он в начале отчёта).
STAGE_NAMES = ("разработка", "тестирование", "накат")

# ── Поводы отправки ────────────────────────────────────────────────────────
# Смена этапа релиза: подпись для сообщения → поле с датой начала этапа
# в таблице releases. Порядок = хронология этапа.
MILESTONES = (
    ("Начало разработки", "dev_start"),
    ("Начало тестирования", "test_start"),
    ("Накат", "rollout_date"),
)

# Получатель по умолчанию. Требование пользователя: отчёт приходит Марии Иванцевой
# (личный чат 1C_INFO_BOT, HUID 7b5b… — чат создан ею самой).
DEFAULT_CHAT_ID = "bb5766ee-85e5-016f-1902-fe182ae43e05"

# Журнал отправок: ключ «релиз|дата» → отчёта по этому релизу в этот день не будет
# повторно. Нужен, чтобы понедельник и смена этапа в один день не дали два отчёта.
SENT_LOG = HERE / "data" / "weekly_reports_sent.json"

# Хранить журнал за последние N дней (иначе файл растёт бесконечно).
SENT_LOG_KEEP_DAYS = 60

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
    """Активные релизы: окно «от начала разработки до даты наката включительно».

    ⚠️ Отбор идёт по ДАТАМ, а НЕ по полю `release_stage`. Поле отстаёт: у релиза
    в день «Начала разработки» оно ещё `'0'` (`1С_TK_v36.36`, `ERP_GP_v65.70`),
    поэтому фильтр `release_stage IN ('1','2')` молча съедал бы повод «Начало
    разработки» — триггер не сработал бы никогда. Формулировка пользователя
    «от 1 до 2 = с начала разработки до даты наката включительно» описывает
    именно диапазон дат.

    Условие: `dev_start <= today <= rollout_date` (обе даты заполнены).
    """
    rows = conn.execute(
        """SELECT release_name, dev_start, dev_end, test_start, test_end,
                  rollout_date, release_stage, has_dates
             FROM releases
            WHERE has_dates = 1
            ORDER BY release_name"""
    ).fetchall()

    out = []
    for r in rows:
        rel = dict(r)
        start = _d(rel.get("dev_start"))
        finish = _d(rel.get("rollout_date"))
        if not start or not finish or not (start <= today <= finish):
            continue  # окно ещё не открылось либо накат уже прошёл
        rel["stage"] = stage_of(rel, today) or "разработка"
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


def build_release_block(conn: sqlite3.Connection, rel: dict, today: date,
                        reasons: list[str] | None = None) -> list[str]:
    """Сообщение(я) по одному релизу: статистика релиза + расшифровка задач.

    `reasons` — поводы отправки. Если среди них есть смена этапа, добавляется
    строка с пометкой, чтобы получатель видел, ПОЧЕМУ пришёл отчёт.
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

    header = [f"📈 **{rel['release_name']}** — стадия: *{rel['stage']}*"]
    # Пометка о смене этапа — сразу после заголовка, до статистики.
    for reason in (reasons or []):
        if reason.startswith("смена этапа: "):
            header.append(f"🔔 **{reason.split(': ', 1)[1]}** — сегодня")
    header += [
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


async def send_to_me(parts: list[str], chat_id: str = DEFAULT_CHAT_ID) -> None:
    """Отправляет части текста в личный чат получателя в Delo Space.

    `chat_id` — личный чат BotX (по умолчанию Мария Иванцева).
    """
    from uuid import UUID

    from bot import get_bot
    from config import BOT_ID as CFG_BOT_ID

    b = get_bot()
    await b.startup()
    try:
        chat = UUID(chat_id)
        for i, part in enumerate(parts, 1):
            # send_message возвращает UUID sync_id — печатаем, чтобы доставку
            # можно было подтвердить через get_message_status(sync_id=…).
            sync_id = await b.send_message(
                bot_id=UUID(CFG_BOT_ID), chat_id=chat, body=part, wait_callback=False
            )
            print(f"  часть {i}/{len(parts)} → sync_id {sync_id} ({len(part)} символов)")
    finally:
        await b.shutdown()


def milestones_on(rel: dict, today: date) -> list[str]:
    """Этапы, которые начинаются у релиза ИМЕННО сегодня.

    Возвращает подписи из MILESTONES (может быть несколько, если даты совпали).
    """
    out = []
    for label, field in MILESTONES:
        if _d(rel.get(field)) == today:
            out.append(label)
    return out


def load_sent() -> dict:
    """Журнал отправок: {«релиз|YYYY-MM-DD»: iso-время отправки}."""
    if SENT_LOG.exists():
        try:
            return json.loads(SENT_LOG.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _prune_sent(data: dict, today: date) -> dict:
    """Убирает из журнала записи старше SENT_LOG_KEEP_DAYS — файл не растёт вечно."""
    cut = today - timedelta(days=SENT_LOG_KEEP_DAYS)
    keep = {}
    for key, when in data.items():
        # Ключ: «релиз|YYYY-MM-DD» — дату берём из него, а не из значения.
        ds = key.rsplit("|", 1)[-1]
        try:
            d = date.fromisoformat(ds)
        except ValueError:
            continue
        if d >= cut:
            keep[key] = when
    return keep


def mark_sent(releases: list[str], today: date) -> None:
    """Отмечает, что отчёты по этим релизам за сегодня уже отправлены."""
    data = _prune_sent(load_sent(), today)
    stamp = datetime.now().isoformat(timespec="seconds")
    for rel in releases:
        data[f"{rel}|{today.isoformat()}"] = stamp
    SENT_LOG.parent.mkdir(parents=True, exist_ok=True)
    SENT_LOG.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )


def decide(conn: sqlite3.Connection, today: date) -> list[dict]:
    """Что и по какому поводу отправлять сегодня.

    Понедельник → все активные релизы (повод «еженедельный отчёт»).
    День смены этапа → соответствующий релиз (повод «смена этапа»).
    Пересечение схлопывается: у релиза один элемент с обоими поводами,
    поэтому дублирующего отчёта не будет.

    Возвращает список dict: {rel, reasons: [str, ...]}.
    """
    chosen: dict[str, dict] = {}

    def add(rel: dict, reason: str) -> None:
        item = chosen.setdefault(rel["release_name"], {"rel": rel, "reasons": []})
        if reason not in item["reasons"]:
            item["reasons"].append(reason)

    if today.weekday() == 0:  # 0 = понедельник
        for rel in load_active_releases(conn, today):
            add(rel, "еженедельный отчёт")

    for rel in load_active_releases(conn, today):
        for ms in milestones_on(rel, today):
            add(rel, f"смена этапа: {ms}")

    # Порядок: сначала релиз со сменой этапа (событие), затем остальные;
    # внутри — по названию релиза, чтобы порядок был стабильным.
    out = list(chosen.values())
    out.sort(key=lambda it: (not any("смена" in r for r in it["reasons"]),
                             it["rel"]["release_name"]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="дата отчёта (ISO), по умолчанию сегодня")
    ap.add_argument("--dry-run", action="store_true", help="показать, не отправлять")
    ap.add_argument("--force", action="store_true",
                    help="игнорировать журнал отправок (в т.ч. внеповодный прогон)")
    ap.add_argument("--all", action="store_true",
                    help="слать по всем активным релизам, даже без повода")
    ap.add_argument("--chat-id", default=DEFAULT_CHAT_ID,
                    help="chat_id личного чата получателя (по умолчанию Мария Иванцева)")
    args = ap.parse_args()

    if not PORTFOLIO_DB.exists():
        print(f"❌ Нет базы портфеля: {PORTFOLIO_DB}")
        return 1

    today = _d(args.date) or date.today()
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Отчёт по релизам на {today} "
          f"({'понедельник' if today.weekday() == 0 else 'не понедельник'})")

    conn = sqlite3.connect(f"file:{PORTFOLIO_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        # Поводы на сегодня: понедельник и/или смена этапа у конкретного релиза.
        items = decide(conn, today)
        if args.all:
            # Внеповодный прогон (для теста/ручной проверки) — все активные релизы.
            items = [{"rel": r, "reasons": ["ручной прогон"]}
                     for r in load_active_releases(conn, today)]

        if not args.force:
            sent = load_sent()
            fresh: list[dict] = []
            for it in items:
                name = it["rel"]["release_name"]
                if f"{name}|{today.isoformat()}" in sent:
                    print(f"⏭ {name}: отчёт за {today} уже отправлен — пропускаю")
                else:
                    fresh.append(it)
            items = fresh

        if not items:
            print("ℹ️ Поводов для отправки нет (ни понедельник, ни смена этапа) — молчу")
            return 0

        print("Поводы: " + "; ".join(
            f"{it['rel']['release_name']} → {', '.join(it['reasons'])}" for it in items
        ))

        messages: list[str] = []
        for it in items:
            messages.extend(build_release_block(conn, it["rel"], today, it["reasons"]))
    finally:
        conn.close()

    # Уже готовые сообщения (build_release_block сам делит по границам методологов).
    # Повторно дробить нельзя: часть ровно в PART_LIMIT символов chunk() разрежет
    # и оставит крошечный хвост-огрызок. Делим только то, что реально превышает лимит.
    parts: list[str] = []
    for msg in messages:
        if len(msg) <= PART_LIMIT:
            parts.append(msg)
        else:
            parts.extend(chunk(msg, PART_LIMIT))

    if not parts:
        print("ℹ️ По отобранным релизам нет задач по пяти системам — отправлять нечего")
        return 0

    if args.dry_run:
        for i, p in enumerate(parts, 1):
            print("─" * 60)
            print(f"— сообщение {i}/{len(parts)} ({len(p)} символов) —")
            print(p)
        print("─" * 60)
        print(f"[dry-run] сообщений: {len(parts)}, символов: {sum(len(p) for p in parts)}")
        return 0

    import asyncio

    asyncio.run(send_to_me(parts, args.chat_id))
    # Журнал пишем только после успешной отправки — иначе при сбое отчёт
    # потерялся бы навсегда (дедупликация съела бы следующий прогон).
    mark_sent([it["rel"]["release_name"] for it in items], today)
    print(f"✅ Отправлено в личный чат {args.chat_id}: сообщений {len(parts)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
