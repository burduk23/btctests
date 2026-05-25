import json
import os
from pathlib import Path
from pydantic import BaseModel
import logging
from dotenv import load_dotenv

from typing import Optional

logger = logging.getLogger("btc_notify")

# Загружаем переменные окружения из .env
load_dotenv()

BASE_DIR = Path(__file__).parent.parent
CONFIG_FILE = BASE_DIR / "config.json"
STATE_FILE = BASE_DIR / "state.json"
ADMIN_ID_DEFAULT = "5381999598"

class MTProtoProxy(BaseModel):
    ip: str
    port: int
    secret: str

class ConfigModel(BaseModel):
    BOT_TOKEN: str
    POLL_INTERVAL: int = 20
    API_BASE: str = "https://mempool.space/api"
    ADMIN_CHAT_ID: str = ADMIN_ID_DEFAULT
    WEB_PORT: int = 8080
    WEBAPP_URL: str = ""
    PROXY_URL: str = ""
    MTPROTO_PROXY: Optional[MTProtoProxy] = None

def parse_proxy_string(content: str) -> str:
    if not content:
        return ""
    if "://" in content:
        return content
    
    parts = content.split(':')
    if len(parts) == 4:
        ip, port, user, password = parts
        return f"socks5h://{user}:{password}@{ip}:{port}"
    elif len(parts) == 2:
        ip, port = parts
        return f"socks5h://{ip}:{port}"
    return content

def get_proxy_url() -> str:
    # 1. Сначала проверяем socks5.txt
    proxy_file = BASE_DIR / "socks5.txt"
    if proxy_file.exists():
        try:
            content = proxy_file.read_text().strip()
            return parse_proxy_string(content)
        except Exception as e:
            logger.error(f"Ошибка при чтении socks5.txt: {e}")
    
    # 2. Затем проверяем переменную окружения PROXY (старый формат)
    env_proxy = os.getenv("PROXY")
    if env_proxy:
        return parse_proxy_string(env_proxy)
        
    return ""

def get_mtproto_proxy() -> Optional[MTProtoProxy]:
    proxy_file = BASE_DIR / "MTProto.txt"
    if proxy_file.exists():
        try:
            content = proxy_file.read_text().strip()
            if content:
                parts = content.split(':')
                if len(parts) == 3:
                    ip, port, secret = parts
                    return MTProtoProxy(ip=ip, port=int(port), secret=secret)
        except Exception as e:
            logger.error(f"Ошибка при чтении MTProto.txt: {e}")
    return None

def load_config() -> ConfigModel:
    data = {}
    
    # 1. Пытаемся загрузить из config.json
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text())
        except Exception as e:
            logger.error(f"Ошибка при чтении config.json: {e}")

    # 2. Перекрываем переменными окружения
    env_mapping = {
        "BOT_TOKEN": "BOT_TOKEN",
        "POLL_INTERVAL": "POLL_INTERVAL",
        "API_BASE": "API_BASE",
        "ADMIN_CHAT_ID": "ADMIN_CHAT_ID",
        "WEB_PORT": "WEB_PORT",
        "WEBAPP_URL": "WEBAPP_URL",
        "PROXY_URL": "PROXY_URL",
    }

    for model_key, env_key in env_mapping.items():
        env_val = os.getenv(env_key)
        if env_val is not None:
            if model_key in ["POLL_INTERVAL", "WEB_PORT"]:
                try:
                    data[model_key] = int(env_val)
                except ValueError:
                    logger.warning(f"Некорректное значение для {env_key}: {env_val}. Используется значение по умолчанию или из config.json")
            else:
                data[model_key] = env_val

    if not data.get("BOT_TOKEN"):
        raise SystemExit("BOT_TOKEN не найден ни в config.json, ни в .env — заполните и перезапустите.")

    try:
        conf = ConfigModel(**data)
        if not conf.PROXY_URL:
            conf.PROXY_URL = get_proxy_url()
        else:
            # Если PROXY_URL задан строкой без протокола, пробуем распарсить
            conf.PROXY_URL = parse_proxy_string(conf.PROXY_URL)
            
        if not conf.MTPROTO_PROXY:
            conf.MTPROTO_PROXY = get_mtproto_proxy()
        return conf
    except Exception as e:
        raise SystemExit(f"Ошибка при валидации конфигурации: {e}")

config = load_config()
