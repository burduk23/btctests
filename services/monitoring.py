import asyncio
import httpx
import logging
from core.config import config
from core.state import load_state, save_state, get_all_admins

logger = logging.getLogger("btc_notify")

async def monitor_loop(app):
    api_base = config.API_BASE
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
                        # Получаем список транзакций (Blockstream API)
                        r = await client.get(f"{api_base}/address/{addr}/txs")
                        if r.status_code == 200:
                            transactions = r.json()
                            
                            for tx in transactions:
                                txid = tx.get("txid")
                                status = tx.get("status", {})
                                confirmed = status.get("confirmed", False)
                                
                                # Если транзакция уже была обработана как подтвержденная - пропускаем
                                if not txid or txid in state.get("notified_confirmed", {}):
                                    continue

                                # 1. Новая транзакция (неподтвержденная)
                                if not confirmed and txid not in state.get("unconfirmed", {}):
                                    # Считаем сумму, которая пришла на этот адрес
                                    amount_sat = 0
                                    for vout in tx.get("vout", []):
                                        if vout.get("scriptpubkey_address") == addr:
                                            amount_sat += vout.get("value", 0)
                                    
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

                                # 2. Если транзакция из unconfirmed получила подтверждение
                                elif confirmed and txid in state.get("unconfirmed", {}):
                                    info = state["unconfirmed"][txid]
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
                        logger.debug(f"Ошибка при опросе {addr}: {e}")

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
