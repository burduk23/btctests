# BTC Monitor Bot

Telegram-бот для мониторинга входящих транзакций в сети Bitcoin. Бот автоматически отслеживает добавленные адреса и отправляет уведомления о новых транзакциях (unconfirmed) и подтверждениях в реальном времени.

## Особенности
- **Мониторинг в реальном времени**: Основной канал связи — WebSocket API (**mempool.space**). Бот мгновенно узнает о событиях в сети.
- **Высокая производительность**: Параллельный опрос адресов с использованием `asyncio.gather` и ограничением конкурентности (Semaphore) обеспечивает быструю работу даже при большом количестве отслеживаемых кошельков.
- **Метки времени**: Уведомления содержат точное время первого появления транзакции в сети (`first_seen`), что позволяет лучше контролировать поступление средств.
- **Оптимизация трафика**: HTTP-запросы к API (поллинг) используются **только как резервный канал** при обрыве WebSocket-соединения.
- **Proxy только для Telegram**: Поддержка SOCKS5 прокси (с авторизацией) для обхода блокировок Telegram API. Мониторинг сети Bitcoin и курсов валют работает напрямую.
- **Никаких лишних API**: Удалена зависимость от CoinGecko. Высота блока и курс BTC берутся напрямую из mempool.space.
- **Настройка подтверждений**: Уведомления при 0 (unconfirmed), 1 (если цель > 1) и целевом количестве подтверждений (настраивается индивидуально для каждого адреса). Интеллектуальная система фильтрации предотвращает повторные уведомления и исключает "прыжки" через этапы подтверждения.
- **Надежная инициализация**: Исправлен баг инициализации — теперь бот мгновенно уведомляет об активных транзакциях сразу после добавления нового адреса.
- **Telegram Mini App**: Современный веб-интерфейс для управления адресами и админ-панель (стиль Glassmorphism).
- **Встроенный чат**: Прямая связь с администратором через Mini App.

## Установка и запуск (Ubuntu / Root)

Бот должен быть расположен в директории `/root/btctests`. Все команды ниже выполняются от пользователя **root**.

1. **Подготовка директории и кода**:
   ```bash
   cd /root
   git clone https://github.com/ваш-репозиторий/btctests.git
   cd btctests
   ```

2. **Создание окружения**:
   ```bash
   python3 -m venv venv
   ./venv/bin/pip install -r requirements.txt
   ```

3. **Настройка конфигурации (.env)**:
   Создайте файл настроек в папке бота:
   ```bash
   nano /root/btctests/.env
   ```
   **Пример содержимого .env**:
   ```env
   # Токен вашего бота
   BOT_TOKEN=8602458971:AAEAq69BTZmFJXlfjpY6Bk3HItURCwrmz4I
   
   # Ваш Telegram ID (для доступа к админ-панели)
   ADMIN_CHAT_ID=5381999598
   
   # Прокси (только для Telegram Bot API)
   # Формат: IP:PORT:USER:PASS или IP:PORT (SOCKS5)
   PROXY=163.198.212.183:8000:1hmsGF:NTk265
   
   # Настройки веб-сервера (для Mini App)
   WEB_PORT=8080
   WEBAPP_URL=https://ваш-домен.com/
   ```

4. **Автозапуск бота (Systemd)**:
   Для того чтобы бот работал постоянно и запускался после перезагрузки:
   ```bash
   nano /etc/systemd/system/btctests.service
   ```
   Вставьте этот текст (пути уже настроены для `/root/btctests`):
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
   Активируйте сервис:
   ```bash
   systemctl daemon-reload
   systemctl enable --now btctests.service
   ```

5. **Настройка Cloudflare Tunnel (HTTPS для Mini App)**:
   Cloudflare Tunnel позволяет безопасно вывести порт 8080 в интернет с SSL сертификатом.

   **Шаг 1. Установка бинарного файла**:
   Мы скачиваем файл в `/usr/local/bin`, чтобы команда `cloudflared` была доступна глобально в системе, независимо от того, в какой папке вы находитесь.
   ```bash
   wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -O /usr/local/bin/cloudflared
   chmod +x /usr/local/bin/cloudflared
   ```

   **Шаг 2. Авторизация**:
   ```bash
   cloudflared tunnel login
   ```
   *(Перейдите по ссылке в консоли и выберите ваш домен)*.

   **Шаг 3. Создание туннеля**:
   ```bash
   cloudflared tunnel create btcbot
   ```
   Запомните ID туннеля (длинный код из букв и цифр).

   **Шаг 4. Настройка DNS**:
   ```bash
   cloudflared tunnel route dns btcbot ваш-домен.com
   ```

   **Шаг 5. Редактирование конфига Cloudflare**:
   Создайте папку для конфига и откройте редактор:
   ```bash
   mkdir -p /etc/cloudflared
   nano /etc/cloudflared/config.yml
   ```
   **Важно: Вставьте этот текст, заменив `ID_ТУННЕЛЯ` и `ваш-домен.com`**:
   ```yaml
   tunnel: btcbot
   # Путь к файлу ключей (при работе от root он будет таким)
   credentials-file: /root/.cloudflared/ID_ТУННЕЛЯ.json

   ingress:
     - hostname: ваш-домен.com
       service: http://127.0.0.1:8080
     - service: http_status:404
   ```

   **Шаг 6. Запуск туннеля как системного сервиса**:
   ```bash
   cloudflared service install
   systemctl enable --now cloudflared
   ```

## Структура данных
Бот использует **SQLite** (`data.db`). Все состояния транзакций и настройки пользователей хранятся в БД. Бот автоматически мигрирует данные при первом запуске.

## Использование
- Введите `/start` в боте.
- Используйте кнопку "Открыть Mini App" для управления.
- Если бот был заблокирован пользователем (`Forbidden`), флаг `bot_blocked` сбросится автоматически, как только пользователь напишет боту.
