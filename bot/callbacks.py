from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from core.state import get_or_create_user, get_all_admins, is_admin, is_main_admin, get_user, add_address_group, add_address, delete_address_group, delete_address, delete_admin, update_user_blocked
from core.config import config
from bot.markups import main_menu_markup, groups_list_markup, group_view_markup, cancel_markup, admin_panel_markup, admin_broadcast_menu_markup, admin_user_markup, admin_addr_markup, admin_manage_admins_markup
from bot.handlers import update_menu, remove_menu_only, remove_prompt
from services.monitoring import initialize_address
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from core.database import async_session
from core.models import User, Admin, AddressGroup, Address
import logging

logger = logging.getLogger("btc_notify")

async def callback_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query: return
    await query.answer()
    data = query.data
    chat_id = update.effective_chat.id # type: ignore
    user = update.effective_user
    logger.info(f"Callback '{data}' от пользователя {chat_id}")
    
    await get_or_create_user(
        telegram_id=chat_id,
        username=user.username if user else None,
        first_name=user.first_name if user else None
    )
    await update_user_blocked(chat_id, False)

    is_adm = await is_admin(chat_id)

    if data == "menu_main":
        ctx.chat_data.pop("flow", None) # type: ignore
        await update_menu(ctx, chat_id, "Главное меню:", main_menu_markup(is_adm))
        return

    if data == "menu_groups":
        user_obj = await get_user(chat_id)
        if not user_obj or not user_obj.groups:
            await update_menu(ctx, chat_id, "Список адресов пуст.", main_menu_markup(is_adm))
            return
        await update_menu(ctx, chat_id, "Список адресов (нажмите название):", groups_list_markup(user_obj.groups))
        return

    if data == "menu_add_address":
        await remove_menu_only(ctx, chat_id)
        prompt = await ctx.bot.send_message(chat_id=chat_id, text="Введите название для адреса:", reply_markup=cancel_markup("menu_main"))
        ctx.chat_data["flow"] = {"action": "add_address_name", "prompt_id": prompt.message_id} # type: ignore
        return

    if data == "menu_remove_group":
        user_obj = await get_user(chat_id)
        if not user_obj or not user_obj.groups:
            await update_menu(ctx, chat_id, "Список пуст.", main_menu_markup(is_adm))
            return
        kb = [[InlineKeyboardButton(f"Удалить «{g.name}»", callback_data=f"action_delete_group:{g.id}")] for g in user_obj.groups]
        kb.append([InlineKeyboardButton("⬅ Назад", callback_data="menu_main")])
        await update_menu(ctx, chat_id, "Удаление названия:", InlineKeyboardMarkup(kb))
        return

    if data and data.startswith("group:"):
        gid = int(data.split(":", 1)[1])
        async with async_session() as session:
            stmt = select(AddressGroup).where(AddressGroup.id == gid).options(selectinload(AddressGroup.addresses))
            result = await session.execute(stmt)
            group = result.scalar_one_or_none()
            if not group:
                await update_menu(ctx, chat_id, "Название не найдено.", main_menu_markup(is_adm))
                return
            if group.addresses:
                lines = [f"{idx+1}. <code>{a.address}</code>" for idx, a in enumerate(group.addresses)]
                body = f"<b>{group.name}</b>\n\nАдреса:\n" + "\n".join(lines)
            else:
                body = f"<b>{group.name}</b>\n\nАдресов нет."
            await update_menu(ctx, chat_id, body, group_view_markup(group))
        return

    if data and data.startswith("group_add_addr:"):
        gid = int(data.split(":", 1)[1])
        async with async_session() as session:
            group = await session.get(AddressGroup, gid)
            if not group:
                await update_menu(ctx, chat_id, "Группа не найдена.", main_menu_markup(is_adm))
                return
            await remove_menu_only(ctx, chat_id)
            prompt = await ctx.bot.send_message(chat_id=chat_id, text=f"Введите BTC адрес для «{group.name}»:", reply_markup=cancel_markup(f"group:{gid}"))
            ctx.chat_data["flow"] = {"action": "add_address_direct", "gid": gid, "prompt_id": prompt.message_id} # type: ignore
        return

    if data and data.startswith("group_edit_addr:"):
        gid = int(data.split(":", 1)[1])
        async with async_session() as session:
            stmt = select(AddressGroup).where(AddressGroup.id == gid).options(selectinload(AddressGroup.addresses))
            result = await session.execute(stmt)
            group = result.scalar_one_or_none()
            if not group or not group.addresses:
                await update_menu(ctx, chat_id, "Нет адресов для замены.", main_menu_markup(is_adm))
                return
            await remove_menu_only(ctx, chat_id)
            prompt = await ctx.bot.send_message(chat_id=chat_id, text="Выберите адрес для замены (введите старый адрес):", reply_markup=cancel_markup(f"group:{gid}"))
            ctx.chat_data["flow"] = {"action": "edit_address_select", "gid": gid, "prompt_id": prompt.message_id} # type: ignore
        return

    if data and data.startswith("group_del_addr:"):
        gid = int(data.split(":", 1)[1])
        async with async_session() as session:
            stmt = select(AddressGroup).where(AddressGroup.id == gid).options(selectinload(AddressGroup.addresses))
            result = await session.execute(stmt)
            group = result.scalar_one_or_none()
            if not group:
                return
            kb = [[InlineKeyboardButton(f"🗑 {a.address[:12]}...", callback_data=f"addr_del:{gid}:{a.id}")] for a in group.addresses]
            kb.append([InlineKeyboardButton("⬅ Назад", callback_data=f"group:{gid}")])
            await update_menu(ctx, chat_id, f"Выберите адрес для удаления в «{group.name}»:", InlineKeyboardMarkup(kb))
        return

    if data and (data.startswith("action_delete_group:") or data.startswith("group_del:")):
        gid = int(data.split(":", 1)[1])
        await delete_address_group(gid)
        await update_menu(ctx, chat_id, "Название удалено.", main_menu_markup(is_adm))
        return

    if data and data.startswith("addr_del:"):
        _, gid, aid = data.split(":")
        gid, aid = int(gid), int(aid)
        await delete_address(aid)
        
        async with async_session() as session:
            stmt = select(AddressGroup).where(AddressGroup.id == gid).options(selectinload(AddressGroup.addresses))
            result = await session.execute(stmt)
            group = result.scalar_one_or_none()
            if group:
                await update_menu(ctx, chat_id, "Адрес удалён.", group_view_markup(group))
        return

    if data and data.startswith("conf:"):
        confirmations = int(data.split(":")[1])
        flow = ctx.chat_data.get("flow") # type: ignore
        if not flow or flow.get("action") != "waiting_for_confirmations":
            await update_menu(ctx, chat_id, "Главное меню:", main_menu_markup(is_adm))
            return
        
        addr = flow.get("addr")
        
        if flow.get("target_uid"): # Admin adding for user
            target_uid = int(flow.get("target_uid"))
            name = flow.get("name")
            
            async with async_session() as session:
                stmt = select(User).where(User.telegram_id == target_uid).options(selectinload(User.groups).selectinload(AddressGroup.addresses))
                result = await session.execute(stmt)
                target_user = result.scalar_one_or_none()
                if not target_user:
                    target_user = User(telegram_id=target_uid)
                    session.add(target_user)
                    await session.flush()
                
                group = next((g for g in target_user.groups if g.name.lower() == name.lower()), None)
                if not group:
                    group = AddressGroup(user_id=target_user.id, name=name)
                    session.add(group)
                    await session.flush()
                
                new_addr = Address(group_id=group.id, address=addr, confirmations_target=confirmations)
                session.add(new_addr)
                await session.commit()
                
                await initialize_address(addr, target_uid, target=confirmations, group_name=name, app=ctx.application)
                ctx.chat_data.pop("flow", None) # type: ignore
                await remove_prompt(ctx, chat_id)
                await ctx.bot.send_message(chat_id=chat_id, text=f"✅ Адрес добавлен пользователю {target_uid} в «{name}» ({confirmations} подтв.).")
                
                await session.refresh(target_user)
                await update_menu(ctx, chat_id, f"Пользователь: {target_uid}\n\nВыберите адрес для управления:", admin_user_markup(target_uid, target_user)) # type: ignore
            return
            
        elif flow.get("gid"): # User adding to existing group
            gid = int(flow.get("gid"))
            async with async_session() as session:
                stmt = select(AddressGroup).where(AddressGroup.id == gid).options(selectinload(AddressGroup.addresses))
                result = await session.execute(stmt)
                group = result.scalar_one_or_none()
                if group:
                    new_addr = Address(group_id=group.id, address=addr, confirmations_target=confirmations)
                    session.add(new_addr)
                    await session.commit()
                    
                    await initialize_address(addr, chat_id, target=confirmations, group_name=group.name, app=ctx.application)
                    ctx.chat_data.pop("flow", None) # type: ignore
                    await remove_prompt(ctx, chat_id)
                    await ctx.bot.send_message(chat_id=chat_id, text=f"✅ Адрес добавлен в «{group.name}» ({confirmations} подтв.).")
                    
                    await session.refresh(group)
                    lines = [f"{idx+1}. <code>{a.address}</code>" for idx, a in enumerate(group.addresses)]
                    body = f"<b>{group.name}</b>\n\nАдреса:\n" + "\n".join(lines)
                    await update_menu(ctx, chat_id, body, group_view_markup(group)) # type: ignore
                return
        
        else: # User adding to new group
            name = flow.get("name")
            user_obj = await get_user(chat_id)
            async with async_session() as session:
                group = next((g for g in user_obj.groups if g.name.lower() == name.lower()), None)
                if not group:
                    group = AddressGroup(user_id=user_obj.id, name=name)
                    session.add(group)
                    await session.flush()
                
                new_addr = Address(group_id=group.id, address=addr, confirmations_target=confirmations)
                session.add(new_addr)
                await session.commit()
                
                await initialize_address(addr, chat_id, target=confirmations, group_name=name, app=ctx.application)
                ctx.chat_data.pop("flow", None) # type: ignore
                await remove_prompt(ctx, chat_id)
                await ctx.bot.send_message(chat_id=chat_id, text=f"✅ Адрес добавлен в «{name}» ({confirmations} подтв.).")
                await update_menu(ctx, chat_id, "Главное меню:", main_menu_markup(is_adm))
            return

    if data and data.startswith("cancel:"):
        target = data.split(":", 1)[1]
        ctx.chat_data.pop("flow", None) # type: ignore
        await remove_prompt(ctx, chat_id)
        if target.startswith("group:"):
            gid = int(target.split(":")[1])
            async with async_session() as session:
                stmt = select(AddressGroup).where(AddressGroup.id == gid).options(selectinload(AddressGroup.addresses))
                result = await session.execute(stmt)
                group = result.scalar_one_or_none()
                if group:
                    lines = [f"{idx+1}. <code>{a.address}</code>" for idx, a in enumerate(group.addresses)]
                    body = f"<b>{group.name}</b>\n\nАдреса:\n" + "\n".join(lines) if lines else f"<b>{group.name}</b>\n\nАдресов нет."
                    await update_menu(ctx, chat_id, body, group_view_markup(group))
                    return
                else:
                    await update_menu(ctx, chat_id, "Главное меню:", main_menu_markup(is_adm))
                    return
        elif target == "admin_panel":
            async with async_session() as session:
                stmt = select(User).options(selectinload(User.groups).selectinload(AddressGroup.addresses))
                result = await session.execute(stmt)
                users = result.scalars().all()
                await update_menu(ctx, chat_id, "👑 Админ панель - Список пользователей:", admin_panel_markup(users, is_main_admin(chat_id)))
            return
        elif target.startswith("admin_user:"):
            target_uid = int(target.split(":")[1])
            target_user = await get_user(target_uid)
            if target_user:
                name = target_user.first_name or target_user.username or str(target_uid)
                text = f"Пользователь: {name}\nID: {target_uid}\n\nВыберите адрес для управления:"
                await update_menu(ctx, chat_id, text, admin_user_markup(target_uid, target_user)) # type: ignore
            return
        
        await update_menu(ctx, chat_id, "Главное меню:", main_menu_markup(is_adm))
        return

    if data == "admin_panel" and is_adm:
        async with async_session() as session:
            stmt = select(User).options(selectinload(User.groups).selectinload(AddressGroup.addresses))
            result = await session.execute(stmt)
            users = result.scalars().all()
            await update_menu(ctx, chat_id, "👑 Админ панель - Список пользователей:", admin_panel_markup(users, is_main_admin(chat_id)))
        return

    if data == "admin_manage_admins" and is_main_admin(chat_id):
        all_admins_list = await get_all_admins()
        await update_menu(ctx, chat_id, "Управление администраторами:", admin_manage_admins_markup(all_admins_list, config.ADMIN_CHAT_ID))
        return

    if data and data.startswith("admin_del_admin:") and is_main_admin(chat_id):
        del_uid = int(data.split(":", 1)[1])
        await delete_admin(del_uid)
        all_admins_list = await get_all_admins()
        await update_menu(ctx, chat_id, "Управление администраторами:", admin_manage_admins_markup(all_admins_list, config.ADMIN_CHAT_ID))
        return

    if data == "admin_add_admin_prompt" and is_main_admin(chat_id):
        await remove_menu_only(ctx, chat_id)
        prompt = await ctx.bot.send_message(chat_id=chat_id, text="Введите Telegram ID нового администратора (только цифры):", reply_markup=cancel_markup("admin_panel"))
        ctx.chat_data["flow"] = {"action": "admin_add_admin_input", "prompt_id": prompt.message_id} # type: ignore
        return

    if data == "admin_broadcast_menu" and is_adm:
        selected = ctx.chat_data.setdefault("broadcast_targets", set()) # type: ignore
        async with async_session() as session:
            stmt = select(User)
            result = await session.execute(stmt)
            users = result.scalars().all()
            await update_menu(ctx, chat_id, "Выберите пользователей для рассылки:", admin_broadcast_menu_markup(users, selected))
        return

    if data and data.startswith("admin_broadcast_toggle:") and is_adm:
        target_uid = data.split(":", 1)[1]
        selected = ctx.chat_data.setdefault("broadcast_targets", set()) # type: ignore
        if target_uid in selected:
            selected.remove(target_uid)
        else:
            selected.add(target_uid)
        async with async_session() as session:
            stmt = select(User)
            result = await session.execute(stmt)
            users = result.scalars().all()
            await update_menu(ctx, chat_id, "Выберите пользователей для рассылки:", admin_broadcast_menu_markup(users, selected))
        return

    if data == "admin_broadcast_select_all" and is_adm:
        selected = ctx.chat_data.setdefault("broadcast_targets", set()) # type: ignore
        async with async_session() as session:
            stmt = select(User.telegram_id)
            result = await session.execute(stmt)
            all_uids = set(str(uid) for uid in result.scalars().all())
            if selected == all_uids:
                selected.clear()
            else:
                selected.update(all_uids)
            
            stmt = select(User)
            result = await session.execute(stmt)
            users = result.scalars().all()
            await update_menu(ctx, chat_id, "Выберите пользователей для рассылки:", admin_broadcast_menu_markup(users, selected))
        return

    if data == "admin_broadcast_write" and is_adm:
        selected = ctx.chat_data.get("broadcast_targets", set()) # type: ignore
        if not selected:
            async with async_session() as session:
                stmt = select(User)
                result = await session.execute(stmt)
                users = result.scalars().all()
                await update_menu(ctx, chat_id, "Никто не выбран.", admin_broadcast_menu_markup(users, selected))
            return
        await remove_menu_only(ctx, chat_id)
        prompt = await ctx.bot.send_message(chat_id=chat_id, text=f"Введите текст рассылки для {len(selected)} пользователей:", reply_markup=cancel_markup("admin_panel"))
        ctx.chat_data["flow"] = {"action": "admin_broadcast_text", "prompt_id": prompt.message_id} # type: ignore
        return

    if data and data.startswith("admin_add_addr:") and is_adm:
        target_uid = data.split(":", 1)[1]
        await remove_menu_only(ctx, chat_id)
        prompt = await ctx.bot.send_message(chat_id=chat_id, text="Введите название группы для этого пользователя:", reply_markup=cancel_markup(f"admin_user:{target_uid}"))
        ctx.chat_data["flow"] = {"action": "admin_add_name", "target_uid": target_uid, "prompt_id": prompt.message_id} # type: ignore
        return

    if data and data.startswith("admin_user:") and is_adm:
        target_uid = int(data.split(":", 1)[1])
        target_user = await get_user(target_uid)
        if target_user:
            name = target_user.first_name or target_user.username or str(target_uid)
            text = f"Пользователь: {name}\nID: {target_uid}\n\nВыберите адрес для управления:"
            await update_menu(ctx, chat_id, text, admin_user_markup(target_uid, target_user)) # type: ignore
        return

    if data and data.startswith("admin_addr:") and is_adm:
        _, target_uid, gid, aid = data.split(":")
        target_uid, gid, aid = int(target_uid), int(gid), int(aid)
        async with async_session() as session:
            addr = await session.get(Address, aid)
            if not addr: return
            
            status = "отключены" if addr.notify_disabled else "включены"
            text = f"Адрес: {addr.address}\nСтатус уведомлений: {status}"
            await update_menu(ctx, chat_id, text, admin_addr_markup(target_uid, gid, aid, addr.notify_disabled)) # type: ignore
        return

    if data and data.startswith("admin_toggle_notify:") and is_adm:
        _, target_uid, gid, aid = data.split(":")
        target_uid, gid, aid = int(target_uid), int(gid), int(aid)
        async with async_session() as session:
            addr = await session.get(Address, aid)
            if addr:
                addr.notify_disabled = not addr.notify_disabled
                await session.commit()
                status = "отключены" if addr.notify_disabled else "включены"
                text = f"Адрес: {addr.address}\nСтатус уведомлений: {status}"
                await update_menu(ctx, chat_id, text, admin_addr_markup(target_uid, gid, aid, addr.notify_disabled)) # type: ignore
        return

    if data and data.startswith("admin_del_addr:") and is_adm:
        _, target_uid, gid, aid = data.split(":")
        target_uid, gid, aid = int(target_uid), int(gid), int(aid)
        await delete_address(aid)
        
        target_user = await get_user(target_uid)
        await update_menu(ctx, chat_id, "Адрес удален.", admin_user_markup(target_uid, target_user)) # type: ignore
        return

    if data == "reply_to_admin":
        await remove_menu_only(ctx, chat_id)
        prompt = await ctx.bot.send_message(chat_id=chat_id, text="Введите ваше сообщение для администратора:", reply_markup=cancel_markup("menu_main"))
        ctx.chat_data["flow"] = {"action": "user_reply_admin", "prompt_id": prompt.message_id} # type: ignore
        return

    await update_menu(ctx, chat_id, "Главное меню:", main_menu_markup(is_adm))
