# BTC Monitor Bot

Telegram-бот для мониторинга входящих транзакций в сети Bitcoin. Бот автоматически отслеживает добавленные адреса и отправляет уведомления о новых транзакциях (unconfirmed) и подтверждениях в реальном времени.

## Особенности
- **Мониторинг в реальном времени**: Основной канал связи — WebSocket API (**mempool.space**). Бот мгновенно узнает о событиях в сети.
- **Оптимизация трафика**: HTTP-запросы к API (поллинг) используются **только как резервный канал** при обрыве WebSocket-соединения.
- **Proxy только для Telegram**: Поддержка SOCKS5 прокси (с авторизацией) для обхода блокировок Telegram API. Мониторинг сети Bitcoin и курсов валют работает напрямую.
- **Никаких лишних API**: Удалена зависимость от CoinGecko. Высота блока и курс BTC берутся напрямую из mempool.space.
- **Настройка подтверждений**: Уведомления при 0 (unconfirmed), 1 и целевом количестве подтверждений (настраивается индивидуально для каждого адреса).
- **Telegram Mini App**: Современный веб-интерфейс для управления адресами и админ-панель (стиль Glassmorphism).
- **Встроенный чат**: Прямая связь с администратором через Mini App.

## Установка и запуск (Ubuntu)

Бот должен быть расположен в директории `/root/btctests`.

1. Клонируйте репозиторий:
   ```bash
   cd /root
   git clone https://github.com/ваш-репозиторий/btctests.git
   cd btctests
   ```
2. Создайте виртуальное окружение и установите зависимости:
   ```bash
   python3 -m venv venv
   ./venv/bin/pip install -r requirements.txt
   ```

3. **Настройка конфигурации**:
   Создайте файл `.env` в директории `/root/btctests`.

   **Пример .env**:
   ```env
   # Обязательные параметры
   BOT_TOKEN=ВАШ_ТЕЛЕГРАМ_ТОКЕН
   ADMIN_CHAT_ID=5381999598
   
   # Прокси (только для Telegram Bot API)
   # Формат: IP:PORT:USER:PASS или IP:PORT
   PROXY=163.198.212.183:8000:1hmsGF:NTk265
   
   # Веб-сервер для Mini App
   WEB_PORT=8080
   WEBAPP_URL=https://ваш-домен.com/
   
   # Дополнительные настройки (по желанию)
   POLL_INTERVAL=20
   API_BASE=https://mempool.space/api
   ```

4. **Автозапуск через Systemd (Рекомендуется)**:
   Создайте файл сервиса:
   ```bash
   nano /etc/systemd/system/btctests.service
   ```
   Вставьте следующее содержимое:
   ```ini
   [Unit]
   Description=BTC Notification Bot and Web Server
   After=network.target

   [Service]
   Type=simple
   User=root
   WorkingDirectory=/root/btctests
   ExecStart=/root/btctests/venv/bin/python main.py
   Restart=always
   RestartSec=10
   Environment=PYTHONUNBUFFERED=1

   [Install]
   WantedBy=multi-user.target
   ```
   Запустите и включите сервис:
   ```bash
   systemctl daemon-reload
   systemctl enable --now btctests.service
   ```

5. **Настройка Cloudflare Tunnel для Mini App**:
   Для работы Mini App необходим HTTPS. Cloudflare Tunnel позволяет опубликовать локальный порт 8080 без открытия портов наружу.

   **Шаг 1. Установка и логин**:
   ```bash
   wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -O /usr/local/bin/cloudflared
   chmod +x /usr/local/bin/cloudflared
   cloudflared tunnel login
   ```

   **Шаг 2. Создание туннеля**:
   ```bash
   cloudflared tunnel create btcbot
   cloudflared tunnel route dns btcbot ваш-домен.com
   ```

   **Шаг 3. Конфигурация (/etc/cloudflared/config.yml)**:
   ```yaml
   tunnel: btcbot
   credentials-file: /root/.cloudflared/ВАШ_TUNNEL_ID.json

   ingress:
     - hostname: ваш-домен.com
       service: http://127.0.0.1:8080
     - service: http_status:404
   ```

   **Шаг 4. Установка как сервис**:
   ```bash
   cloudflared service install
   systemctl enable --now cloudflared
   ```

## Структура данных
Бот использует **SQLite** (`data.db`). Файл `notified_txs.json` больше не используется. Все состояния уведомлений и настройки пользователей хранятся в БД.

## Использование
- Введите `/start` в боте.
- Используйте Mini App для управления адресами.
- Если бот был заблокирован (`Forbidden`), просто напишите ему любое сообщение или нажмите `/start`, чтобы возобновить уведомления (флаг `bot_blocked` сбросится автоматически).
