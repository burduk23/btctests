import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from core.state import load_state, save_state
from core.config import config
from bot.markups import main_menu_markup, groups_list_markup, group_view_markup, cancel_markup, admin_panel_markup, admin_broadcast_menu_markup, admin_user_markup, admin_addr_markup
from bot.handlers import update_menu, remove_menu_only, remove_prompt
from services.address import find_group, find_address

logger = logging.getLogger("btc_notify")

async def callback_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query: return
    await query.answer()
    data = query.data
    chat_id = update.effective_chat.id # type: ignore
    user = update.effective_user
    logger.info(f"Callback '{data}' от пользователя {chat_id}")
    
    state = load_state()
    user_id = str(chat_id)
    user_data = state.setdefault("users", {}).setdefault(user_id, {"groups": []}) # type: ignore
    if user:
        user_data["username"] = user.username
        user_data["first_name"] = user.first_name

    if data == "menu_main":
        ctx.chat_data.pop("flow", None) # type: ignore
        await update_menu(ctx, chat_id, "Главное меню:", main_menu_markup(user_id))
        return

    if data == "exchange":
        await remove_menu_only(ctx, chat_id)
        prompt = await ctx.bot.send_message(chat_id=chat_id, text="Введите сумму в Bitcoin (например 0.0045).", reply_markup=cancel_markup("menu_main"))
        ctx.chat_data["flow"] = {"action": "exchange_wait_amount", "prompt_id": prompt.message_id} # type: ignore
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
        ctx.chat_data["flow"] = {"action": "add_address_name", "prompt_id": prompt.message_id} # type: ignore
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

    if data and data.startswith("group:"):
        gid = data.split(":", 1)[1]
        group = find_group(user_data, gid) # type: ignore
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

    if data and data.startswith("group_add_addr:"):
        gid = data.split(":", 1)[1]
        group = find_group(user_data, gid) # type: ignore
        if not group:
            await update_menu(ctx, chat_id, "Группа не найдена.", main_menu_markup(user_id))
            return
        await remove_menu_only(ctx, chat_id)
        prompt = await ctx.bot.send_message(chat_id=chat_id, text=f"Введите BTC адрес для «{group['name']}»:", reply_markup=cancel_markup(f"group:{gid}"))
        ctx.chat_data["flow"] = {"action": "add_address_direct", "gid": gid, "prompt_id": prompt.message_id} # type: ignore
        return

    if data and data.startswith("group_edit_addr:"):
        gid = data.split(":", 1)[1]
        group = find_group(user_data, gid) # type: ignore
        if not group or not group.get("addresses"):
            await update_menu(ctx, chat_id, "Нет адресов для замены.", main_menu_markup(user_id))
            return
        await remove_menu_only(ctx, chat_id)
        prompt = await ctx.bot.send_message(chat_id=chat_id, text="Выберите адрес для замены (введите старый адрес):", reply_markup=cancel_markup(f"group:{gid}"))
        ctx.chat_data["flow"] = {"action": "edit_address_select", "gid": gid, "prompt_id": prompt.message_id} # type: ignore
        return

    if data and data.startswith("group_del_addr:"):
        gid = data.split(":", 1)[1]
        group = find_group(user_data, gid) # type: ignore
        if not group:
            return
        kb = [[InlineKeyboardButton(f"🗑 {a['addr'][:12]}...", callback_data=f"addr_del:{gid}:{a['id']}")] for a in group.get("addresses", [])]
        kb.append([InlineKeyboardButton("⬅ Назад", callback_data=f"group:{gid}")])
        await update_menu(ctx, chat_id, f"Выберите адрес для удаления в «{group['name']}»:", InlineKeyboardMarkup(kb))
        return

    if data and (data.startswith("action_delete_group:") or data.startswith("group_del:")):
        gid = data.split(":", 1)[1]
        user_data["groups"] = [g for g in user_data.get("groups", []) if g.get("id") != gid]
        save_state(state)
        await update_menu(ctx, chat_id, "Название удалено.", main_menu_markup(user_id))
        return

    if data and data.startswith("addr_del:"):
        _, gid, aid = data.split(":")
        group = find_group(user_data, gid) # type: ignore
        if group:
            group["addresses"] = [a for a in group.get("addresses", []) if a.get("id") != aid]
            save_state(state)
            await update_menu(ctx, chat_id, "Адрес удалён.", group_view_markup(group))
        return

    if data and data.startswith("cancel:"):
        target = data.split(":", 1)[1]
        ctx.chat_data.pop("flow", None) # type: ignore
        await remove_prompt(ctx, chat_id)
        if target.startswith("group:"):
            gid = target.split(":")[1]
            group = find_group(user_data, gid) # type: ignore
            if group:
                lines = [f"{idx+1}. <code>{a['addr']}</code>" for idx, a in enumerate(group.get("addresses", []))]
                body = f"<b>{group['name']}</b>\n\nАдреса:\n" + "\n".join(lines) if lines else f"<b>{group['name']}</b>\n\nАдресов нет."
                await update_menu(ctx, chat_id, body, group_view_markup(group))
                return
            else:
                await update_menu(ctx, chat_id, "Главное меню:", main_menu_markup(user_id))
                return
        elif target == "admin_panel":
            await update_menu(ctx, chat_id, "👑 Админ панель - Список пользователей:", admin_panel_markup(state.get("users", {})))
            return
        elif target.startswith("admin_user:"):
            target_uid = target.split(":")[1]
            target_user = state.get("users", {}).get(target_uid, {})
            name = target_user.get("first_name") or target_user.get("username") or target_uid
            text = f"Пользователь: {name}\nID: {target_uid}\n\nВыберите адрес для управления:"
            await update_menu(ctx, chat_id, text, admin_user_markup(target_uid, target_user)) # type: ignore
            return
        
        await update_menu(ctx, chat_id, "Главное меню:", main_menu_markup(user_id))
        return

    admin_id_cfg = str(config.ADMIN_CHAT_ID)
    
    if data == "admin_panel" and user_id == admin_id_cfg:
        await update_menu(ctx, chat_id, "👑 Админ панель - Список пользователей:", admin_panel_markup(state.get("users", {})))
        return
        
    if data == "admin_broadcast_menu" and user_id == admin_id_cfg:
        selected = ctx.chat_data.setdefault("broadcast_targets", set()) # type: ignore
        await update_menu(ctx, chat_id, "Выберите пользователей для рассылки:", admin_broadcast_menu_markup(state.get("users", {}), selected))
        return

    if data and data.startswith("admin_broadcast_toggle:") and user_id == admin_id_cfg:
        target_uid = data.split(":", 1)[1]
        selected = ctx.chat_data.setdefault("broadcast_targets", set()) # type: ignore
        if target_uid in selected:
            selected.remove(target_uid)
        else:
            selected.add(target_uid)
        await update_menu(ctx, chat_id, "Выберите пользователей для рассылки:", admin_broadcast_menu_markup(state.get("users", {}), selected))
        return

    if data == "admin_broadcast_select_all" and user_id == admin_id_cfg:
        selected = ctx.chat_data.setdefault("broadcast_targets", set()) # type: ignore
        all_uids = set(state.get("users", {}).keys())
        if selected == all_uids:
            selected.clear()
        else:
            selected.update(all_uids)
        await update_menu(ctx, chat_id, "Выберите пользователей для рассылки:", admin_broadcast_menu_markup(state.get("users", {}), selected))
        return

    if data == "admin_broadcast_write" and user_id == admin_id_cfg:
        selected = ctx.chat_data.get("broadcast_targets", set()) # type: ignore
        if not selected:
            await update_menu(ctx, chat_id, "Никто не выбран.", admin_broadcast_menu_markup(state.get("users", {}), selected))
            return
        await remove_menu_only(ctx, chat_id)
        prompt = await ctx.bot.send_message(chat_id=chat_id, text=f"Введите текст рассылки для {len(selected)} пользователей:", reply_markup=cancel_markup("admin_panel"))
        ctx.chat_data["flow"] = {"action": "admin_broadcast_text", "prompt_id": prompt.message_id} # type: ignore
        return

    if data and data.startswith("admin_add_addr:") and user_id == admin_id_cfg:
        target_uid = data.split(":", 1)[1]
        await remove_menu_only(ctx, chat_id)
        prompt = await ctx.bot.send_message(chat_id=chat_id, text="Введите название группы для этого пользователя:", reply_markup=cancel_markup(f"admin_user:{target_uid}"))
        ctx.chat_data["flow"] = {"action": "admin_add_name", "target_uid": target_uid, "prompt_id": prompt.message_id} # type: ignore
        return

    if data and data.startswith("admin_user:") and user_id == admin_id_cfg:
        target_uid = data.split(":", 1)[1]
        target_user = state.get("users", {}).get(target_uid, {})
        name = target_user.get("first_name") or target_user.get("username") or target_uid
        text = f"Пользователь: {name}\nID: {target_uid}\n\nВыберите адрес для управления:"
        await update_menu(ctx, chat_id, text, admin_user_markup(target_uid, target_user)) # type: ignore
        return

    if data and data.startswith("admin_addr:") and user_id == admin_id_cfg:
        _, target_uid, gid, aid = data.split(":")
        target_user = state.get("users", {}).get(target_uid, {})
        group = find_group(target_user, gid) # type: ignore
        if not group: return
        addr = find_address(group, aid)
        if not addr: return
        
        status = "отключены" if addr.get("notify_disabled") else "включены"
        text = f"Адрес: {addr['addr']}\nСтатус уведомлений: {status}"
        await update_menu(ctx, chat_id, text, admin_addr_markup(target_uid, gid, aid, addr.get("notify_disabled"))) # type: ignore
        return

    if data and data.startswith("admin_toggle_notify:") and user_id == admin_id_cfg:
        _, target_uid, gid, aid = data.split(":")
        target_user = state.get("users", {}).get(target_uid, {})
        group = find_group(target_user, gid) # type: ignore
        if group:
            addr = find_address(group, aid)
            if addr:
                addr["notify_disabled"] = not addr.get("notify_disabled", False)
                save_state(state)
                status = "отключены" if addr.get("notify_disabled") else "включены"
                text = f"Адрес: {addr['addr']}\nСтатус уведомлений: {status}"
                await update_menu(ctx, chat_id, text, admin_addr_markup(target_uid, gid, aid, addr.get("notify_disabled"))) # type: ignore
        return

    if data and data.startswith("admin_del_addr:") and user_id == admin_id_cfg:
        _, target_uid, gid, aid = data.split(":")
        target_user = state.get("users", {}).get(target_uid, {})
        group = find_group(target_user, gid) # type: ignore
        if group:
            group["addresses"] = [a for a in group.get("addresses", []) if a.get("id") != aid]
            save_state(state)
            await update_menu(ctx, chat_id, "Адрес удален.", admin_user_markup(target_uid, target_user)) # type: ignore
        return

    if data == "reply_to_admin":
        await remove_menu_only(ctx, chat_id)
        prompt = await ctx.bot.send_message(chat_id=chat_id, text="Введите ваше сообщение для администратора:", reply_markup=cancel_markup("menu_main"))
        ctx.chat_data["flow"] = {"action": "user_reply_admin", "prompt_id": prompt.message_id} # type: ignore
        return

    await update_menu(ctx, chat_id, "Главное меню:", main_menu_markup(user_id))
