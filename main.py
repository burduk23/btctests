#!/usr/bin/env python3
import logging
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from core.config import config, STATE_FILE
from bot.handlers import cmd_start, text_router, error_handler
from bot.callbacks import callback_router
from services.monitoring import monitor_loop
from api.server import start_web_server
from core.database import init_db

# ---------------- logging ----------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("btc_notify")

async def run_migration():
    if STATE_FILE.exists():
        logger.info("Обнаружен state.json, запуск миграции...")
        from scripts.migrate import migrate
        await migrate()

def main():
    logger.info("Инициализация бота...")
    builder = ApplicationBuilder().token(config.BOT_TOKEN)
    
    if config.PROXY_URL:
        logger.info(f"Использование SOCKS5 прокси: {config.PROXY_URL}")
        builder.proxy(config.PROXY_URL)
        builder.get_updates_proxy(config.PROXY_URL)
    elif config.MTPROTO_PROXY:
        logger.info(f"Обнаружен MTProto прокси: {config.MTPROTO_PROXY.ip}:{config.MTPROTO_PROXY.port}")
        logger.warning("python-telegram-bot использует HTTP API и не поддерживает MTProto прокси напрямую. "
                       "Рекомендуется использовать SOCKS5 (socks5.txt) или настроить HTTP-бридж.")
        
    app = builder.build()

    # Регистрация обработчиков
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(filters.ALL & (~filters.COMMAND), text_router))
    
    # Обработка ошибок
    app.add_error_handler(error_handler)

    async def _post_init(application):
        await init_db()
        await run_migration()
        
        logger.info("Запуск фонового мониторинга транзакций...")
        import asyncio
        from services.monitoring import monitor_loop, initialize_all_existing_addresses
        
        # Initialize history for existing addresses to avoid spam
        await initialize_all_existing_addresses(application)
        
        asyncio.create_task(monitor_loop(application))
        
        logger.info("Запуск веб-сервера для Mini App...")
        asyncio.create_task(start_web_server(application))

    app.post_init = _post_init

    logger.info("Бот запущен и ожидает сообщений...")
    app.run_polling()

if __name__ == "__main__":
    main()
