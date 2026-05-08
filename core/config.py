import json
from pathlib import Path
from pydantic import BaseModel
import logging

logger = logging.getLogger("btc_notify")

BASE_DIR = Path(__file__).parent.parent
CONFIG_FILE = BASE_DIR / "config.json"
STATE_FILE = BASE_DIR / "state.json"
ADMIN_ID_DEFAULT = "5381999598"

class ConfigModel(BaseModel):
    BOT_TOKEN: str
    POLL_INTERVAL: int = 20
    API_BASE: str = "https://blockstream.info/api"
    ADMIN_CHAT_ID: str = ADMIN_ID_DEFAULT
    WEB_PORT: int = 8080
    WEBAPP_URL: str = ""
    PROXY_URL: str = ""

def get_proxy_url() -> str:
    proxy_file = BASE_DIR / "socks5.txt"
    if proxy_file.exists():
        try:
            content = proxy_file.read_text().strip()
            if content:
                parts = content.split(':')
                if len(parts) == 4:
                    ip, port, user, password = parts
                    return f"socks5h://{user}:{password}@{ip}:{port}"
        except Exception as e:
            logger.error(f"Ошибка при чтении socks5.txt: {e}")
    return ""

def load_config() -> ConfigModel:
    if not CONFIG_FILE.exists():
        raise SystemExit("config.json не найден — заполните и перезапустите.")
    try:
        data = json.loads(CONFIG_FILE.read_text())
        conf = ConfigModel(**data)
        if not conf.PROXY_URL:
            conf.PROXY_URL = get_proxy_url()
        return conf
    except Exception as e:
        raise SystemExit(f"Ошибка при загрузке config.json: {e}")

config = load_config()
