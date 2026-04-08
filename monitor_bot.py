#!/usr/bin/env python3
# monitor_bot.py — Telegram bot + BTC monitor (финальная исправленная версия)
# Полностью автономный файл — редактируй через nano

import asyncio
import json
import logging
import time
import secrets
import hmac
import hashlib
import random
from playwright.async_api import async_playwright
from urllib.parse import parse_qsl
from pathlib import Path
from typing import Optional

import httpx
from aiohttp import web
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
)
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

# ---------------- config / files ----------------
BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"
STATE_FILE = BASE_DIR / "state.json"

ADMIN_ID = "5381999598"

# ---------------- logging ----------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("btc_notify")

# ---------------- state helpers ----------------
def load_config():
    if not CONFIG_FILE.exists():
        raise SystemExit("config.json не найден — заполните и перезапустите.")
    return json.loads(CONFIG_FILE.read_text())

def load_state():
    default_state = {"users": {}, "unconfirmed": {}, "notified_confirmed": {}}
    if not STATE_FILE.exists():
        return default_state
    try:
        data = json.loads(STATE_FILE.read_text())
        if "groups" in data and "users" not in data:
            cfg = load_config()
            admin_id = str(cfg.get("ADMIN_CHAT_ID", ""))
            new_state = {"users": {}, "unconfirmed": data.get("unconfirmed", {}), "notified_confirmed": data.get("notified_confirmed", {})}
            if admin_id:
                new_state["users"][admin_id] = {"groups": data.get("groups", [])}
            logger.info(f"Выполнена миграция базы данных для админа {admin_id}")
            save_state(new_state)
            return new_state
        
        for key in default_state:
            if key not in data:
                data[key] = default_state[key]
        return data
    except Exception as e:
        logger.error(f"Ошибка при загрузке state.json: {e}")
        return default_state

def save_state(state):
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    except Exception as e:
        logger.error(f"Ошибка при сохранении state.json: {e}")

def mk_group(name: str) -> dict:
    return {"id": secrets.token_hex(6), "name": name.strip(), "addresses": []}

def mk_address(addr: str) -> dict:
    return {"id": secrets.token_hex(6), "addr": addr.strip(), "notify_disabled": False}

def find_group(user_data: dict, gid: str) -> Optional[dict]:
    for g in user_data.get("groups", []):
        if g.get("id") == gid:
            return g
    return None

def find_address(group: dict, aid: str) -> Optional[dict]:
    for a in group.get("addresses", []):
        if a.get("id") == aid:
            return a
    return None

# ---------------- exchange scraping ----------------
async def get_exchange_rate(btc_amount: float) -> str:
    """
    Navigates to onemoment.cc, inputs the BTC amount, and returns the resulting RUB amount.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        try:
            await page.goto('https://onemoment.cc/')
            await page.wait_for_timeout(3000)

            # 1. Отдаете -> СБП
            try:
                give_label = page.get_by_text('Отдаете', exact=True).first
                await give_label.evaluate('el => el.parentElement.parentElement.querySelector("button").click()')
                await page.wait_for_timeout(1000)
                await page.get_by_text('СБП', exact=True).nth(0).click()
                await page.wait_for_timeout(1000)
            except Exception as e:
                logger.warning(f"Failed to select СБП: {e}")

            # 2. Получаете -> Bitcoin
            try:
                get_label = page.get_by_text('Получаете', exact=True).first
                await get_label.evaluate('el => el.parentElement.parentElement.querySelector("button").click()')
                await page.wait_for_timeout(1000)
                await page.get_by_text('Bitcoin', exact=True).nth(0).click()
                await page.wait_for_timeout(1000)
            except Exception as e:
                logger.warning(f"Failed to select Bitcoin: {e}")

            # 3. Сначала выбираем "С верификацией", чтобы избежать сброса введенной суммы
            try:
                await page.evaluate('''() => {
                    const buttons = Array.from(document.querySelectorAll('button'));
                    const target = buttons.find(b => b.textContent.includes('С верификацией'));
                    if (target) target.click();
                }''')
                await page.wait_for_timeout(2000)
            except Exception as e:
                logger.warning(f"Failed to click 'С верификацией': {e}")

            # 4. Ввод суммы BTC
            inputs = page.locator('input[type="text"]')
            await inputs.nth(1).focus()
            await inputs.nth(1).click(click_count=3)
            await page.keyboard.press('Backspace')
            await page.wait_for_timeout(500)
            
            await inputs.nth(1).press_sequentially(str(btc_amount), delay=100)
            await page.wait_for_timeout(5000)

            # 5. Получение итоговой суммы в RUB
            give_val_str = await inputs.nth(0).input_value()
            
            try:
                clean_val = give_val_str.replace('\xa0', '').replace(' ', '').replace(',', '.')
                parsed_rub = float(clean_val)
                # Hidden commission 300-310 RUB
                final_rub = parsed_rub + random.randint(300, 310)
                return f"{final_rub:,.2f}".replace(',', ' ') + " RUB"
            except Exception as e:
                logger.warning(f"Ошибка при парсинге суммы '{give_val_str}': {e}")
                return f"{give_val_str} RUB"

        except Exception as e:
            logger.error(f"Playwright error: {e}")
            return "Ошибка при получении курса. Пожалуйста, попробуйте позже."
        finally:
            await browser.close()

# ---------------- web app ----------------
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
        cfg = load_config()
        user = verify_webapp_data(init_data, cfg.get("BOT_TOKEN"))
        if not user:
            return web.json_response({"error": "Unauthorized"}, status=401)
        
        uid = str(user.get("id"))
        state = load_state()
        is_admin = (uid == str(cfg.get("ADMIN_CHAT_ID", ADMIN_ID)))
        
        user_data = state.get("users", {}).get(uid, {"groups": []})
        response = {
            "user_data": user_data,
            "is_admin": is_admin
        }
        if is_admin:
            response["all_users"] = state.get("users", {})
            
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
        
        cfg = load_config()
        user = verify_webapp_data(init_data, cfg.get("BOT_TOKEN"))
        if not user:
            return web.json_response({"error": "Unauthorized"}, status=401)
            
        uid = str(user.get("id"))
        state = load_state()
        is_admin = (uid == str(cfg.get("ADMIN_CHAT_ID", ADMIN_ID)))
        
        user_data = state.setdefault("users", {}).setdefault(uid, {"groups": []})
        
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
            group = find_group(user_data, gid)
            if group:
                group["addresses"] = [a for a in group.get("addresses", []) if a.get("id") != aid]
                save_state(state)
            return web.json_response({"success": True})
            
        elif action == "admin_toggle_notify" and is_admin:
            target_uid = str(payload.get("uid"))
            gid = payload.get("gid")
            aid = payload.get("aid")
            target_user = state.get("users", {}).get(target_uid, {})
            group = find_group(target_user, gid)
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
            group = find_group(target_user, gid)
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
                
            target_user = state.setdefault("users", {}).setdefault(target_uid, {"groups": []})
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
                target_user = state.setdefault("users", {}).setdefault(target_uid, {"groups": []})
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
            target_user = state.setdefault("users", {}).setdefault(target_uid, {"groups": []})
            
            sender_role = "admin" if is_admin and payload.get("uid") else "user"
            msg_obj = {"id": secrets.token_hex(4), "from": sender_role, "text": text, "ts": int(time.time())}
            target_user.setdefault("messages", []).append(msg_obj)
            save_state(state)
            
            try:
                if sender_role == "admin":
                    await bot.send_message(chat_id=int(target_uid), text=f"📩 Сообщение от администратора:\n\n{text}")
                else:
                    admin_chat_id = cfg.get("ADMIN_CHAT_ID", ADMIN_ID)
                    user_name = user.get("first_name") or user.get("username") or uid
                    await bot.send_message(chat_id=int(admin_chat_id), text=f"📩 Новое сообщение в Mini App от {user_name} ({uid}):\n\n{text}")
            except Exception as e:
                logger.error(f"Error sending chat notification: {e}")
                
            return web.json_response({"success": True})
            
        return web.json_response({"error": "Unknown action"}, status=400)
    except Exception as e:
        logger.error(f"Error in web_api_action: {e}")
        return web.json_response({"error": "Server error"}, status=500)

async def start_web_server(app):
    webapp = web.Application()
    webapp['bot_app'] = app
    webapp.router.add_get('/', web_index)
    webapp.router.add_post('/api/get', web_api_get)
    webapp.router.add_post('/api/action', web_api_action)

    runner = web.AppRunner(webapp)
    await runner.setup()
    cfg = load_config()
    port = int(cfg.get("WEB_PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"Web server started on port {port}")

# ---------------- inline UI builders ----------------
def main_menu_markup(user_id: str = None):
    cfg = load_config()
    kb = []

    kb.extend([
        [InlineKeyboardButton("💱 Обмен", callback_data="exchange")],
        [InlineKeyboardButton("📂 Список адресов", callback_data="menu_groups")],
        [InlineKeyboardButton("➕ Добавить адрес", callback_data="menu_add_address")],
        [InlineKeyboardButton("🗑 Удалить название", callback_data="menu_remove_group")]
    ])
    if user_id == str(cfg.get("ADMIN_CHAT_ID", ADMIN_ID)):
        kb.append([InlineKeyboardButton("👑 Админ панель", callback_data="admin_panel")])
    return InlineKeyboardMarkup(kb)

def groups_list_markup(groups):
    kb = [[InlineKeyboardButton(g["name"], callback_data=f"group:{g['id']}")] for g in groups]
    kb.append([InlineKeyboardButton("⬅ Назад", callback_data="menu_main")])
    return InlineKeyboardMarkup(kb)

def group_view_markup(group):
    kb = [
        [InlineKeyboardButton("➕ Добавить адрес", callback_data=f"group_add_addr:{group['id']}")],
        [InlineKeyboardButton("✏ Заменить адрес", callback_data=f"group_edit_addr:{group['id']}")],
        [InlineKeyboardButton("🗑 Удалить адрес", callback_data=f"group_del_addr:{group['id']}")],
        [InlineKeyboardButton("🗑 Удалить название", callback_data=f"group_del:{group['id']}")],
        [InlineKeyboardButton("⬅ Назад", callback_data="menu_groups")],
    ]
    return InlineKeyboardMarkup(kb)

def cancel_markup(target="menu_main"):
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data=f"cancel:{target}")]])

def admin_panel_markup(users):
    kb = []
    for uid, udata in users.items():
        name = udata.get("first_name") or udata.get("username") or f"ID: {uid}"
        kb.append([InlineKeyboardButton(f"👤 {name} ({uid})", callback_data=f"admin_user:{uid}")])
    kb.append([InlineKeyboardButton("✉ Рассылка сообщений", callback_data="admin_broadcast_menu")])
    kb.append([InlineKeyboardButton("⬅ Назад", callback_data="menu_main")])
    return InlineKeyboardMarkup(kb)

def admin_broadcast_menu_markup(users, selected_uids):
    kb = []
    for uid, udata in users.items():
        name = udata.get("first_name") or udata.get("username") or f"ID: {uid}"
        mark = "✅ " if uid in selected_uids else "⬜ "
        kb.append([InlineKeyboardButton(f"{mark}{name} ({uid})", callback_data=f"admin_broadcast_toggle:{uid}")])
    
    if selected_uids:
        kb.append([InlineKeyboardButton("✍️ Написать сообщение", callback_data="admin_broadcast_write")])
    kb.append([InlineKeyboardButton("☑ Выбрать всех", callback_data="admin_broadcast_select_all")])
    kb.append([InlineKeyboardButton("⬅ Назад", callback_data="admin_panel")])
    return InlineKeyboardMarkup(kb)

def admin_user_markup(uid, udata):
    kb = []
    for g in udata.get("groups", []):
        for a in g.get("addresses", []):
            status = "❌ Откл." if a.get("notify_disabled") else "✅ Вкл."
            kb.append([InlineKeyboardButton(f"{g['name']} - {a['addr'][:8]}... [{status}]", callback_data=f"admin_addr:{uid}:{g['id']}:{a['id']}")])
    kb.append([InlineKeyboardButton("➕ Добавить адрес пользователю", callback_data=f"admin_add_addr:{uid}")])
    kb.append([InlineKeyboardButton("⬅ Назад", callback_data="admin_panel")])
    return InlineKeyboardMarkup(kb)

def admin_addr_markup(uid, gid, aid, disabled):
    kb = [
        [InlineKeyboardButton("✅ Включить уведомления" if disabled else "❌ Отключить уведомления", callback_data=f"admin_toggle_notify:{uid}:{gid}:{aid}")],
        [InlineKeyboardButton("🗑 Удалить адрес", callback_data=f"admin_del_addr:{uid}:{gid}:{aid}")],
        [InlineKeyboardButton("⬅ Назад", callback_data=f"admin_user:{uid}")]
    ]
    return InlineKeyboardMarkup(kb)

# ---------------- menu message management ----------------
async def update_menu(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, markup: InlineKeyboardMarkup = None, parse_mode="HTML"):
    menu_id = ctx.chat_data.get("menu_id")
    if menu_id:
        try:
            await ctx.bot.edit_message_text(chat_id=chat_id, message_id=menu_id, text=text, reply_markup=markup, parse_mode=parse_mode)
            return
        except Exception:
            ctx.chat_data.pop("menu_id", None)
    msg = await ctx.bot.send_message(chat_id=chat_id, text=text, reply_markup=markup, parse_mode=parse_mode)
    ctx.chat_data["menu_id"] = msg.message_id

async def remove_menu_only(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int):
    menu_id = ctx.chat_data.pop("menu_id", None)
    if menu_id:
        try:
            await ctx.bot.delete_message(chat_id=chat_id, message_id=menu_id)
        except Exception:
            pass

async def remove_prompt(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int):
    prompt_id = ctx.chat_data.pop("prompt_id", None)
    if prompt_id:
        try:
            await ctx.bot.delete_message(chat_id=chat_id, message_id=prompt_id)
        except Exception:
            pass

# ---------------- command handlers ----------------
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    logger.info(f"Команда /start от пользователя {chat_id}")
    
    state = load_state()
    user_id = str(chat_id)
    user_data = state.setdefault("users", {}).setdefault(user_id, {"groups": []})
    if user:
        user_data["username"] = user.username
        user_data["first_name"] = user.first_name
    save_state(state)
    
    ctx.chat_data.pop("flow", None)
    await remove_prompt(ctx, chat_id)
    await update_menu(ctx, chat_id, "Главное меню:", main_menu_markup(user_id))

# ---------------- callback handling ----------------
async def callback_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = update.effective_chat.id
    user = update.effective_user
    logger.info(f"Callback '{data}' от пользователя {chat_id}")
    
    state = load_state()
    user_id = str(chat_id)
    user_data = state.setdefault("users", {}).setdefault(user_id, {"groups": []})
    if user:
        user_data["username"] = user.username
        user_data["first_name"] = user.first_name

    if data == "menu_main":
        ctx.chat_data.pop("flow", None)
        await update_menu(ctx, chat_id, "Главное меню:", main_menu_markup(user_id))
        return

    if data == "exchange":
        await remove_menu_only(ctx, chat_id)
        prompt = await ctx.bot.send_message(chat_id=chat_id, text="Введите сумму в Bitcoin (например 0.0045).", reply_markup=cancel_markup("menu_main"))
        ctx.chat_data["flow"] = {"action": "exchange_wait_amount", "prompt_id": prompt.message_id}
        return

    if data == "menu_groups":
        groups = user_data.get("groups", [])
        if not groups:
            await update_menu(ctx, chat_id, "Список адресов пуст.", main_menu_markup(user_id))
            return
        await update_menu(ctx, chat_id, "Список адресов (нажмите название):", groups_list_markup(groups))
        return

    if data == "menu_add_address":
        await remove_menu_only(ctx, chat_id)
        prompt = await ctx.bot.send_message(chat_id=chat_id, text="Введите название для адреса:", reply_markup=cancel_markup("menu_main"))
        ctx.chat_data["flow"] = {"action": "add_address_name", "prompt_id": prompt.message_id}
        return

    if data == "menu_remove_group":
        groups = user_data.get("groups", [])
        if not groups:
            await update_menu(ctx, chat_id, "Список пуст.", main_menu_markup(user_id))
            return
        kb = [[InlineKeyboardButton(f"Удалить «{g['name']}»", callback_data=f"action_delete_group:{g['id']}")] for g in groups]
        kb.append([InlineKeyboardButton("⬅ Назад", callback_data="menu_main")])
        await update_menu(ctx, chat_id, "Удаление названия:", InlineKeyboardMarkup(kb))
        return

    if data.startswith("group:"):
        gid = data.split(":", 1)[1]
        group = find_group(user_data, gid)
        if not group:
            await update_menu(ctx, chat_id, "Название не найдено.", main_menu_markup(user_id))
            return
        if group.get("addresses"):
            lines = [f"{idx+1}. <code>{a['addr']}</code>" for idx, a in enumerate(group.get("addresses"))]
            body = f"<b>{group['name']}</b>\n\nАдреса:\n" + "\n".join(lines)
        else:
            body = f"<b>{group['name']}</b>\n\nАдресов нет."
        await update_menu(ctx, chat_id, body, group_view_markup(group))
        return

    if data.startswith("group_add_addr:"):
        gid = data.split(":", 1)[1]
        group = find_group(user_data, gid)
        if not group:
            await update_menu(ctx, chat_id, "Группа не найдена.", main_menu_markup(user_id))
            return
        await remove_menu_only(ctx, chat_id)
        prompt = await ctx.bot.send_message(chat_id=chat_id, text=f"Введите BTC адрес для «{group['name']}»:", reply_markup=cancel_markup(f"group:{gid}"))
        ctx.chat_data["flow"] = {"action": "add_address_direct", "gid": gid, "prompt_id": prompt.message_id}
        return

    if data.startswith("group_edit_addr:"):
        gid = data.split(":", 1)[1]
        group = find_group(user_data, gid)
        if not group or not group.get("addresses"):
            await update_menu(ctx, chat_id, "Нет адресов для замены.", main_menu_markup(user_id))
            return
        await remove_menu_only(ctx, chat_id)
        prompt = await ctx.bot.send_message(chat_id=chat_id, text="Выберите адрес для замены (введите старый адрес):", reply_markup=cancel_markup(f"group:{gid}"))
        ctx.chat_data["flow"] = {"action": "edit_address_select", "gid": gid, "prompt_id": prompt.message_id}
        return

    if data.startswith("group_del_addr:"):
        gid = data.split(":", 1)[1]
        group = find_group(user_data, gid)
        if not group:
            return
        kb = [[InlineKeyboardButton(f"🗑 {a['addr'][:12]}...", callback_data=f"addr_del:{gid}:{a['id']}")] for a in group.get("addresses", [])]
        kb.append([InlineKeyboardButton("⬅ Назад", callback_data=f"group:{gid}")])
        await update_menu(ctx, chat_id, f"Выберите адрес для удаления в «{group['name']}»:", InlineKeyboardMarkup(kb))
        return

    if data.startswith("action_delete_group:") or data.startswith("group_del:"):
        gid = data.split(":", 1)[1]
        user_data["groups"] = [g for g in user_data.get("groups", []) if g.get("id") != gid]
        save_state(state)
        await update_menu(ctx, chat_id, "Название удалено.", main_menu_markup(user_id))
        return

    if data.startswith("addr_del:"):
        _, gid, aid = data.split(":")
        group = find_group(user_data, gid)
        if group:
            group["addresses"] = [a for a in group.get("addresses", []) if a.get("id") != aid]
            save_state(state)
            await update_menu(ctx, chat_id, "Адрес удалён.", group_view_markup(group))
        return

    if data.startswith("cancel:"):
        ctx.chat_data.pop("flow", None)
        await remove_prompt(ctx, chat_id)
        await update_menu(ctx, chat_id, "Главное меню:", main_menu_markup(user_id))
        return

    # Admin Panel callbacks
    cfg = load_config()
    admin_id_cfg = str(cfg.get("ADMIN_CHAT_ID", ADMIN_ID))
    
    if data == "admin_panel" and user_id == admin_id_cfg:
        await update_menu(ctx, chat_id, "👑 Админ панель - Список пользователей:", admin_panel_markup(state.get("users", {})))
        return
        
    if data == "admin_broadcast_menu" and user_id == admin_id_cfg:
        selected = ctx.chat_data.setdefault("broadcast_targets", set())
        await update_menu(ctx, chat_id, "Выберите пользователей для рассылки:", admin_broadcast_menu_markup(state.get("users", {}), selected))
        return

    if data.startswith("admin_broadcast_toggle:") and user_id == admin_id_cfg:
        target_uid = data.split(":", 1)[1]
        selected = ctx.chat_data.setdefault("broadcast_targets", set())
        if target_uid in selected:
            selected.remove(target_uid)
        else:
            selected.add(target_uid)
        await update_menu(ctx, chat_id, "Выберите пользователей для рассылки:", admin_broadcast_menu_markup(state.get("users", {}), selected))
        return

    if data == "admin_broadcast_select_all" and user_id == admin_id_cfg:
        selected = ctx.chat_data.setdefault("broadcast_targets", set())
        all_uids = set(state.get("users", {}).keys())
        if selected == all_uids:
            selected.clear()
        else:
            selected.update(all_uids)
        await update_menu(ctx, chat_id, "Выберите пользователей для рассылки:", admin_broadcast_menu_markup(state.get("users", {}), selected))
        return

    if data == "admin_broadcast_write" and user_id == admin_id_cfg:
        selected = ctx.chat_data.get("broadcast_targets", set())
        if not selected:
            await update_menu(ctx, chat_id, "Никто не выбран.", admin_broadcast_menu_markup(state.get("users", {}), selected))
            return
        await remove_menu_only(ctx, chat_id)
        prompt = await ctx.bot.send_message(chat_id=chat_id, text=f"Введите текст рассылки для {len(selected)} пользователей:", reply_markup=cancel_markup("admin_panel"))
        ctx.chat_data["flow"] = {"action": "admin_broadcast_text", "prompt_id": prompt.message_id}
        return

    if data.startswith("admin_add_addr:") and user_id == admin_id_cfg:
        target_uid = data.split(":", 1)[1]
        await remove_menu_only(ctx, chat_id)
        prompt = await ctx.bot.send_message(chat_id=chat_id, text="Введите название группы для этого пользователя:", reply_markup=cancel_markup(f"admin_user:{target_uid}"))
        ctx.chat_data["flow"] = {"action": "admin_add_name", "target_uid": target_uid, "prompt_id": prompt.message_id}
        return

    if data.startswith("admin_user:") and user_id == admin_id_cfg:
        target_uid = data.split(":", 1)[1]
        target_user = state.get("users", {}).get(target_uid, {})
        name = target_user.get("first_name") or target_user.get("username") or target_uid
        text = f"Пользователь: {name}\nID: {target_uid}\n\nВыберите адрес для управления:"
        await update_menu(ctx, chat_id, text, admin_user_markup(target_uid, target_user))
        return

    if data.startswith("admin_addr:") and user_id == admin_id_cfg:
        _, target_uid, gid, aid = data.split(":")
        target_user = state.get("users", {}).get(target_uid, {})
        group = find_group(target_user, gid)
        if not group: return
        addr = find_address(group, aid)
        if not addr: return
        
        status = "отключены" if addr.get("notify_disabled") else "включены"
        text = f"Адрес: {addr['addr']}\nСтатус уведомлений: {status}"
        await update_menu(ctx, chat_id, text, admin_addr_markup(target_uid, gid, aid, addr.get("notify_disabled")))
        return

    if data.startswith("admin_toggle_notify:") and user_id == admin_id_cfg:
        _, target_uid, gid, aid = data.split(":")
        target_user = state.get("users", {}).get(target_uid, {})
        group = find_group(target_user, gid)
        if group:
            addr = find_address(group, aid)
            if addr:
                addr["notify_disabled"] = not addr.get("notify_disabled", False)
                save_state(state)
                status = "отключены" if addr.get("notify_disabled") else "включены"
                text = f"Адрес: {addr['addr']}\nСтатус уведомлений: {status}"
                await update_menu(ctx, chat_id, text, admin_addr_markup(target_uid, gid, aid, addr.get("notify_disabled")))
        return

    if data.startswith("admin_del_addr:") and user_id == admin_id_cfg:
        _, target_uid, gid, aid = data.split(":")
        target_user = state.get("users", {}).get(target_uid, {})
        group = find_group(target_user, gid)
        if group:
            group["addresses"] = [a for a in group.get("addresses", []) if a.get("id") != aid]
            save_state(state)
            await update_menu(ctx, chat_id, "Адрес удален.", admin_user_markup(target_uid, target_user))
        return

    if data == "reply_to_admin":
        await remove_menu_only(ctx, chat_id)
        prompt = await ctx.bot.send_message(chat_id=chat_id, text="Введите ваше сообщение для администратора:", reply_markup=cancel_markup("menu_main"))
        ctx.chat_data["flow"] = {"action": "user_reply_admin", "prompt_id": prompt.message_id}
        return

    await update_menu(ctx, chat_id, "Главное меню:", main_menu_markup(user_id))

# ---------------- text handler ----------------
async def text_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = str(chat_id)
    user = update.effective_user
    flow = ctx.chat_data.get("flow")
    logger.info(f"Сообщение от {chat_id}: '{update.message.text}' (flow: {flow.get('action') if flow else 'None'})")
    
    state = load_state()
    user_data = state.setdefault("users", {}).setdefault(user_id, {"groups": []})
    if user:
        user_data["username"] = user.username
        user_data["first_name"] = user.first_name

    if not flow:
        await update_menu(ctx, chat_id, "Главное меню:", main_menu_markup(user_id))
        return

    text = update.message.text.strip()

    cfg = load_config()
    admin_id_cfg = str(cfg.get("ADMIN_CHAT_ID", ADMIN_ID))

    if flow.get("action") == "exchange_wait_amount":
        text_val = text.replace(',', '.')
        try:
            btc_amount = float(text_val)
            if btc_amount <= 0:
                raise ValueError()
        except ValueError:
            await ctx.bot.send_message(chat_id=chat_id, text="Пожалуйста, отправьте корректное число (например, 0.0045).", reply_markup=cancel_markup("menu_main"))
            return

        await remove_prompt(ctx, chat_id)
        wait_msg = await ctx.bot.send_message(chat_id=chat_id, text="Проверяю курс... ⏳")
        ctx.chat_data.pop("flow", None)
        
        result = await get_exchange_rate(btc_amount)
        
        try:
            await ctx.bot.delete_message(chat_id=chat_id, message_id=wait_msg.message_id)
        except Exception:
            pass

        kb = [[InlineKeyboardButton("🔙 Назад", callback_data="menu_main")]]
        await ctx.bot.send_message(chat_id=chat_id, text=f"За {btc_amount} BTC вы отдадите:\n**{result}**", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
        return

    if flow.get("action") == "add_address_name":
        name = text
        await remove_prompt(ctx, chat_id)
        prompt = await ctx.bot.send_message(chat_id=chat_id, text=f"Название: <b>{name}</b>\n\nВведите BTC адрес:", reply_markup=cancel_markup("menu_main"), parse_mode="HTML")
        ctx.chat_data["flow"] = {"action": "add_address_addr", "name": name, "prompt_id": prompt.message_id}
        return

    if flow.get("action") == "add_address_addr":
        name = flow.get("name")
        addr = text
        if len(addr) < 26 or len(addr) > 90:
            await ctx.bot.send_message(chat_id=chat_id, text="Неверный BTC адрес.")
            return
        group = next((g for g in user_data.get("groups", []) if g["name"].lower() == name.lower()), None)
        if not group:
            group = mk_group(name)
            user_data.setdefault("groups", []).append(group)
        group.setdefault("addresses", []).append(mk_address(addr))
        save_state(state)
        ctx.chat_data.pop("flow", None)
        await remove_prompt(ctx, chat_id)
        await ctx.bot.send_message(chat_id=chat_id, text=f"✅ Адрес добавлен в «{name}».")
        await update_menu(ctx, chat_id, "Главное меню:", main_menu_markup(user_id))
        return

    if flow.get("action") == "add_address_direct":
        gid = flow.get("gid")
        group = find_group(user_data, gid)
        if not group:
            ctx.chat_data.pop("flow", None)
            await remove_prompt(ctx, chat_id)
            await update_menu(ctx, chat_id, "Главное меню:", main_menu_markup(user_id))
            return
        if len(text) < 26 or len(text) > 90:
            await ctx.bot.send_message(chat_id=chat_id, text="Неверный BTC адрес.")
            return
        group.setdefault("addresses", []).append(mk_address(text))
        save_state(state)
        ctx.chat_data.pop("flow", None)
        await remove_prompt(ctx, chat_id)
        await ctx.bot.send_message(chat_id=chat_id, text=f"✅ Адрес добавлен в «{group['name']}».")
        lines = [f"{idx+1}. <code>{a['addr']}</code>" for idx, a in enumerate(group.get("addresses", []))]
        body = f"<b>{group['name']}</b>\n\nАдреса:\n" + "\n".join(lines)
        await update_menu(ctx, chat_id, body, group_view_markup(group))
        return

    if flow.get("action") == "edit_address_select":
        gid = flow.get("gid")
        group = find_group(user_data, gid)
        if not group:
            ctx.chat_data.pop("flow", None)
            await remove_prompt(ctx, chat_id)
            await update_menu(ctx, chat_id, "Главное меню:", main_menu_markup(user_id))
            return
        addr_entry = next((a for a in group.get("addresses", []) if a["addr"] == text), None)
        if not addr_entry:
            await ctx.bot.send_message(chat_id=chat_id, text="Такой адрес не найден в группе.")
            return
        await remove_prompt(ctx, chat_id)
        prompt = await ctx.bot.send_message(chat_id=chat_id, text="Введите новый BTC адрес:", reply_markup=cancel_markup(f"group:{gid}"))
        ctx.chat_data["flow"] = {"action": "edit_address_new", "gid": gid, "aid": addr_entry["id"], "prompt_id": prompt.message_id}
        return

    if flow.get("action") == "edit_address_new":
        gid = flow.get("gid")
        aid = flow.get("aid")
        group = find_group(user_data, gid)
        if not group:
            ctx.chat_data.pop("flow", None)
            await remove_prompt(ctx, chat_id)
            await update_menu(ctx, chat_id, "Главное меню:", main_menu_markup(user_id))
            return
        addr_entry = find_address(group, aid)
        if not addr_entry:
            ctx.chat_data.pop("flow", None)
            await remove_prompt(ctx, chat_id)
            await update_menu(ctx, chat_id, "Главное меню:", main_menu_markup(user_id))
            return
        if len(text) < 26 or len(text) > 90:
            await ctx.bot.send_message(chat_id=chat_id, text="Неверный BTC адрес.")
            return
        addr_entry["addr"] = text.strip()
        save_state(state)
        ctx.chat_data.pop("flow", None)
        await remove_prompt(ctx, chat_id)
        await ctx.bot.send_message(chat_id=chat_id, text="✅ Адрес заменён.")
        lines = [f"{idx+1}. <code>{a['addr']}</code>" for idx, a in enumerate(group.get("addresses", []))]
        body = f"<b>{group['name']}</b>\n\nАдреса:\n" + "\n".join(lines)
        await update_menu(ctx, chat_id, body, group_view_markup(group))
        return

    if flow.get("action") == "admin_add_name" and user_id == admin_id_cfg:
        target_uid = flow.get("target_uid")
        name = text
        await remove_prompt(ctx, chat_id)
        prompt = await ctx.bot.send_message(chat_id=chat_id, text=f"Название: <b>{name}</b>\n\nВведите BTC адрес для этого пользователя:", reply_markup=cancel_markup(f"admin_user:{target_uid}"), parse_mode="HTML")
        ctx.chat_data["flow"] = {"action": "admin_add_val", "target_uid": target_uid, "name": name, "prompt_id": prompt.message_id}
        return

    if flow.get("action") == "admin_add_val" and user_id == admin_id_cfg:
        target_uid = flow.get("target_uid")
        name = flow.get("name")
        addr = text
        if len(addr) < 26 or len(addr) > 90:
            await ctx.bot.send_message(chat_id=chat_id, text="Неверный BTC адрес.")
            return
        
        target_user = state.setdefault("users", {}).setdefault(target_uid, {"groups": []})
        group = next((g for g in target_user.get("groups", []) if g["name"].lower() == name.lower()), None)
        if not group:
            group = mk_group(name)
            target_user.setdefault("groups", []).append(group)
        group.setdefault("addresses", []).append(mk_address(addr))
        save_state(state)
        
        ctx.chat_data.pop("flow", None)
        await remove_prompt(ctx, chat_id)
        await ctx.bot.send_message(chat_id=chat_id, text=f"✅ Адрес добавлен пользователю {target_uid} в «{name}».")
        await update_menu(ctx, chat_id, f"Пользователь: {target_uid}\n\nВыберите адрес для управления:", admin_user_markup(target_uid, target_user))
        return

    if flow.get("action") == "admin_broadcast_text" and user_id == admin_id_cfg:
        selected = ctx.chat_data.get("broadcast_targets", set())
        if not selected:
            ctx.chat_data.pop("flow", None)
            await update_menu(ctx, chat_id, "Главное меню:", main_menu_markup(user_id))
            return
            
        msg_text = text
        sent_count = 0
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("💬 Ответить", callback_data="reply_to_admin")]])
        
        for uid in selected:
            try:
                await ctx.bot.send_message(chat_id=int(uid), text=msg_text, reply_markup=markup)
                sent_count += 1
            except Exception as e:
                logger.error(f"Не удалось отправить рассылку {uid}: {e}")
                
        ctx.chat_data.pop("broadcast_targets", None)
        ctx.chat_data.pop("flow", None)
        await remove_prompt(ctx, chat_id)
        await ctx.bot.send_message(chat_id=chat_id, text=f"✅ Рассылка завершена. Успешно отправлено: {sent_count}.")
        await update_menu(ctx, chat_id, "Главное меню:", main_menu_markup(user_id))
        return

    if flow.get("action") == "user_reply_admin":
        user_name = user.first_name or user.username or "Пользователь"
        msg_text = f"📩 Ответ от {user_name} ({user_id}):\n\n{text}"
        try:
            await ctx.bot.send_message(chat_id=int(admin_id_cfg), text=msg_text)
            await ctx.bot.send_message(chat_id=chat_id, text="✅ Ваше сообщение успешно отправлено администратору.")
        except Exception as e:
            logger.error(f"Не удалось переслать ответ админу от {user_id}: {e}")
            await ctx.bot.send_message(chat_id=chat_id, text="❌ Ошибка при отправке сообщения.")
            
        ctx.chat_data.pop("flow", None)
        await remove_prompt(ctx, chat_id)
        await update_menu(ctx, chat_id, "Главное меню:", main_menu_markup(user_id))
        return

    ctx.chat_data.pop("flow", None)
    await remove_prompt(ctx, chat_id)
    await update_menu(ctx, chat_id, "Главное меню:", main_menu_markup(user_id))

# ---------------- error handler ----------------
async def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"Ошибка при обработке update: {ctx.error}", exc_info=ctx.error)

# ---------------- monitoring loop ----------------
async def monitor_loop(app):
    cfg = load_config()
    api_base = cfg.get("API_BASE", "https://api.blockcypher.com/v1/btc/main")
    api_token = cfg.get("API_TOKEN", "561df65bfb5949208715c9e7e1dd07fd")
    poll_interval = int(cfg.get("POLL_INTERVAL", 20))

    client = httpx.AsyncClient(timeout=10.0)
    try:
        while True:
            try:
                state = load_state()
                # 1. Сбор всех уникальных адресов и подписчиков
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

                # 2. Курс BTC
                try:
                    r = await client.get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd")
                    btc_price = r.json()["bitcoin"]["usd"] if r.status_code == 200 else 65000
                except:
                    btc_price = 65000

                # 3. Опрос по каждому адресу
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
                                        logger.debug(f"Не удалось отправить уведомление {uid}: {e}")

                                state.setdefault("unconfirmed", {})[txid] = {
                                    "addr": addr,
                                    "amount_btc": amount_btc,
                                    "amount_usd": amount_usd
                                }
                    except Exception as e:
                        logger.debug(f"Ошибка при опросе {addr}: {e}")

                # 4. Проверка подтверждений
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

                                state.setdefault("notified_confirmed", {})[txid] = True
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

# ---------------- main ----------------
def main():
    cfg = load_config()
    bot_token = cfg["BOT_TOKEN"]
    logger.info("Инициализация бота...")
    app = ApplicationBuilder().token(bot_token).build()

    # Регистрация обработчиков
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(filters.ALL & (~filters.COMMAND), text_router))
    
    # Обработка ошибок
    app.add_error_handler(error_handler)

    async def _post_init(application):
        logger.info("Запуск фонового мониторинга транзакций...")
        application.create_task(monitor_loop(application))
        logger.info("Запуск веб-сервера для Mini App...")
        application.create_task(start_web_server(application))

    app.post_init = _post_init

    logger.info("Бот запущен и ожидает сообщений...")
    app.run_polling()

if __name__ == "__main__":
    main()