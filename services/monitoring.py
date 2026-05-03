import asyncio
import httpx
import logging
import json
import aiohttp
import time
from core.config import config
from core.state import load_state, save_state, get_all_admins

logger = logging.getLogger("btc_notify")

async def get_btc_price():
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd")
            if r.status_code == 200:
                return r.json()["bitcoin"]["usd"]
    except:
        pass
    return 65000

async def get_tip_height():
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get("https://mempool.space/api/blocks/tip/height")
            if r.status_code == 200:
                return int(r.text)
    except:
        pass
    return 0

async def send_notification(app, chat_id, text):
    try:
        await app.bot.send_message(chat_id=int(chat_id), text=text, parse_mode="Markdown")
    except Exception as e:
        logger.debug(f"Не удалось отправить уведомление {chat_id}: {e}")

def get_addr_to_subs(state):
    addr_to_subs = {}
    for uid, udata in state.get("users", {}).items():
        for group in udata.get("groups", []):
            for a_entry in group.get("addresses", []):
                addr = a_entry.get("addr")
                if addr:
                    if addr not in addr_to_subs:
                        addr_to_subs[addr] = []
                    addr_to_subs[addr].append({
                        "uid": uid,
                        "group_name": group["name"],
                        "disabled": a_entry.get("notify_disabled", False),
                        "confirmations": a_entry.get("confirmations", 1)
                    })
    return addr_to_subs

async def process_tx(app, state, tx, addr, subs, btc_price, tip_height):
    txid = tx.get("txid")
    if not txid: return False
    
    status = tx.get("status", {})
    confirmed = status.get("confirmed", False)
    block_height = status.get("block_height", 0)
    confs = (tip_height - block_height + 1) if confirmed and tip_height and block_height else (1 if confirmed else 0)
    
    changed = False
    
    # Get notified UIDs for this txid
    notified_data = state.setdefault("notified_confirmed", {}).get(txid)
    if notified_data is True:
        return False # Already notified for everyone (legacy)
    
    notified_uids = notified_data if isinstance(notified_data, list) else []
    
    # 1. Check for confirmed notification
    if confirmed:
        notified_now = False
        for sub in subs:
            uid = sub["uid"]
            if sub.get("disabled") or uid in notified_uids:
                continue
                
            target = sub.get("confirmations", 1)
            if confs >= target:
                group_name = sub["group_name"]
                amount_sat = 0
                for vout in tx.get("vout", []):
                    if vout.get("scriptpubkey_address") == addr:
                        amount_sat += vout.get("value", 0)
                
                if amount_sat > 0:
                    amount_btc = amount_sat / 1e8
                    amount_usd = round(amount_btc * btc_price, 2)
                    
                    text = (f"✅ Транзакция получила {confs}+ подтверждение\n"
                            f"————————————\n"
                            f"**{group_name}**\n"
                            f"`{addr}`\n"
                            f"————————————\n"
                            f"💰 Сумма: `{amount_btc:.8f}` BTC | `${amount_usd}`\n"
                            f"————————————\n"
                            f"📍 https://blockchair.com/bitcoin/transaction/{txid}")
                    await send_notification(app, uid, text)
                    notified_uids.append(uid)
                    notified_now = True
        
        if notified_now:
            state["notified_confirmed"][txid] = notified_uids
            state.get("unconfirmed", {}).pop(txid, None)
            changed = True

    # 2. Check for unconfirmed notification
    elif not confirmed and txid not in state.get("unconfirmed", {}) and not notified_uids:
        should_notify_unconfirmed = any(sub.get("confirmations", 1) == 1 for sub in subs if not sub.get("disabled"))
        
        if should_notify_unconfirmed:
            amount_sat = 0
            for vout in tx.get("vout", []):
                if vout.get("scriptpubkey_address") == addr:
                    amount_sat += vout.get("value", 0)
            
            if amount_sat > 0:
                amount_btc = amount_sat / 1e8
                amount_usd = round(amount_btc * btc_price, 2)
                
                for sub in subs:
                    if sub.get("disabled") or sub.get("confirmations", 1) != 1:
                        continue
                    uid = sub["uid"]
                    group_name = sub["group_name"]
                    
                    text = (f"🔔 Новая входящая транзакция (unconfirmed)\n"
                            f"————————————\n"
                            f"**{group_name}**\n"
                            f"`{addr}`\n"
                            f"————————————\n"
                            f"💰 Сумма: `{amount_btc:.8f}` BTC | `${amount_usd}`\n"
                            f"————————————\n"
                            f"📍 https://blockchair.com/bitcoin/transaction/{txid}")
                    await send_notification(app, uid, text)
                
                state.setdefault("unconfirmed", {})[txid] = {
                    "addr": addr,
                    "amount_btc": amount_btc,
                    "amount_usd": amount_usd
                }
                changed = True
                
    return changed

async def monitor_loop(app):
    poll_interval = config.POLL_INTERVAL
    ws_url = "wss://mempool.space/api/v1/ws"
    
    async def ws_handler():
        while True:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(ws_url) as ws:
                        logger.info("Mempool.space WS connected")
                        
                        state = load_state()
                        addr_to_subs = get_addr_to_subs(state)
                        if addr_to_subs:
                            await ws.send_json({"action": "want-address-tracking", "addresses": list(addr_to_subs.keys())})
                        await ws.send_json({"action": "want-blocks"})
                        
                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                data = json.loads(msg.data)
                                
                                txs = []
                                if "address-transaction" in data:
                                    txs = [data["address-transaction"]]
                                elif "address-transactions" in data:
                                    txs = data["address-transactions"]
                                
                                if txs or "block" in data:
                                    state = load_state()
                                    addr_to_subs = get_addr_to_subs(state)
                                    btc_price = await get_btc_price()
                                    tip_height = await get_tip_height()
                                    
                                    changed = False
                                    for tx in txs:
                                        for addr, subs in addr_to_subs.items():
                                            # Check if addr is in vout
                                            is_relevant = False
                                            for vout in tx.get("vout", []):
                                                if vout.get("scriptpubkey_address") == addr:
                                                    is_relevant = True
                                                    break
                                            if is_relevant:
                                                if await process_tx(app, state, tx, addr, subs, btc_price, tip_height):
                                                    changed = True
                                    
                                    if changed:
                                        save_state(state)
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
            except Exception as e:
                logger.error(f"WS Error: {e}")
            
            await asyncio.sleep(10)

    # Start WS handler in background
    asyncio.create_task(ws_handler())
    
    # Polling loop (Fallback and periodic check)
    async with httpx.AsyncClient(timeout=10.0) as client:
        while True:
            try:
                state = load_state()
                addr_to_subs = get_addr_to_subs(state)
                
                if addr_to_subs:
                    btc_price = await get_btc_price()
                    tip_height = await get_tip_height()
                    
                    changed = False
                    for addr, subs in addr_to_subs.items():
                        try:
                            r = await client.get(f"{config.API_BASE}/address/{addr}/txs")
                            if r.status_code == 200:
                                for tx in r.json():
                                    if await process_tx(app, state, tx, addr, subs, btc_price, tip_height):
                                        changed = True
                        except Exception as e:
                            logger.debug(f"Polling error for {addr}: {e}")
                    
                    if changed:
                        save_state(state)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"Error in polling loop: {e}")
            
            await asyncio.sleep(poll_interval)
