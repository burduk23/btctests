import logging
from aiohttp import web
from core.config import config, BASE_DIR
from .routes import web_index, web_api_get, web_api_action

logger = logging.getLogger("btc_notify")

async def security_middleware(app, handler):
    async def middleware(request):
        # Защита загруженных изображений
        if request.path.startswith('/static/uploads/'):
            if 'tg_session' not in request.cookies:
                return web.Response(text="403 Forbidden: Authorized access only", status=403)
        
        # Защита исходного кода от прямого просмотра в браузере
        elif request.path.endswith('.js') or request.path.endswith('.css'):
            dest = request.headers.get('Sec-Fetch-Dest')
            # Если запрос идет как документ (прямой переход в браузере) - блокируем
            if dest == 'document':
                return web.Response(text="403 Forbidden: Direct access denied", status=403)
                
        return await handler(request)
    return middleware

async def start_web_server(app):
    webapp = web.Application(client_max_size=1024**2 * 10, middlewares=[security_middleware])
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
