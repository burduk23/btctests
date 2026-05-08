import json
import logging
from typing import Dict, List, Optional, TypedDict, Any
from .config import STATE_FILE, config

logger = logging.getLogger("btc_notify")

class Address(TypedDict):
    id: str
    addr: str
    notify_disabled: bool
    confirmations: int

class Group(TypedDict):
    id: str
    name: str
    addresses: List[Address]

class UserData(TypedDict, total=False):
    groups: List[Group]
    messages: List[Dict[str, Any]]
    username: Optional[str]
    first_name: Optional[str]

class UnconfirmedTx(TypedDict):
    addr: str
    amount_btc: float
    amount_usd: float

class StateData(TypedDict):
    users: Dict[str, UserData]
    unconfirmed: Dict[str, UnconfirmedTx]
    notified_confirmed: Dict[str, Any]
    admins: List[str]

def load_state() -> StateData:
    default_state: StateData = {"users": {}, "unconfirmed": {}, "notified_confirmed": {}, "admins": []}
    if not STATE_FILE.exists():
        return default_state
    try:
        data = json.loads(STATE_FILE.read_text())
        if "groups" in data and "users" not in data:
            admin_id = str(config.ADMIN_CHAT_ID)
            new_state = {"users": {}, "unconfirmed": data.get("unconfirmed", {}), "notified_confirmed": data.get("notified_confirmed", {}), "admins": []}
            if admin_id:
                new_state["users"][admin_id] = {"groups": data.get("groups", [])}
            logger.info(f"Выполнена миграция базы данных для админа {admin_id}")
            save_state(new_state) # type: ignore
            return new_state # type: ignore
        
        for key in default_state:
            if key not in data:
                data[key] = default_state[key]
        
        # Ensure confirmations field exists in all addresses
        for user_data in data.get("users", {}).values():
            for group in user_data.get("groups", []):
                for addr in group.get("addresses", []):
                    if "confirmations" not in addr:
                        addr["confirmations"] = 1
        
        # Migrate notified_confirmed to new structure: txid -> { uid: [milestones] }
        notified = data.get("notified_confirmed", {})
        migrated = False
        for txid, val in list(notified.items()):
            if val is True:
                # Legacy: notified everyone. We don't know who, so we use a special key or just leave it.
                # For now, let's convert to an empty dict or keep as is if we handle it in process_tx.
                # But the task says it SHOULD be Dict[txid, Dict[uid, List[milestones]]].
                # If it's True, we can't easily map to UIDs. 
                # Let's use a special key "__all__" or just skip migration for True and handle it in process_tx.
                pass
            elif isinstance(val, list):
                # Legacy: list of UIDs notified for target.
                new_val = {}
                for uid in val:
                    new_val[str(uid)] = ["target"]
                notified[txid] = new_val
                migrated = True
        
        if migrated:
            data["notified_confirmed"] = notified
                        
        return data # type: ignore
    except Exception as e:
        logger.error(f"Ошибка при загрузке state.json: {e}")
        return default_state

def save_state(state: StateData):
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    except Exception as e:
        logger.error(f"Ошибка при сохранении state.json: {e}")

def is_main_admin(uid: str) -> bool:
    return uid == str(config.ADMIN_CHAT_ID)

def get_all_admins(state: StateData) -> List[str]:
    main_admin = str(config.ADMIN_CHAT_ID)
    admins = state.get("admins", [])
    if main_admin not in admins:
        return [main_admin] + admins
    return admins

def is_admin(uid: str, state: StateData) -> bool:
    return uid in get_all_admins(state)
