"""
Бот для Delo Space (eXpress) — мониторинг релизов 1С.
Копия функциональности Telegram-бота 1c_release_scraper.
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from http import HTTPStatus
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pybotx import (
    Bot,
    BotAccountWithSecret,
    HandlerCollector,
    IncomingMessage,
    ChatCreatedEvent,
    build_command_accepted_response,
)

from config import BOT_ID, CTS_URL, SECRET_KEY, CHAT_ID
from scraper import run_scrape, query_product
from formatter import format_changes, format_product_info, format_full_report
import message_store

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Коллектор обработчиков
collector = HandlerCollector()


# ═══════════════════════════════════════════════════════════
#  Обработчики команд
# ═══════════════════════════════════════════════════════════

@collector.command("/check", description="Проверить изменения релизов 1С")
async def check_handler(message: IncomingMessage, bot: Bot) -> None:
    """Запускает скрапер и отправляет изменения."""
    await bot.answer_message("🔍 Проверяю релизы 1С...")
    
    try:
        all_rows, new_rows, removed_rows = await asyncio.to_thread(run_scrape)
        
        if not new_rows and not removed_rows:
            await bot.answer_message("✅ Изменений на releases.1c.ru нет")
            return
        
        # Формируем сообщение
        text = format_changes(new_rows, removed_rows)
        
        # Отправляем частями (лимит Delo Space ~4000 символов)
        for chunk in split_message(text, 4000):
            await bot.answer_message(chunk)
            
    except Exception as e:
        logger.error(f"Ошибка при проверке релизов: {e}")
        await bot.answer_message(f"❌ Ошибка: {str(e)}")


@collector.command("/report", description="Полная сводка по всем продуктам")
async def report_handler(message: IncomingMessage, bot: Bot) -> None:
    """Отправляет полную сводку по всем продуктам."""
    await bot.answer_message("📊 Формирую полную сводку...")
    
    try:
        all_rows, _, _ = await asyncio.to_thread(run_scrape)
        text = format_full_report(all_rows)
        
        for chunk in split_message(text, 4000):
            await bot.answer_message(chunk)
            
    except Exception as e:
        logger.error(f"Ошибка при формировании сводки: {e}")
        await bot.answer_message(f"❌ Ошибка: {str(e)}")


@collector.command("/query", description="Найти продукт: /query ERP")
async def query_handler(message: IncomingMessage, bot: Bot) -> None:
    """Ищет продукт по названию."""
    # Извлекаем аргумент из сообщения
    args = message.body.strip() if message.body else ""
    
    if not args:
        await bot.answer_message("Использование: /query <название продукта>\nПример: /query ERP")
        return
    
    await bot.answer_message(f"🔎 Ищу: {args}...")
    
    try:
        rows = await asyncio.to_thread(query_product, args)
        
        if not rows:
            await bot.answer_message(f"❌ Продукт '{args}' не найден")
            return
        
        text = format_product_info(args, rows)
        
        for chunk in split_message(text, 4000):
            await bot.answer_message(chunk)
            
    except Exception as e:
        logger.error(f"Ошибка при поиске продукта: {e}")
        await bot.answer_message(f"❌ Ошибка: {str(e)}")


@collector.command("/help", description="Список команд")
async def help_handler(message: IncomingMessage, bot: Bot) -> None:
    """Показывает справку."""
    help_text = """
📋 Доступные команды:

/check — проверить изменения релизов
/report — полная сводка по всем продуктам
/query <название> — найти продукт
/chat_id — получить ID текущего чата
/help — эта справка

Примеры:
/query ERP
/query Документооборот
"""
    await bot.answer_message(help_text)


@collector.command("/chat_id", description="Получить ID текущего чата")
async def chat_id_handler(message: IncomingMessage, bot: Bot) -> None:
    """Возвращает chat_id текущего чата."""
    chat_id = message.chat.id
    chat_type = getattr(message.chat, "type", "unknown")
    await bot.answer_message(f"📌 Chat ID: `{chat_id}`\nТип чата: {chat_type}")


@collector.default_message_handler
async def store_all_messages(message: IncomingMessage, bot: Bot) -> None:
    """Сохраняет все входящие сообщения в SQLite-хранилище."""
    try:
        chat_id = str(message.chat.id)
        chat_type = getattr(message.chat, "type", "unknown")
        sender_id = str(getattr(message.sender, "huid", "") or getattr(message.sender, "udid", ""))
        sender_name = getattr(message.sender, "username", "") or ""
        body = message.body or ""

        saved = message_store.store_message(
            sync_id=str(message.sync_id),
            chat_id=chat_id,
            chat_type=chat_type,
            sender_id=sender_id,
            sender_name=sender_name,
            body=body,
            command=None,
        )
        if saved:
            logger.info(f"📥 Сообщение от {sender_name} сохранено (chat {chat_id})")
    except Exception as e:
        logger.error(f"Ошибка сохранения сообщения: {e}")


@collector.chat_created
async def chat_created_handler(event: ChatCreatedEvent, bot: Bot) -> None:
    """Обработчик создания чата с ботом."""
    logger.info(f"Создан чат с ботом: {event.chat_id}")


# ═══════════════════════════════════════════════════════════
#  Вспомогательные функции
# ═══════════════════════════════════════════════════════════

def split_message(text: str, max_len: int = 4000) -> list[str]:
    """Разбивает сообщение на части по max_len символов."""
    if len(text) <= max_len:
        return [text]
    
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        
        # Ищем последний перенос строки
        cut = text.rfind("\n", 0, max_len)
        if cut == -1:
            cut = text.rfind(" ", 0, max_len)
        if cut == -1:
            cut = max_len
        
        chunks.append(text[:cut])
        text = text[cut:].lstrip()
    
    return chunks


# ═══════════════════════════════════════════════════════════
#  Инициализация бота
# ═══════════════════════════════════════════════════════════

def create_bot() -> Bot:
    """Создаёт экземпляр бота с настройками."""
    if not BOT_ID or not CTS_URL or not SECRET_KEY:
        raise ValueError(
            "Не заданы BOT_ID, CTS_URL или SECRET_KEY в .env\n"
            "Получите их у администратора Delo Space после регистрации бота."
        )
    
    return Bot(
        collectors=[collector],
        bot_accounts=[
            BotAccountWithSecret(
                id=UUID(BOT_ID),
                cts_url=CTS_URL,
                secret_key=SECRET_KEY,
            ),
        ],
    )


# Создаём бота (лениво — если credentials заданы)
_bot: Bot | None = None
if BOT_ID and CTS_URL and SECRET_KEY:
    _bot = create_bot()
else:
    logger.warning(
        "⚠ BOT_ID / CTS_URL / SECRET_KEY не заданы в .env — "
        "бот не будет зарегистрирован. Заполни .env и перезапусти."
    )


def get_bot() -> Bot:
    """Возвращает экземпляр бота или поднимает ошибку."""
    if _bot is None:
        raise RuntimeError("Бот не инициализирован — заполни .env")
    return _bot


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Управление жизненным циклом: startup/shutdown бота."""
    if _bot:
        await _bot.startup()
    yield
    if _bot:
        await _bot.shutdown()


# FastAPI приложение
app = FastAPI(title="1C Releases Bot for Delo Space", lifespan=lifespan)


@app.post("/command")
async def command_handler(request: Request) -> JSONResponse:
    """Эндпоинт для команд от BotX."""
    try:
        b = get_bot()
        b.async_execute_raw_bot_command(
            await request.json(),
            request_headers=request.headers,
        )
        return JSONResponse(
            build_command_accepted_response(),
            status_code=HTTPStatus.ACCEPTED,
        )
    except Exception as e:
        logger.error(f"Ошибка обработки команды: {e}")
        return JSONResponse(
            {"error": str(e)},
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
        )


@app.get("/status")
async def status_handler(request: Request) -> JSONResponse:
    """Эндпоинт для проверки статуса бота."""
    b = get_bot()
    status = await b.raw_get_status(
        dict(request.query_params),
        request_headers=request.headers,
    )
    return JSONResponse(status)


@app.get("/messages")
async def list_messages_handler(chat_id: str = "", limit: int = 100) -> JSONResponse:
    """Возвращает сохранённые сообщения из хранилища (для чтения с ПК via VPN)."""
    messages = message_store.list_messages(
        chat_id=chat_id or None,
        limit=min(limit, 500),
    )
    return JSONResponse({
        "total": message_store.count_messages(),
        "count": len(messages),
        "messages": messages,
    })


@app.get("/messages/{message_id}")
async def get_message_handler(message_id: int) -> JSONResponse:
    """Возвращает одно сообщение по ID."""
    msg = message_store.get_message(message_id)
    if msg is None:
        return JSONResponse({"error": "Message not found"}, status_code=404)
    return JSONResponse(msg)


@app.post("/notification/callback")
async def callback_handler(request: Request) -> JSONResponse:
    """Эндпоинт для callback'ов от асинхронных операций."""
    b = get_bot()
    await b.set_raw_botx_method_result(
        await request.json(),
        verify_request=False,
    )
    return JSONResponse(
        build_command_accepted_response(),
        status_code=HTTPStatus.ACCEPTED,
    )


# ═══════════════════════════════════════════════════════════
#  Запуск
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
