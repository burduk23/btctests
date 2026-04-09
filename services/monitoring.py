import asyncio
import httpx
import logging
from core.config import config
from core.state import load_state, save_state, get_all_admins

logger = logging.getLogger("btc_notify")

async def monitor_loop(app):
    api_base = config.API_BASE
    api_token = config.API_TOKEN
    poll_interval = config.POLL_INTERVAL

    client = httpx.AsyncClient(timeout=10.0)
    try:
        while True:
            try:
                state = load_state()
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
                                    "disabled": a_entry.get("notify_disabled", False)
                                })

                if not addr_to_subs:
                    await asyncio.sleep(poll_interval)
                    continue

                try:
                    r = await client.get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd")
                    btc_price = r.json()["bitcoin"]["usd"] if r.status_code == 200 else 65000
                except:
                    btc_price = 65000

                for addr, subs in addr_to_subs.items():
                    try:
                        r = await client.get(f"{api_base}/addrs/{addr}?token={api_token}&limit=5")
                        if r.status_code == 200:
                            data = r.json()
                            tx_hashes = set()
                            for tx in data.get("unconfirmed_txrefs", []):
                                tx_hashes.add(tx.get("tx_hash"))
                            for tx in data.get("txrefs", [])[:3]:
                                if tx.get("confirmations", 0) == 0:
                                    tx_hashes.add(tx.get("tx_hash"))

                            for txid in tx_hashes:
                                if not txid or txid in state.get("unconfirmed", {}) or txid in state.get("notified_confirmed", {}):
                                    continue

                                try:
                                    detail = (await client.get(f"{api_base}/txs/{txid}?token={api_token}")).json()
                                except Exception as e:
                                    logger.debug(f"Ошибка при получении деталей tx {txid}: {e}")
                                    continue

                                amount_sat = 0
                                for o in detail.get("outputs", []):
                                    if addr in o.get("addresses", []):
                                        amount_sat += o.get("value", 0)
                                        
                                if amount_sat <= 0:
                                    continue

                                amount_btc = amount_sat / 1e8
                                amount_usd = round(amount_btc * btc_price, 2)

                                for sub in subs:
                                    if sub.get("disabled"):
                                        continue
                                    uid = sub["uid"]
                                    group_name = sub["group_name"]
                                    all_admins = get_all_admins(state)
                                    for admin_id in all_admins:
                                        try:
                                            await app.bot.send_message(
                                                chat_id=int(admin_id),
                                                text=f"🔔 Новая входящая транзакция (unconfirmed)\n"
                                                     f"————————————\n"
                                                     f"**{group_name}**\n"
                                                     f"`{addr}`\n"
                                                     f"————————————\n"
                                                     f"💰 Сумма: `{amount_btc:.8f}` BTC | `${amount_usd}`\n"
                                                     f"————————————\n"
                                                     f"📍 https://blockchair.com/bitcoin/transaction/{txid}",
                                                parse_mode="Markdown"
                                            )
                                        except Exception as e:
                                            logger.debug(f"Не удалось отправить уведомление админу {admin_id}: {e}")
                                    try:
                                        await app.bot.send_message(
                                            chat_id=int(uid),
                                            text=f"🔔 Новая входящая транзакция (unconfirmed)\n"
                                                 f"————————————\n"
                                                 f"**{group_name}**\n"
                                                 f"`{addr}`\n"
                                                 f"————————————\n"
                                                 f"💰 Сумма: `{amount_btc:.8f}` BTC | `${amount_usd}`\n"
                                                 f"————————————\n"
                                                 f"📍 https://blockchair.com/bitcoin/transaction/{txid}",
                                            parse_mode="Markdown"
                                        )
                                    except Exception as e:
                                        logger.debug(f"Не удалось отправить уведомление пользователю {uid}: {e}")

                                state.setdefault("unconfirmed", {})[txid] = {
                                    "addr": addr,
                                    "amount_btc": amount_btc,
                                    "amount_usd": amount_usd
                                } # type: ignore
                    except Exception as e:
                        logger.debug(f"Ошибка при опросе {addr}: {e}")

                for txid, info in list(state.get("unconfirmed", {}).items()):
                    try:
                        status_r = await client.get(f"{api_base}/txs/{txid}?token={api_token}")
                        if status_r.status_code == 200:
                            status = status_r.json()
                            if status.get("confirmations", 0) > 0 and txid not in state.get("notified_confirmed", {}):
                                addr = info["addr"]
                                amount_btc = info.get("amount_btc", 0)
                                amount_usd = info.get("amount_usd", 0)

                                subs = addr_to_subs.get(addr, [])
                                for sub in subs:
                                    if sub.get("disabled"):
                                        continue
                                    uid = sub["uid"]
                                    group_name = sub["group_name"]
                                    all_admins = get_all_admins(state)
                                    for admin_id in all_admins:
                                        try:
                                            await app.bot.send_message(
                                                chat_id=int(admin_id),
                                                text=f"✅ Транзакция получила 1+ подтверждение\n"
                                                     f"————————————\n"
                                                     f"**{group_name}**\n"
                                                     f"`{addr}`\n"
                                                     f"————————————\n"
                                                     f"💰 Сумма: `{amount_btc:.8f}` BTC | `${amount_usd}`\n"
                                                     f"————————————\n"
                                                     f"📍 https://blockchair.com/bitcoin/transaction/{txid}",
                                                parse_mode="Markdown"
                                            )
                                        except Exception as e:
                                            logger.debug(f"Не удалось отправить уведомление админу {admin_id}: {e}")
                                    try:
                                        await app.bot.send_message(
                                            chat_id=int(uid),
                                            text=f"✅ Транзакция получила 1+ подтверждение\n"
                                                 f"————————————\n"
                                                 f"**{group_name}**\n"
                                                 f"`{addr}`\n"
                                                 f"————————————\n"
                                                 f"💰 Сумма: `{amount_btc:.8f}` BTC | `${amount_usd}`\n"
                                                 f"————————————\n"
                                                 f"📍 https://blockchair.com/bitcoin/transaction/{txid}",
                                            parse_mode="Markdown"
                                        )
                                    except Exception as e:
                                        logger.debug(f"Не удалось отправить уведомление {uid}: {e}")

                                state.setdefault("notified_confirmed", {})[txid] = True # type: ignore
                                state["unconfirmed"].pop(txid, None)
                    except Exception as e:
                        logger.debug(f"Ошибка статуса tx {txid}: {e}")

                save_state(state)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("Ошибка в monitor_loop: %s", e)
            await asyncio.sleep(poll_interval)
    finally:
        try:
            await client.aclose()
        except Exception:
            pass
