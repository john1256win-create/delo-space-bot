"""
telegram_send.py — отправка сообщений и файлов в Telegram.

Временное переключение отправки с Delo Space на Telegram
для отладки механизма гиперссылок и вложений.
"""
import os
import requests
from pathlib import Path
from typing import Optional

# Читаем токен из ~/.hermes/.env (как в старом скрапере)
def _get_token() -> str:
    env_file = Path.home() / ".hermes" / ".env"
    env = {}
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    # Сначала TZ_BOT_TOKEN (My_TZ_Bot для группы Агентская)
    return env.get("TZ_BOT_TOKEN") or env.get("MY_TZ_BOT_TOKEN") or ""

TG_CHAT_ID = -1004362657732  # группа «Агентская» (как в старом скрапере)
TG_THREAD_ID = None  # если группа имеет топики — указать ID

def send_message(text: str, parse_mode: str = "HTML") -> bool:
    """Отправляет текстовое сообщение в Telegram."""
    token = _get_token()
    if not token:
        print("⚠ Нет TZ_BOT_TOKEN — отправка в Telegram пропущена")
        return False
    api = f"https://api.telegram.org/bot{token}"

    # Telegram-сообщение максимум 4096 символов
    max_len = 4000
    success = True
    for chunk in _chunks(text, max_len):
        data = {
            "chat_id": TG_CHAT_ID,
            "text": chunk,
            "parse_mode": parse_mode,
        }
        if TG_THREAD_ID:
            data["message_thread_id"] = TG_THREAD_ID
        try:
            r = requests.post(f"{api}/sendMessage", data=data, timeout=15)
            r.raise_for_status()
            resp = r.json()
            if not resp.get("ok"):
                print(f"❌ Telegram sendMessage: {resp.get('description', resp)}")
                success = False
        except Exception as e:
            print(f"❌ Telegram sendMessage error: {e}")
            success = False
    return success


def send_document(filepath: str, caption: Optional[str] = None) -> bool:
    """Отправляет файл в Telegram."""
    token = _get_token()
    if not token:
        print("⚠ Нет TZ_BOT_TOKEN — отправка файла пропущена")
        return False
    if not os.path.exists(filepath):
        print(f"❌ Файл не найден: {filepath}")
        return False
    api = f"https://api.telegram.org/bot{token}"

    data = {"chat_id": TG_CHAT_ID}
    if caption:
        data["caption"] = caption[:1024]  # лимит подписи 1024
    if TG_THREAD_ID:
        data["message_thread_id"] = TG_THREAD_ID

    try:
        with open(filepath, "rb") as f:
            r = requests.post(
                f"{api}/sendDocument",
                data=data,
                files={"document": f},
                timeout=60,
            )
        r.raise_for_status()
        resp = r.json()
        if resp.get("ok"):
            return True
        print(f"❌ Telegram sendDocument: {resp.get('description', resp)}")
        return False
    except Exception as e:
        print(f"❌ Telegram sendDocument error: {e}")
        return False


def _chunks(text: str, size: int) -> list[str]:
    """Разбивает текст на части по size символов."""
    if len(text) <= size:
        return [text]
    chunks = []
    while text:
        if len(text) <= size:
            chunks.append(text)
            break
        cut = size
        nl = text.rfind("\n", 0, size)
        if nl > 0:
            cut = nl
        chunks.append(text[:cut])
        text = text[cut:].lstrip()
    return chunks