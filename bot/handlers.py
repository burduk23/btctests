import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from core.state import load_state, save_state
from core.config import config
from bot.markups import main_menu_markup, cancel_markup, group_view_markup, admin_user_markup
from services.address import mk_group, mk_address, find_group, find_address
from services.exchange import BrowserService

logger = logging.getLogger("btc_notify")

async def update_menu(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, markup: InlineKeyboardMarkup = None, parse_mode="HTML"):
    menu_id = ctx.chat_data.get("menu_id") # type: ignore
    if menu_id:
        try:
            await ctx.bot.edit_message_text(chat_id=chat_id, message_id=menu_id, text=text, reply_markup=markup, parse_mode=parse_mode)
            return
        except Exception:
            ctx.chat_data.pop("menu_id", None) # type: ignore
    msg = await ctx.bot.send_message(chat_id=chat_id, text=text, reply_markup=markup, parse_mode=parse_mode)
    ctx.chat_data["menu_id"] = msg.message_id # type: ignore

async def remove_menu_only(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int):
    menu_id = ctx.chat_data.pop("menu_id", None) # type: ignore
    if menu_id:
        try:
            await ctx.bot.delete_message(chat_id=chat_id, message_id=menu_id)
        except Exception:
            pass

async def remove_prompt(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int):
    prompt_id = ctx.chat_data.pop("prompt_id", None) # type: ignore
    if prompt_id:
        try:
            await ctx.bot.delete_message(chat_id=chat_id, message_id=prompt_id)
        except Exception:
            pass

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id # type: ignore
    user = update.effective_user
    logger.info(f"Команда /start от пользователя {chat_id}")
    
    state = load_state()
    user_id = str(chat_id)
    user_data = state.setdefault("users", {}).setdefault(user_id, {"groups": []}) # type: ignore
    if user:
        user_data["username"] = user.username
        user_data["first_name"] = user.first_name
    save_state(state)
    
    ctx.chat_data.pop("flow", None) # type: ignore
    await remove_prompt(ctx, chat_id)
    await update_menu(ctx, chat_id, "Главное меню:", main_menu_markup(user_id))

async def text_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id # type: ignore
    user_id = str(chat_id)
    user = update.effective_user
    flow = ctx.chat_data.get("flow") # type: ignore
    logger.info(f"Сообщение от {chat_id}: '{update.message.text}' (flow: {flow.get('action') if flow else 'None'})") # type: ignore
    
    state = load_state()
    user_data = state.setdefault("users", {}).setdefault(user_id, {"groups": []}) # type: ignore
    if user:
        user_data["username"] = user.username
        user_data["first_name"] = user.first_name

    if not flow:
        await update_menu(ctx, chat_id, "Главное меню:", main_menu_markup(user_id))
        return

    text = update.message.text.strip() # type: ignore

    admin_id_cfg = str(config.ADMIN_CHAT_ID)

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
        ctx.chat_data.pop("flow", None) # type: ignore
        
        result = await BrowserService.get_exchange_rate(btc_amount)
        
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
        ctx.chat_data["flow"] = {"action": "add_address_addr", "name": name, "prompt_id": prompt.message_id} # type: ignore
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
            user_data.setdefault("groups", []).append(group) # type: ignore
        group.setdefault("addresses", []).append(mk_address(addr)) # type: ignore
        save_state(state)
        ctx.chat_data.pop("flow", None) # type: ignore
        await remove_prompt(ctx, chat_id)
        await ctx.bot.send_message(chat_id=chat_id, text=f"✅ Адрес добавлен в «{name}».")
        await update_menu(ctx, chat_id, "Главное меню:", main_menu_markup(user_id))
        return

    if flow.get("action") == "add_address_direct":
        gid = flow.get("gid")
        group = find_group(user_data, gid) # type: ignore
        if not group:
            ctx.chat_data.pop("flow", None) # type: ignore
            await remove_prompt(ctx, chat_id)
            await update_menu(ctx, chat_id, "Главное меню:", main_menu_markup(user_id))
            return
        if len(text) < 26 or len(text) > 90:
            await ctx.bot.send_message(chat_id=chat_id, text="Неверный BTC адрес.")
            return
        group.setdefault("addresses", []).append(mk_address(text)) # type: ignore
        save_state(state)
        ctx.chat_data.pop("flow", None) # type: ignore
        await remove_prompt(ctx, chat_id)
        await ctx.bot.send_message(chat_id=chat_id, text=f"✅ Адрес добавлен в «{group['name']}».")
        lines = [f"{idx+1}. <code>{a['addr']}</code>" for idx, a in enumerate(group.get("addresses", []))]
        body = f"<b>{group['name']}</b>\n\nАдреса:\n" + "\n".join(lines)
        await update_menu(ctx, chat_id, body, group_view_markup(group)) # type: ignore
        return

    if flow.get("action") == "edit_address_select":
        gid = flow.get("gid")
        group = find_group(user_data, gid) # type: ignore
        if not group:
            ctx.chat_data.pop("flow", None) # type: ignore
            await remove_prompt(ctx, chat_id)
            await update_menu(ctx, chat_id, "Главное меню:", main_menu_markup(user_id))
            return
        addr_entry = next((a for a in group.get("addresses", []) if a["addr"] == text), None)
        if not addr_entry:
            await ctx.bot.send_message(chat_id=chat_id, text="Такой адрес не найден в группе.")
            return
        await remove_prompt(ctx, chat_id)
        prompt = await ctx.bot.send_message(chat_id=chat_id, text="Введите новый BTC адрес:", reply_markup=cancel_markup(f"group:{gid}"))
        ctx.chat_data["flow"] = {"action": "edit_address_new", "gid": gid, "aid": addr_entry["id"], "prompt_id": prompt.message_id} # type: ignore
        return

    if flow.get("action") == "edit_address_new":
        gid = flow.get("gid")
        aid = flow.get("aid")
        group = find_group(user_data, gid) # type: ignore
        if not group:
            ctx.chat_data.pop("flow", None) # type: ignore
            await remove_prompt(ctx, chat_id)
            await update_menu(ctx, chat_id, "Главное меню:", main_menu_markup(user_id))
            return
        addr_entry = find_address(group, aid) # type: ignore
        if not addr_entry:
            ctx.chat_data.pop("flow", None) # type: ignore
            await remove_prompt(ctx, chat_id)
            await update_menu(ctx, chat_id, "Главное меню:", main_menu_markup(user_id))
            return
        if len(text) < 26 or len(text) > 90:
            await ctx.bot.send_message(chat_id=chat_id, text="Неверный BTC адрес.")
            return
        addr_entry["addr"] = text.strip()
        save_state(state)
        ctx.chat_data.pop("flow", None) # type: ignore
        await remove_prompt(ctx, chat_id)
        await ctx.bot.send_message(chat_id=chat_id, text="✅ Адрес заменён.")
        lines = [f"{idx+1}. <code>{a['addr']}</code>" for idx, a in enumerate(group.get("addresses", []))]
        body = f"<b>{group['name']}</b>\n\nАдреса:\n" + "\n".join(lines)
        await update_menu(ctx, chat_id, body, group_view_markup(group)) # type: ignore
        return

    if flow.get("action") == "admin_add_name" and user_id == admin_id_cfg:
        target_uid = flow.get("target_uid")
        name = text
        await remove_prompt(ctx, chat_id)
        prompt = await ctx.bot.send_message(chat_id=chat_id, text=f"Название: <b>{name}</b>\n\nВведите BTC адрес для этого пользователя:", reply_markup=cancel_markup(f"admin_user:{target_uid}"), parse_mode="HTML")
        ctx.chat_data["flow"] = {"action": "admin_add_val", "target_uid": target_uid, "name": name, "prompt_id": prompt.message_id} # type: ignore
        return

    if flow.get("action") == "admin_add_val" and user_id == admin_id_cfg:
        target_uid = flow.get("target_uid")
        name = flow.get("name")
        addr = text
        if len(addr) < 26 or len(addr) > 90:
            await ctx.bot.send_message(chat_id=chat_id, text="Неверный BTC адрес.")
            return
        
        target_user = state.setdefault("users", {}).setdefault(target_uid, {"groups": []}) # type: ignore
        group = next((g for g in target_user.get("groups", []) if g["name"].lower() == name.lower()), None)
        if not group:
            group = mk_group(name)
            target_user.setdefault("groups", []).append(group) # type: ignore
        group.setdefault("addresses", []).append(mk_address(addr)) # type: ignore
        save_state(state)
        
        ctx.chat_data.pop("flow", None) # type: ignore
        await remove_prompt(ctx, chat_id)
        await ctx.bot.send_message(chat_id=chat_id, text=f"✅ Адрес добавлен пользователю {target_uid} в «{name}».")
        await update_menu(ctx, chat_id, f"Пользователь: {target_uid}\n\nВыберите адрес для управления:", admin_user_markup(target_uid, target_user)) # type: ignore
        return

    if flow.get("action") == "admin_broadcast_text" and user_id == admin_id_cfg:
        selected = ctx.chat_data.get("broadcast_targets", set()) # type: ignore
        if not selected:
            ctx.chat_data.pop("flow", None) # type: ignore
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
                
        ctx.chat_data.pop("broadcast_targets", None) # type: ignore
        ctx.chat_data.pop("flow", None) # type: ignore
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
            
        ctx.chat_data.pop("flow", None) # type: ignore
        await remove_prompt(ctx, chat_id)
        await update_menu(ctx, chat_id, "Главное меню:", main_menu_markup(user_id))
        return

    ctx.chat_data.pop("flow", None) # type: ignore
    await remove_prompt(ctx, chat_id)
    await update_menu(ctx, chat_id, "Главное меню:", main_menu_markup(user_id))

async def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"Ошибка при обработке update: {ctx.error}", exc_info=ctx.error)
