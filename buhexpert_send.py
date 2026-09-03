"""
buhexpert_send.py — отправка семинаров БухЭксперт в Delo Space (канал «БухЭксперт»).

Содержит функции форматирования сообщений и отправки через того же бота.
"""
import asyncio
from datetime import date, datetime
from uuid import UUID

from buhexpert_scraper import get_credentials, login, _make_session, fetch_event_content

BOT_ID = "d2863b44-7aee-5a07-bc4c-9a6098b5696e"
CHAT_BUHEXPERT = UUID("52d96a19-199e-56b0-9222-a6c2bf7b940f")  # канал «БухЭксперт»

MONTHS_RU = ["", "январь", "февраль", "март", "апрель", "май", "июнь",
             "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]


def fmt_date(iso_date: str) -> str:
    """'2026-09-03' -> '03.09.2026'."""
    try:
        d = datetime.strptime(iso_date, "%Y-%m-%d")
        return d.strftime("%d.%m.%Y")
    except ValueError:
        return iso_date


def fmt_seminar(sem: dict, with_link: bool = True) -> str:
    """Форматирует один семинар: Дата - Время - Название (ссылка) - Лектор."""
    d = fmt_date(sem["date"])
    t = sem["time"]
    title = sem["title"]
    if with_link and sem["url"]:
        title = f"[{title}]({sem['url']})"
    lect = sem["lecturer"]
    return f"{d} - {t} - {title} - {lect}"


def fmt_announcement(seminars: list[dict], month_year_label: str) -> str:
    """Анонс событий на следующий месяц (15-го числа)."""
    lines = [f"📅 Анонс семинаров на {month_year_label}:\n"]
    if not seminars:
        lines.append("На текущий момент семинаров не запланировано.")
        return "\n".join(lines)
    for i, s in enumerate(seminars, 1):
        lines.append(f"{i}. {fmt_seminar(s)}")
    return "\n".join(lines)


def format_reminder(seminar: dict) -> str:
    """Напоминание о событии за 1 день."""
    return (f"⏰ Напоминание! Завтра семинар:\n{fmt_seminar(seminar)}")


async def send_message(body: str, bot=None, chat_id: UUID = CHAT_BUHEXPERT):
    """Отправляет текст в канал БухЭксперт. Если bot не передан — создаёт/закрывает."""
    from bot import get_bot
    own_bot = bot is None
    if own_bot:
        bot = get_bot()
        await bot.startup()
    try:
        chunk = body
        size = 4000
        while chunk:
            part = chunk[:size]
            if len(chunk) > size:
                cut = part.rfind("\n")
                if cut > 0:
                    part, chunk = chunk[:cut], chunk[cut:].lstrip()
                else:
                    chunk = chunk[size:].lstrip()
            else:
                chunk = ""
            await bot.send_message(bot_id=UUID(BOT_ID), chat_id=chat_id,
                                   body=part, wait_callback=False)
    finally:
        if own_bot:
            await bot.shutdown()


def get_event_content_for(seminar: dict) -> str:
    """Возвращает контент события (с авторизацией) или пустую строку."""
    s = _make_session()
    try:
        if not login(s):
            return ""
        return fetch_event_content(s, seminar["url"])
    except Exception:
        return ""
    finally:
        s.close()