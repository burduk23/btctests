import time
import secrets
import json
import logging
import hmac
import hashlib
from urllib.parse import parse_qsl
from pathlib import Path
from aiohttp import web
from core.config import config, BASE_DIR
from core.state import load_state, save_state, is_admin as check_is_admin, is_main_admin, get_all_admins
from services.address import mk_group, mk_address, find_group, find_address

logger = logging.getLogger("btc_notify")

def verify_webapp_data(init_data: str, bot_token: str):
    if not init_data: return None
    try:
        parsed_data = dict(parse_qsl(init_data))
        if "hash" not in parsed_data: return None
        hash_val = parsed_data.pop("hash")
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed_data.items()))
        secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        calc_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(calc_hash, hash_val):
            return json.loads(parsed_data.get("user", "{}"))
    except Exception as e:
        logger.error(f"Webapp auth error: {e}")
    return None

async def web_index(request):
    return web.FileResponse(BASE_DIR / "index.html")

async def web_api_get(request):
    try:
        data = await request.json()
        init_data = data.get("initData")
        user = verify_webapp_data(init_data, config.BOT_TOKEN)
        if not user:
            return web.json_response({"error": "Unauthorized"}, status=401)
        
        uid = str(user.get("id"))
        state = load_state()
        is_admin = check_is_admin(uid, state)
        
        user_data = state.get("users", {}).get(uid, {"groups": []})
        response = {
            "user_data": user_data,
            "is_admin": is_admin
        }
        if is_admin:
            response["all_users"] = state.get("users", {})
        if is_main_admin(uid):
            response["is_main_admin"] = True
            response["admins"] = state.get("admins", [])
            
        return web.json_response(response)
    except Exception as e:
        logger.error(f"Error in web_api_get: {e}")
        return web.json_response({"error": "Server error"}, status=500)

async def web_api_action(request):
    try:
        data = await request.json()
        init_data = data.get("initData")
        action = data.get("action")
        payload = data.get("payload", {})
        
        user = verify_webapp_data(init_data, config.BOT_TOKEN)
        if not user:
            return web.json_response({"error": "Unauthorized"}, status=401)
            
        uid = str(user.get("id"))
        state = load_state()
        is_admin = check_is_admin(uid, state)
        
        user_data = state.setdefault("users", {}).setdefault(uid, {"groups": []}) # type: ignore
        
        if action == "add_address":
            name = payload.get("group_name", "").strip()
            addr = payload.get("address", "").strip()
            if not name or len(addr) < 26:
                return web.json_response({"error": "Invalid data"}, status=400)
                
            group = next((g for g in user_data.get("groups", []) if g["name"].lower() == name.lower()), None)
            if not group:
                group = mk_group(name)
                user_data.setdefault("groups", []).append(group)
            group.setdefault("addresses", []).append(mk_address(addr))
            save_state(state)
            return web.json_response({"success": True})
            
        elif action == "delete_group":
            gid = payload.get("gid")
            user_data["groups"] = [g for g in user_data.get("groups", []) if g.get("id") != gid]
            save_state(state)
            return web.json_response({"success": True})
            
        elif action == "delete_address":
            gid = payload.get("gid")
            aid = payload.get("aid")
            group = find_group(user_data, gid) # type: ignore
            if group:
                group["addresses"] = [a for a in group.get("addresses", []) if a.get("id") != aid]
                save_state(state)
            return web.json_response({"success": True})
            
        elif action == "admin_toggle_notify" and is_admin:
            target_uid = str(payload.get("uid"))
            gid = payload.get("gid")
            aid = payload.get("aid")
            target_user = state.get("users", {}).get(target_uid, {})
            group = find_group(target_user, gid) # type: ignore
            if group:
                addr = find_address(group, aid)
                if addr:
                    addr["notify_disabled"] = not addr.get("notify_disabled", False)
                    save_state(state)
            return web.json_response({"success": True})
            
        elif action == "admin_delete_address" and is_admin:
            target_uid = str(payload.get("uid"))
            gid = payload.get("gid")
            aid = payload.get("aid")
            target_user = state.get("users", {}).get(target_uid, {})
            group = find_group(target_user, gid) # type: ignore
            if group:
                group["addresses"] = [a for a in group.get("addresses", []) if a.get("id") != aid]
                save_state(state)
            return web.json_response({"success": True})
            
        elif action == "admin_add_address" and is_admin:
            target_uid = str(payload.get("uid"))
            name = payload.get("group_name", "").strip()
            addr = payload.get("address", "").strip()
            if not name or len(addr) < 26:
                return web.json_response({"error": "Invalid data"}, status=400)
                
            target_user = state.setdefault("users", {}).setdefault(target_uid, {"groups": []}) # type: ignore
            group = next((g for g in target_user.get("groups", []) if g["name"].lower() == name.lower()), None)
            if not group:
                group = mk_group(name)
                target_user.setdefault("groups", []).append(group)
            group.setdefault("addresses", []).append(mk_address(addr))
            save_state(state)
            return web.json_response({"success": True})
            
        elif action == "admin_broadcast" and is_admin:
            uids = payload.get("uids", [])
            text = payload.get("text", "").strip()
            if not text or not uids:
                return web.json_response({"error": "Invalid data"}, status=400)
            
            bot = request.app['bot_app'].bot
            sent = 0
            for target_uid in uids:
                target_uid = str(target_uid)
                target_user = state.setdefault("users", {}).setdefault(target_uid, {"groups": []}) # type: ignore
                msg_obj = {"id": secrets.token_hex(4), "from": "admin", "text": text, "ts": int(time.time())}
                target_user.setdefault("messages", []).append(msg_obj)
                try:
                    await bot.send_message(chat_id=int(target_uid), text=f"📩 Рассылка от администратора:\n\n{text}")
                    sent += 1
                except Exception as e:
                    logger.error(f"Error sending broadcast to {target_uid}: {e}")
            save_state(state)
            return web.json_response({"success": True, "sent": sent})

        elif action == "send_chat_message":
            text = payload.get("text", "").strip()
            if not text:
                return web.json_response({"error": "Invalid data"}, status=400)
            
            bot = request.app['bot_app'].bot
            target_uid = str(payload.get("uid")) if is_admin and payload.get("uid") else uid
            target_user = state.setdefault("users", {}).setdefault(target_uid, {"groups": []}) # type: ignore
            
            sender_role = "admin" if is_admin and payload.get("uid") else "user"
            msg_obj = {"id": secrets.token_hex(4), "from": sender_role, "text": text, "ts": int(time.time())}
            target_user.setdefault("messages", []).append(msg_obj)
            save_state(state)
            
            try:
                if sender_role == "admin":
                    await bot.send_message(chat_id=int(target_uid), text=f"📩 Сообщение от администратора:\n\n{text}")
                else:
                    all_admins = get_all_admins(state)
                    user_name = user.get("first_name") or user.get("username") or uid
                    for admin_id in all_admins:
                        try:
                            await bot.send_message(chat_id=int(admin_id), text=f"📩 Новое сообщение в Mini App от {user_name} ({uid}):\n\n{text}")
                        except Exception as e:
                            logger.error(f"Error sending chat notification to admin {admin_id}: {e}")
            except Exception as e:
                logger.error(f"Error handling send_chat_message: {e}")
                
            return web.json_response({"success": True})

        elif action == "admin_add_admin" and is_main_admin(uid):
            new_admin_id = str(payload.get("uid")).strip()
            if not new_admin_id or not new_admin_id.isdigit():
                return web.json_response({"error": "Invalid UID"}, status=400)
            
            admins = state.setdefault("admins", [])
            if new_admin_id not in admins and new_admin_id != config.ADMIN_CHAT_ID:
                admins.append(new_admin_id)
                save_state(state)
            return web.json_response({"success": True})

        elif action == "admin_remove_admin" and is_main_admin(uid):
            remove_admin_id = str(payload.get("uid")).strip()
            admins = state.get("admins", [])
            if remove_admin_id in admins:
                admins.remove(remove_admin_id)
                save_state(state)
            return web.json_response({"success": True})
            
        elif action == "mark_chat_read":
            target_uid = payload.get("uid")
            if is_admin and target_uid:
                target_user = state.setdefault("users", {}).setdefault(str(target_uid), {"groups": []}) # type: ignore
                for msg in target_user.get("messages", []):
                    if msg.get("from") == "user":
                        msg["read"] = True
                save_state(state)
            elif not is_admin:
                for msg in user_data.get("messages", []):
                    if msg.get("from") == "admin":
                        msg["read"] = True
                save_state(state)
            return web.json_response({"success": True})
            
        return web.json_response({"error": "Unknown action"}, status=400)
    except Exception as e:
        logger.error(f"Error in web_api_action: {e}")
        return web.json_response({"error": "Server error"}, status=500)
