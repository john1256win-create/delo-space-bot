"""Форматирование сообщений для Delo Space (без HTML-тегов)."""
from datetime import datetime


def format_changes(new_rows: list[dict], removed_rows: list[dict]) -> str:
    """Форматирует список изменений — без HTML, только текст."""
    today = datetime.now().strftime("%d.%m.%Y")
    parts = [f"📢 Изменения на releases.1c.ru — {today}\n"]

    if new_rows:
        parts.append(f"🆕 Новые/обновлённые ({len(new_rows)}):")
        by_product = {}
        for row in new_rows:
            key = row["product"]
            if key not in by_product:
                by_product[key] = []
            by_product[key].append(row)

        for product, rows in sorted(by_product.items()):
            parts.append(f"\n{product}")
            for row in rows:
                ver = row["version"]
                ver_type = row["title"]
                date = row["date"]
                parts.append(f"  - {ver_type}: {ver} ({date})")

    if removed_rows:
        parts.append(f"\n🗑 Удалено ({len(removed_rows)}):")
        for row in removed_rows[:10]:
            parts.append(f"  - {row['product']} — {row['version']}")
        if len(removed_rows) > 10:
            parts.append(f"  ... и ещё {len(removed_rows) - 10}")

    return "\n".join(parts)


def format_changes_tg(new_rows: list[dict], removed_rows: list[dict]) -> str:
    """
    Форматирует изменения для Telegram (HTML-ссылки).
    Номер релиза — гиперссылка на страницу релиза.
    Нужен row['url'] для построения ссылки на версию.
    """
    from release_files import version_url

    today = datetime.now().strftime("%d.%m.%Y")
    parts = [f"<b>📢 Изменения на releases.1c.ru — {today}</b>\n"]

    if new_rows:
        parts.append(f"🆕 <b>Новые/обновлённые ({len(new_rows)}):</b>")
        by_product = {}
        for row in new_rows:
            key = row["product"]
            if key not in by_product:
                by_product[key] = []
            by_product[key].append(row)

        for product, rows in sorted(by_product.items()):
            parts.append(f"\n<b>{product}</b>")
            for row in rows:
                ver = row["version"]
                ver_type = row["title"]
                date = row["date"]
                vurl = version_url(row)
                if vurl:
                    # HTML-ссылка в Telegram
                    ver_link = f'<a href="{vurl}">{ver}</a>'
                else:
                    ver_link = ver
                parts.append(f"  - {ver_type}: {ver_link} ({date})")

    if removed_rows:
        parts.append(f"\n🗑 <b>Удалено ({len(removed_rows)}):</b>")
        for row in removed_rows[:10]:
            parts.append(f"  - {row['product']} — {row['version']}")
        if len(removed_rows) > 10:
            parts.append(f"  ... и ещё {len(removed_rows) - 10}")

    return "\n".join(parts)


def format_changes_links(new_rows: list[dict], removed_rows: list[dict]) -> str:
    """
    Форматирует изменения для Delo Space с markdown-гиперссылками на версии.
    Delo Space не рендерит HTML-теги, но поддерживает markdown-ссылки [текст](url).
    Номер релиза — кликабельная ссылка на страницу релиза.
    """
    from release_files import version_url

    today = datetime.now().strftime("%d.%m.%Y")
    parts = [f"📢 Изменения на releases.1c.ru — {today}\n"]

    if new_rows:
        parts.append(f"🆕 Новые/обновлённые ({len(new_rows)}):")
        by_product = {}
        for row in new_rows:
            key = row["product"]
            if key not in by_product:
                by_product[key] = []
            by_product[key].append(row)

        for product, rows in sorted(by_product.items()):
            parts.append(f"\n{product}")
            for row in rows:
                ver = row["version"]
                ver_type = row["title"]
                date = row["date"]
                vurl = version_url(row)
                if vurl:
                    # markdown-ссылка для Delo Space
                    ver_link = f"[{ver}]({vurl})"
                else:
                    ver_link = ver
                parts.append(f"  - {ver_type}: {ver_link} ({date})")

    if removed_rows:
        parts.append(f"\n🗑 Удалено ({len(removed_rows)}):")
        for row in removed_rows[:10]:
            parts.append(f"  - {row['product']} — {row['version']}")
        if len(removed_rows) > 10:
            parts.append(f"  ... и ещё {len(removed_rows) - 10}")

    return "\n".join(parts)


def format_full_report(all_rows: list[dict]) -> str:
    """Форматирует полную сводку по всем продуктам — без HTML."""
    today = datetime.now().strftime("%d.%m.%Y")
    parts = [f"📊 Полная сводка релизов 1С — {today}\n"]

    by_product = {}
    for row in all_rows:
        key = row["product"]
        if key not in by_product:
            by_product[key] = []
        by_product[key].append(row)

    for product in sorted(by_product.keys()):
        rows = by_product[product]
        parts.append(f"\n{product}")

        actual = [r for r in rows if r["title"] == "Актуальная"]
        if actual:
            versions = ", ".join(r["version"] for r in actual[:3])
            parts.append(f"  Актуальная: {versions}")

        planned = [r for r in rows if r["title"] == "Планируемая"]
        if planned:
            versions = ", ".join(r["version"] for r in planned[:3])
            parts.append(f"  Планируемая: {versions}")

    return "\n".join(parts)


def format_product_info(query: str, rows: list[dict]) -> str:
    """Форматирует информацию о найденном продукте — без HTML."""
    if not rows:
        return f"❌ Продукт '{query}' не найден"

    parts = [f"🔎 Результаты поиска: {query}\n"]

    by_product = {}
    for row in rows:
        key = row["product"]
        if key not in by_product:
            by_product[key] = []
        by_product[key].append(row)

    for product in sorted(by_product.keys()):
        prod_rows = by_product[product]
        parts.append(f"{product}")

        actual = [r for r in prod_rows if r["title"] == "Актуальная"]
        if actual:
            for r in actual:
                parts.append(f"  ✅ {r['version']} — {r['date']}")

        planned = [r for r in prod_rows if r["title"] == "Планируемая"]
        if planned:
            for r in planned:
                parts.append(f"  📅 {r['version']} — {r['date']}")

        parts.append("")

    return "\n".join(parts)


def format_error(message: str) -> str:
    """Форматирует сообщение об ошибке."""
    return f"❌ Ошибка\n{message}"


def format_success(message: str) -> str:
    """Форматирует успешное сообщение."""
    return f"✅ {message}"


def format_law_monitor_changes(new_rows: list[dict], removed_rows: list[dict]) -> str:
    """Форматирует изменения мониторинга законодательства v8.1c.ru/lawmonitor."""
    today = datetime.now().strftime("%d.%m.%Y")
    parts = [f"📜 Изменения в мониторинге законодательства — {today}\n"]

    if new_rows:
        parts.append(f"🆕 Новые записи ({len(new_rows)}):")
        for row in new_rows:
            product = row.get("product", "")
            status = row.get("status", "")
            details = row.get("details", "")
            parts.append(f"\n📋 {product}")
            parts.append(f"   {status}")
            if details:
                parts.append(f"   {details}")

    if removed_rows:
        parts.append(f"\n🗑 Удалено ({len(removed_rows)}):")
        for row in removed_rows[:10]:
            parts.append(f"  - {row.get('product', '')} ({row.get('status', '')})")
        if len(removed_rows) > 10:
            parts.append(f"  ... и ещё {len(removed_rows) - 10}")

    return "\n".join(parts)