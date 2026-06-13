import asyncio
import httpx
from httpx import HTTPStatusError
import logging
import json
import aiohttp
import time
import os
from datetime import datetime, timezone
from telegram.error import Forbidden
from core.config import config
from core.state import (
    get_address_to_subs, get_notified_confs, get_notified_confs_batch,
    update_notified_confs, update_notified_confs_batch, update_user_blocked
)

logger = logging.getLogger("btc_notify")

async def get_mempool_stats():
    """Gets BTC price and tip height from mempool.space API."""
    stats = {"price": 0, "height": 0}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            async def get_height():
                r = await client.get("https://mempool.space/api/blocks/tip/height")
                if r.status_code == 200:
                    stats["height"] = int(r.text)
            
            async def get_price():
                r = await client.get("https://mempool.space/api/v1/prices")
                if r.status_code == 200:
                    stats["price"] = r.json().get("USD", 0)
            
            await asyncio.gather(get_height(), get_price())
    except Exception as e:
        logger.debug(f"Error fetching mempool stats: {e}")
    return stats

async def get_tip_height():
    # Deprecated in favor of get_mempool_stats, but keeping for compatibility if needed
    stats = await get_mempool_stats()
    return stats["height"]

async def send_notification(app, chat_id, text):
    try:
        # Уведомления через Telegram ВСЕГДА идут через прокси, если он задан в приложении (app.bot)
        # Здесь мы просто вызываем метод бота
        await app.bot.send_message(chat_id=int(chat_id), text=text, parse_mode="Markdown")
        return True
    except Forbidden:
        logger.error(f"Бот заблокирован пользователем {chat_id}. Отключаем уведомления.")
        await update_user_blocked(int(chat_id), True)
        return False
    except Exception as e:
        logger.error(f"Не удалось отправить уведомление {chat_id}: {e}")
        return False

async def initialize_address(addr, uid, target=1, group_name=None, app=None, client=None):
    """Initializes an address for a user, processing existing transactions."""
    should_close = False
    if client is None:
        client = httpx.AsyncClient(timeout=10.0)
        should_close = True
        
    try:
        # Get current stats for processing
        stats = await get_mempool_stats()
        btc_price = stats["price"]
        tip_height = stats["height"]

        for attempt in range(3):
            try:
                r = await client.get(f"{config.API_BASE}/address/{addr}/txs")
                r.raise_for_status()
                txs = r.json()
                uid_str = str(uid)
                
                # Batch fetch notified confs
                txids = [tx.get("txid") for tx in txs if tx.get("txid")]
                all_notified = await get_notified_confs_batch(txids)
                
                all_updates = []
                subs = None
                if group_name and app:
                    subs = [{
                        "uid": uid_str,
                        "group_name": group_name,
                        "confirmations": target,
                        "disabled": False,
                        "bot_blocked": False
                    }]

                for tx in txs:
                    txid = tx.get("txid")
                    if not txid: continue
                    
                    notified = all_notified.get(txid, {})
                    key = f"{uid_str}:{addr}"
                    
                    # If already notified (even legacy), skip
                    if key in notified or uid_str in notified:
                        continue
                    
                    if subs:
                        # Process transaction immediately (notifies if active, marks silently if old)
                        updates = await process_tx(app, tx, addr, subs, btc_price, tip_height, tx_notified=notified)
                        all_updates.extend(updates)
                    else:
                        # Fallback for global init or when no app/group_name provided
                        status = tx.get("status", {})
                        confirmed = status.get("confirmed", False)
                        confs = status.get("confirmations")
                        if confs is None:
                            block_height = status.get("block_height", 0)
                            if confirmed and tip_height and block_height:
                                confs = max(1, tip_height - block_height + 1)
                            else:
                                confs = 1 if confirmed else 0
                        
                        if confs >= target:
                            all_updates.append({"txid": txid, "uid": key, "milestone": "target"})
                        # If confs < target, we do nothing, so monitor loop will pick it up later
                
                if all_updates:
                    await update_notified_confs_batch(all_updates)
                return True
            except HTTPStatusError as e:
                if e.response.status_code == 429:
                    wait_time = (attempt + 1) * 2
                    logger.warning(f"Rate limit hit for {addr} during init, retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    raise
    except Exception as e:
        logger.error(f"Error initializing address {addr} for {uid}: {e}")
    finally:
        if should_close:
            await client.aclose()
    return False

async def initialize_all_existing_addresses(app):
    """Initializes all addresses in the database to prevent historical spam."""
    logger.info("Starting global address initialization...")
    addr_to_subs = await get_address_to_subs()
    
    semaphore = asyncio.Semaphore(5)
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        async def init_task(addr, subs):
            async with semaphore:
                for sub in subs:
                    await initialize_address(
                        addr, 
                        sub["uid"], 
                        target=sub["confirmations"], 
                        group_name=sub["group_name"], 
                        app=app, 
                        client=client
                    )

        tasks = [init_task(addr, subs) for addr, subs in addr_to_subs.items()]
        await asyncio.gather(*tasks)
        
    logger.info("Global address initialization complete.")

async def process_tx(app, tx, addr, subs, btc_price, tip_height, tx_notified=None):
    txid = tx.get("txid")
    if not txid: return []
    
    status = tx.get("status", {})
    confirmed = status.get("confirmed", False)
    
    # 1. Improved confirmation calculation
    confs = status.get("confirmations")
    if confs is None:
        block_height = status.get("block_height", 0)
        if confirmed and tip_height and block_height:
            confs = max(1, tip_height - block_height + 1)
        else:
            confs = 1 if confirmed else 0
            
    # 2. Extract time for enrichment
    timestamp = status.get("block_time") or tx.get("first_seen")
    time_str = ""
    if timestamp:
        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        time_str = dt.strftime("%H:%M:%S %d.%m.%Y UTC")
    
    if tx_notified is None:
        tx_notified = await get_notified_confs(txid)
    
    updates = []
    milestone_order = {"0": 0, "1": 1, "target": 2}
    
    for sub in subs:
        uid = str(sub["uid"])
        if sub.get("disabled") or sub.get("bot_blocked"):
            continue
            
        target = sub.get("confirmations", 1)
        key = f"{uid}:{addr}"
        user_milestones = tx_notified.get(key, [])
        
        # Fallback for migrated data (which only used uid)
        if not user_milestones and uid in tx_notified:
            user_milestones = tx_notified[uid]
            
        # Determine highest reached milestone
        reached_milestone = None
        if confs >= target:
            reached_milestone = "target"
        elif confs >= 1 and target > 1:
            reached_milestone = "1"
        elif confs == 0:
            reached_milestone = "0"
            
        if reached_milestone is None:
            continue
            
        # Check if already notified for this or higher milestone
        already_notified = False
        for m in user_milestones:
            if milestone_order.get(m, -1) >= milestone_order[reached_milestone]:
                already_notified = True
                break
        
        if not already_notified:
            # SAFETY CHECK: If this is the FIRST time we see this TX and it's already 
            # way past our target, just mark it as notified silently to avoid spamming history.
            is_new_discovery = len(user_milestones) == 0
            if is_new_discovery and reached_milestone == "target" and confs > (target + 10):
                logger.info(f"Silently marking historical TX {txid} for {uid}")
                updates.append({"txid": txid, "uid": key, "milestone": reached_milestone})
                # Update local state
                if key not in tx_notified: tx_notified[key] = []
                tx_notified[key].append(reached_milestone)
                continue

            # Notify!
            group_name = sub["group_name"]
            amount_sat = 0
            for vout in tx.get("vout", []):
                if vout.get("scriptpubkey_address") == addr:
                    amount_sat += vout.get("value", 0)
            
            if amount_sat > 0:
                amount_btc = amount_sat / 1e8
                amount_usd = round(amount_btc * btc_price, 2)
                
                if reached_milestone == "target":
                    title = f"✅ Транзакция подтверждена ({confs}/{target})"
                elif reached_milestone == "1":
                    title = f"ℹ️ Транзакция получила первое подтверждение (1/{target})"
                else: # "0"
                    title = f"🔔 Новая входящая транзакция (unconfirmed)"
                
                time_info = f"🕒 Время: `{time_str}`\n" if time_str else ""
                
                text = (f"{title}\n"
                        f"————————————\n"
                        f"**{group_name}**\n"
                        f"`{addr}`\n"
                        f"————————————\n"
                        f"💰 Сумма: `{amount_btc:.8f}` BTC | `${amount_usd}`\n"
                        f"{time_info}"
                        f"————————————\n"
                        f"📍 https://blockchair.com/bitcoin/transaction/{txid}")
                
                if await send_notification(app, uid, text):
                    updates.append({"txid": txid, "uid": key, "milestone": reached_milestone})
                    # Update local state to prevent duplicate in same loop
                    if key not in tx_notified: tx_notified[key] = []
                    tx_notified[key].append(reached_milestone)

    return updates

async def monitor_loop(app):
    poll_interval = config.POLL_INTERVAL
    ws_url = "wss://mempool.space/api/v1/ws"
    
    state = {
        "btc_price": 65000,
        "tip_height": 0,
        "last_price_update": 0,
        "ws_connected": False
    }
    
    async def update_price_if_needed():
        now = time.time()
        if now - state["last_price_update"] > 300: # 5 minutes
            stats = await get_mempool_stats()
            if stats["price"]:
                state["btc_price"] = stats["price"]
                state["last_price_update"] = now
            if stats["height"]:
                state["tip_height"] = stats["height"]
    
    semaphore = asyncio.Semaphore(5)

    async def poll_address(client, addr, subs):
        async with semaphore:
            for attempt in range(3):
                try:
                    r = await client.get(f"https://mempool.space/api/address/{addr}/txs")
                    r.raise_for_status()
                    txs = r.json()
                    
                    # Batch fetch notified confs
                    txids = [tx.get("txid") for tx in txs if tx.get("txid")]
                    all_notified = await get_notified_confs_batch(txids)
                    
                    all_updates = []
                    for tx in txs:
                        txid = tx.get("txid")
                        tx_notified = all_notified.get(txid, {})
                        updates = await process_tx(app, tx, addr, subs, state["btc_price"], state["tip_height"], tx_notified=tx_notified)
                        all_updates.extend(updates)
                    
                    if all_updates:
                        await update_notified_confs_batch(all_updates)
                    break
                except HTTPStatusError as e:
                    if e.response.status_code == 429:
                        wait_time = (attempt + 1) * 2
                        logger.warning(f"Rate limit hit for {addr}, retrying in {wait_time}s...")
                        await asyncio.sleep(wait_time)
                    else:
                        logger.error(f"HTTP error polling {addr}: {e}")
                        break
                except Exception as e:
                    logger.debug(f"Polling error for {addr}: {e}")
                    break

    async def ws_handler():
        while True:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(ws_url) as ws:
                        logger.info("Mempool.space WS connected")
                        state["ws_connected"] = True
                        
                        # Request initial stats
                        await update_price_if_needed()
                        
                        addr_to_subs = await get_address_to_subs()
                        if addr_to_subs:
                            await ws.send_json({"action": "want-address-tracking", "addresses": list(addr_to_subs.keys())})
                        await ws.send_json({"action": "want-blocks"})
                        
                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                data = json.loads(msg.data)
                                
                                if "block" in data:
                                    state["tip_height"] = data["block"].get("height", state["tip_height"])
                                    # Update price on every block
                                    await update_price_if_needed()
                                
                                txs = []
                                if "address-transaction" in data:
                                    txs = [data["address-transaction"]]
                                elif "address-transactions" in data:
                                    txs = data["address-transactions"]
                                
                                if txs:
                                    addr_to_subs = await get_address_to_subs()
                                    if state["btc_price"] == 0:
                                        await update_price_if_needed()
                                    
                                    # Batch fetch notified confs
                                    txids = [tx.get("txid") for tx in txs if tx.get("txid")]
                                    all_notified = await get_notified_confs_batch(txids)
                                    
                                    all_updates = []
                                    for tx in txs:
                                        txid = tx.get("txid")
                                        tx_notified = all_notified.get(txid, {})
                                        for addr, subs in addr_to_subs.items():
                                            is_relevant = False
                                            for vout in tx.get("vout", []):
                                                if vout.get("scriptpubkey_address") == addr:
                                                    is_relevant = True
                                                    break
                                            if is_relevant:
                                                updates = await process_tx(app, tx, addr, subs, state["btc_price"], state["tip_height"], tx_notified=tx_notified)
                                                all_updates.extend(updates)
                                    
                                    if all_updates:
                                        await update_notified_confs_batch(all_updates)
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
            except Exception as e:
                logger.error(f"WS Error: {e}")
            finally:
                state["ws_connected"] = False
            
            await asyncio.sleep(10)

    # Start WS handler in background
    asyncio.create_task(ws_handler())
    
    # Polling loop (STRICT FALLBACK ONLY)
    async with httpx.AsyncClient(timeout=10.0) as client:
        while True:
            try:
                # Only poll if WebSocket is DOWN
                if not state["ws_connected"]:
                    logger.info("WS is down, performing fallback polling...")
                    addr_to_subs = await get_address_to_subs()
                    
                    if addr_to_subs:
                        stats = await get_mempool_stats()
                        if stats["price"]: state["btc_price"] = stats["price"]
                        if stats["height"]: state["tip_height"] = stats["height"]
                        
                        tasks = [poll_address(client, addr, subs) for addr, subs in addr_to_subs.items()]
                        await asyncio.gather(*tasks)
                else:
                    # WS is OK, just sleep and do nothing
                    pass
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"Error in polling loop: {e}")
            
            await asyncio.sleep(poll_interval)

