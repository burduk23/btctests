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
    API_BASE: str = "https://api.blockcypher.com/v1/btc/main"
    API_TOKEN: str
    ADMIN_CHAT_ID: str = ADMIN_ID_DEFAULT
    WEB_PORT: int = 8080
    WEBAPP_URL: str = ""

def load_config() -> ConfigModel:
    if not CONFIG_FILE.exists():
        raise SystemExit("config.json не найден — заполните и перезапустите.")
    try:
        data = json.loads(CONFIG_FILE.read_text())
        return ConfigModel(**data)
    except Exception as e:
        raise SystemExit(f"Ошибка при загрузке config.json: {e}")

config = load_config()
