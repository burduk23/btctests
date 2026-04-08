import secrets
from typing import Optional, List
from core.state import Group, Address, UserData

def mk_group(name: str) -> Group:
    return {"id": secrets.token_hex(6), "name": name.strip(), "addresses": []} # type: ignore

def mk_address(addr: str) -> Address:
    return {"id": secrets.token_hex(6), "addr": addr.strip(), "notify_disabled": False}

def find_group(user_data: UserData, gid: str) -> Optional[Group]:
    for g in user_data.get("groups", []):
        if g.get("id") == gid:
            return g
    return None

def find_address(group: Group, aid: str) -> Optional[Address]:
    for a in group.get("addresses", []):
        if a.get("id") == aid:
            return a
    return None
