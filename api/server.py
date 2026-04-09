import logging
from aiohttp import web
from core.config import config, BASE_DIR
from .routes import web_index, web_api_get, web_api_action

logger = logging.getLogger("btc_notify")

async def start_web_server(app):
    webapp = web.Application()
    webapp['bot_app'] = app
    webapp.router.add_get('/', web_index)
    webapp.router.add_post('/api/get', web_api_get)
    webapp.router.add_post('/api/action', web_api_action)
    
    # Раздача статики
    webapp.router.add_static('/static/', path=BASE_DIR / 'static', name='static')

    # Создание папки для загрузок если её нет
    (BASE_DIR / 'static' / 'uploads').mkdir(parents=True, exist_ok=True)

    runner = web.AppRunner(webapp)
    await runner.setup()
    port = int(config.WEB_PORT)
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"Web server started on port {port}")
