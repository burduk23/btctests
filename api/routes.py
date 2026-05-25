import time
import secrets
import json
import logging
import hmac
import hashlib
import base64
from urllib.parse import parse_qsl
from pathlib import Path
from aiohttp import web
from telegram.error import Forbidden
from core.config import config, BASE_DIR
from core.state import get_user, get_or_create_user, is_admin as check_is_admin, is_main_admin, get_all_admins, delete_address_group, delete_address, delete_admin, update_user_blocked
from services.monitoring import initialize_address
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from core.database import async_session
from core.models import User, Admin, AddressGroup, Address

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
        user_info = verify_webapp_data(init_data, config.BOT_TOKEN)
        if not user_info:
            return web.json_response({"error": "Unauthorized"}, status=401)
        
        uid = int(user_info.get("id"))
        user = await get_or_create_user(uid, user_info.get("username"), user_info.get("first_name"))
        
        is_admin = await check_is_admin(uid)
        
        # Convert user to dict for response
        user_data = {
            "groups": [
                {
                    "id": g.id,
                    "name": g.name,
                    "addresses": [
                        {
                            "id": a.id,
                            "addr": a.address,
                            "notify_disabled": a.notify_disabled,
                            "confirmations": a.confirmations_target
                        } for a in g.addresses
                    ]
                } for g in user.groups
            ]
        }
        
        response = {
            "user_data": user_data,
            "is_admin": is_admin
        }
        if is_admin:
            async with async_session() as session:
                stmt = select(User).options(selectinload(User.groups).selectinload(AddressGroup.addresses))
                result = await session.execute(stmt)
                all_users = result.scalars().all()
                response["all_users"] = {
                    str(u.telegram_id): {
                        "username": u.username,
                        "first_name": u.first_name,
                        "groups": [
                            {
                                "id": g.id,
                                "name": g.name,
                                "addresses": [
                                    {
                                        "id": a.id,
                                        "addr": a.address,
                                        "notify_disabled": a.notify_disabled,
                                        "confirmations": a.confirmations_target
                                    } for a in g.addresses
                                ]
                            } for g in u.groups
                        ]
                    } for u in all_users
                }
                
        if is_main_admin(uid):
            response["is_main_admin"] = True
            response["admins"] = [str(a) for a in await get_all_admins()]
            
        resp = web.json_response(response)
        resp.set_cookie('tg_session', str(uid), httponly=True, samesite='Lax', secure=True if config.WEBAPP_URL.startswith('https') else False)
        return resp
    except Exception as e:
        logger.error(f"Error in web_api_get: {e}")
        return web.json_response({"error": "Server error"}, status=500)

async def web_api_action(request):
    try:
        data = await request.json()
        init_data = data.get("initData")
        action = data.get("action")
        payload = data.get("payload", {})
        
        user_info = verify_webapp_data(init_data, config.BOT_TOKEN)
        if not user_info:
            return web.json_response({"error": "Unauthorized"}, status=401)
            
        uid = int(user_info.get("id"))
        is_admin = await check_is_admin(uid)
        
        async with async_session() as session:
            if action == "add_address":
                name = payload.get("group_name", "").strip()
                addr = payload.get("address", "").strip()
                confirmations = int(payload.get("confirmations", 1))
                if not name or len(addr) < 26:
                    return web.json_response({"error": "Invalid data"}, status=400)
                
                user = await get_or_create_user(uid, user_info.get("username"), user_info.get("first_name"))
                
                group = next((g for g in user.groups if g.name.lower() == name.lower()), None)
                if not group:
                    group = AddressGroup(user_id=user.id, name=name)
                    session.add(group)
                    await session.flush()
                
                new_addr = Address(group_id=group.id, address=addr, confirmations_target=confirmations)
                session.add(new_addr)
                await session.commit()
                
                await initialize_address(addr, uid)
                return web.json_response({"success": True})
                
            elif action == "delete_group":
                gid = int(payload.get("gid"))
                await delete_address_group(gid)
                return web.json_response({"success": True})
                
            elif action == "delete_address":
                aid = int(payload.get("aid"))
                await delete_address(aid)
                return web.json_response({"success": True})
                
            elif action == "admin_toggle_notify" and is_admin:
                aid = int(payload.get("aid"))
                addr = await session.get(Address, aid)
                if addr:
                    addr.notify_disabled = not addr.notify_disabled
                    await session.commit()
                return web.json_response({"success": True})
                
            elif action == "admin_delete_address" and is_admin:
                aid = int(payload.get("aid"))
                await delete_address(aid)
                return web.json_response({"success": True})
                
            elif action == "admin_add_address" and is_admin:
                target_uid = int(payload.get("uid"))
                name = payload.get("group_name", "").strip()
                addr = payload.get("address", "").strip()
                confirmations = int(payload.get("confirmations", 1))
                if not name or len(addr) < 26:
                    return web.json_response({"error": "Invalid data"}, status=400)
                    
                user = await get_or_create_user(target_uid)
                
                group = next((g for g in user.groups if g.name.lower() == name.lower()), None)
                if not group:
                    group = AddressGroup(user_id=user.id, name=name)
                    session.add(group)
                    await session.flush()
                
                new_addr = Address(group_id=group.id, address=addr, confirmations_target=confirmations)
                session.add(new_addr)
                await session.commit()
                
                await initialize_address(addr, target_uid)
                return web.json_response({"success": True})
                
            elif action == "admin_broadcast" and is_admin:
                uids = payload.get("uids", [])
                text = payload.get("text", "").strip()
                if not text or not uids:
                    return web.json_response({"error": "Invalid data"}, status=400)
                
                bot = request.app['bot_app'].bot
                sent = 0
                for target_uid in uids:
                    try:
                        t_uid = int(target_uid)
                        user = await get_user(t_uid)
                        if user and user.bot_blocked:
                            logger.info(f"Skipping broadcast to {t_uid} (bot blocked)")
                            continue
                            
                        await bot.send_message(chat_id=t_uid, text=f"📩 Рассылка от администратора:\n\n{text}")
                        sent += 1
                    except Forbidden:
                        logger.error(f"Бот заблокирован пользователем {target_uid}. Помечаем как blocked.")
                        await update_user_blocked(t_uid, True)
                    except Exception as e:
                        logger.error(f"Error sending broadcast to {target_uid}: {e}")
                return web.json_response({"success": True, "sent": sent})

            elif action == "send_chat_message":
                text = payload.get("text", "").strip()
                image_b64 = payload.get("image")
                
                if not text and not image_b64:
                    return web.json_response({"error": "Invalid data"}, status=400)
                
                bot = request.app['bot_app'].bot
                target_uid = int(payload.get("uid")) if is_admin and payload.get("uid") else uid
                
                sender_role = "admin" if is_admin and payload.get("uid") else "user"
                
                # Handle image
                image_path = None
                if image_b64 and "," in image_b64:
                    try:
                        header, data = image_b64.split(",", 1)
                        ext = "jpg"
                        if "png" in header: ext = "png"
                        elif "gif" in header: ext = "gif"
                        
                        filename = f"{secrets.token_hex(8)}.{ext}"
                        filepath = BASE_DIR / "static" / "uploads" / filename
                        with open(filepath, "wb") as f:
                            f.write(base64.b64decode(data))
                        
                        image_path = filepath
                    except Exception as e:
                        logger.error(f"Error saving image: {e}")

                try:
                    if sender_role == "admin":
                        user = await get_user(target_uid)
                        if user and user.bot_blocked:
                            logger.info(f"Cannot send message to {target_uid} (bot blocked)")
                            return web.json_response({"error": "User blocked the bot"}, status=403)

                        try:
                            if image_path:
                                await bot.send_photo(chat_id=int(target_uid), photo=open(image_path, 'rb'), caption=f"📩 Сообщение от администратора:\n\n{text}" if text else "📩 Изображение от администратора")
                            else:
                                await bot.send_message(chat_id=int(target_uid), text=f"📩 Сообщение от администратора:\n\n{text}")
                        except Forbidden:
                            logger.error(f"Бот заблокирован пользователем {target_uid}. Помечаем как blocked.")
                            await update_user_blocked(int(target_uid), True)
                            return web.json_response({"error": "User blocked the bot"}, status=403)
                    else:
                        all_admins = await get_all_admins()
                        user_name = user_info.get("first_name") or user_info.get("username") or str(uid)
                        notification_text = f"📩 Новое сообщение в Mini App от {user_name} ({uid}):\n\n{text}" if text else f"📩 Новое изображение в Mini App от {user_name} ({uid})"
                        
                        for admin_id in all_admins:
                            try:
                                if image_path:
                                    await bot.send_photo(chat_id=int(admin_id), photo=open(image_path, 'rb'), caption=notification_text)
                                else:
                                    await bot.send_message(chat_id=int(admin_id), text=notification_text)
                            except Forbidden:
                                logger.error(f"Бот заблокирован администратором {admin_id}. Помечаем как blocked.")
                                await update_user_blocked(int(admin_id), True)
                            except Exception as e:
                                logger.error(f"Error sending chat notification to admin {admin_id}: {e}")
                except Exception as e:
                    logger.error(f"Error handling send_chat_message: {e}")
                    
                return web.json_response({"success": True})

            elif action == "admin_add_admin" and is_main_admin(uid):
                new_admin_id = int(payload.get("uid"))
                stmt = select(Admin).where(Admin.telegram_id == new_admin_id)
                result = await session.execute(stmt)
                if not result.scalar_one_or_none() and new_admin_id != config.ADMIN_CHAT_ID:
                    session.add(Admin(telegram_id=new_admin_id))
                    await session.commit()
                return web.json_response({"success": True})

            elif action == "admin_remove_admin" and is_main_admin(uid):
                remove_admin_id = int(payload.get("uid"))
                await delete_admin(remove_admin_id)
                return web.json_response({"success": True})
                
            elif action == "mark_chat_read":
                # We don't store messages in DB yet, so this is a no-op for now
                return web.json_response({"success": True})
                
            return web.json_response({"error": "Unknown action"}, status=400)
    except Exception as e:
        logger.error(f"Error in web_api_action: {e}")
        return web.json_response({"error": "Server error"}, status=500)
