import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from telegram.error import Forbidden
from core.state import get_or_create_user, get_all_admins, is_admin, is_main_admin, get_user, add_address_group, update_user_blocked
from core.config import config
from bot.markups import main_menu_markup, cancel_markup, group_view_markup, admin_user_markup, admin_manage_admins_markup, confirmations_markup
from services.monitoring import initialize_address
from sqlalchemy import select, delete
from core.database import async_session
from core.models import User, Admin, AddressGroup, Address

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
    
    await get_or_create_user(
        telegram_id=chat_id,
        username=user.username if user else None,
        first_name=user.first_name if user else None
    )
    await update_user_blocked(chat_id, False)
    
    ctx.chat_data.pop("flow", None) # type: ignore
    await remove_prompt(ctx, chat_id)
    
    is_adm = await is_admin(chat_id)
    await update_menu(ctx, chat_id, "Главное меню:", main_menu_markup(is_adm))

async def text_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id # type: ignore
    user = update.effective_user
    flow = ctx.chat_data.get("flow") # type: ignore
    logger.info(f"Сообщение от {chat_id}: '{update.message.text}' (flow: {flow.get('action') if flow else 'None'})") # type: ignore
    
    await get_or_create_user(
        telegram_id=chat_id,
        username=user.username if user else None,
        first_name=user.first_name if user else None
    )
    await update_user_blocked(chat_id, False)

    is_adm = await is_admin(chat_id)
    if not flow:
        await update_menu(ctx, chat_id, "Главное меню:", main_menu_markup(is_adm))
        return

    text = update.message.text.strip() # type: ignore

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
        await remove_prompt(ctx, chat_id)
        prompt = await ctx.bot.send_message(chat_id=chat_id, text="Выберите количество подтверждений для уведомлений:", reply_markup=confirmations_markup("menu_main"))
        ctx.chat_data["flow"] = {"action": "waiting_for_confirmations", "name": name, "addr": addr, "prompt_id": prompt.message_id} # type: ignore
        return

    if flow.get("action") == "add_address_direct":
        gid = flow.get("gid")
        async with async_session() as session:
            group = await session.get(AddressGroup, gid)
            if not group:
                ctx.chat_data.pop("flow", None) # type: ignore
                await remove_prompt(ctx, chat_id)
                await update_menu(ctx, chat_id, "Главное меню:", main_menu_markup(is_adm))
                return
            if len(text) < 26 or len(text) > 90:
                await ctx.bot.send_message(chat_id=chat_id, text="Неверный BTC адрес.")
                return
            await remove_prompt(ctx, chat_id)
            prompt = await ctx.bot.send_message(chat_id=chat_id, text="Выберите количество подтверждений для уведомлений:", reply_markup=confirmations_markup(f"group:{gid}"))
            ctx.chat_data["flow"] = {"action": "waiting_for_confirmations", "gid": gid, "addr": text, "prompt_id": prompt.message_id} # type: ignore
        return

    if flow.get("action") == "edit_address_select":
        gid = flow.get("gid")
        async with async_session() as session:
            stmt = select(AddressGroup).where(AddressGroup.id == gid).options(selectinload(AddressGroup.addresses))
            result = await session.execute(stmt)
            group = result.scalar_one_or_none()
            if not group:
                ctx.chat_data.pop("flow", None) # type: ignore
                await remove_prompt(ctx, chat_id)
                await update_menu(ctx, chat_id, "Главное меню:", main_menu_markup(is_adm))
                return
            addr_entry = next((a for a in group.addresses if a.address == text), None)
            if not addr_entry:
                await ctx.bot.send_message(chat_id=chat_id, text="Такой адрес не найден в группе.")
                return
            await remove_prompt(ctx, chat_id)
            prompt = await ctx.bot.send_message(chat_id=chat_id, text="Введите новый BTC адрес:", reply_markup=cancel_markup(f"group:{gid}"))
            ctx.chat_data["flow"] = {"action": "edit_address_new", "gid": gid, "aid": addr_entry.id, "prompt_id": prompt.message_id} # type: ignore
        return

    if flow.get("action") == "edit_address_new":
        gid = flow.get("gid")
        aid = flow.get("aid")
        async with async_session() as session:
            stmt = select(AddressGroup).where(AddressGroup.id == gid).options(selectinload(AddressGroup.addresses))
            result = await session.execute(stmt)
            group = result.scalar_one_or_none()
            if not group:
                ctx.chat_data.pop("flow", None) # type: ignore
                await remove_prompt(ctx, chat_id)
                await update_menu(ctx, chat_id, "Главное меню:", main_menu_markup(is_adm))
                return
            addr_entry = await session.get(Address, aid)
            if not addr_entry:
                ctx.chat_data.pop("flow", None) # type: ignore
                await remove_prompt(ctx, chat_id)
                await update_menu(ctx, chat_id, "Главное меню:", main_menu_markup(is_adm))
                return
            if len(text) < 26 or len(text) > 90:
                await ctx.bot.send_message(chat_id=chat_id, text="Неверный BTC адрес.")
                return
            addr_entry.address = text.strip()
            await session.commit()
            
            await initialize_address(addr_entry.address, chat_id)
            
            ctx.chat_data.pop("flow", None) # type: ignore
            await remove_prompt(ctx, chat_id)
            await ctx.bot.send_message(chat_id=chat_id, text="✅ Адрес заменён.")
            
            await session.refresh(group)
            lines = [f"{idx+1}. <code>{a.address}</code>" for idx, a in enumerate(group.addresses)]
            body = f"<b>{group.name}</b>\n\nАдреса:\n" + "\n".join(lines)
            await update_menu(ctx, chat_id, body, group_view_markup(group)) # type: ignore
        return

    if flow.get("action") == "admin_add_name" and is_adm:
        target_uid = flow.get("target_uid")
        name = text
        await remove_prompt(ctx, chat_id)
        prompt = await ctx.bot.send_message(chat_id=chat_id, text=f"Название: <b>{name}</b>\n\nВведите BTC адрес для этого пользователя:", reply_markup=cancel_markup(f"admin_user:{target_uid}"), parse_mode="HTML")
        ctx.chat_data["flow"] = {"action": "admin_add_val", "target_uid": target_uid, "name": name, "prompt_id": prompt.message_id} # type: ignore
        return

    if flow.get("action") == "admin_add_val" and is_adm:
        target_uid = flow.get("target_uid")
        name = flow.get("name")
        addr = text
        if len(addr) < 26 or len(addr) > 90:
            await ctx.bot.send_message(chat_id=chat_id, text="Неверный BTC адрес.")
            return
        
        await remove_prompt(ctx, chat_id)
        prompt = await ctx.bot.send_message(chat_id=chat_id, text="Выберите количество подтверждений для уведомлений:", reply_markup=confirmations_markup(f"admin_user:{target_uid}"))
        ctx.chat_data["flow"] = {"action": "waiting_for_confirmations", "target_uid": target_uid, "name": name, "addr": addr, "prompt_id": prompt.message_id} # type: ignore
        return

    if flow.get("action") == "admin_broadcast_text" and is_adm:
        selected = ctx.chat_data.get("broadcast_targets", set()) # type: ignore
        if not selected:
            ctx.chat_data.pop("flow", None) # type: ignore
            await update_menu(ctx, chat_id, "Главное меню:", main_menu_markup(is_adm))
            return
            
        msg_text = text
        sent_count = 0
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("💬 Ответить", callback_data="reply_to_admin")]])
        
        for uid in selected:
            try:
                await ctx.bot.send_message(chat_id=int(uid), text=msg_text, reply_markup=markup)
                sent_count += 1
            except Forbidden:
                logger.error(f"Бот заблокирован пользователем {uid}. Помечаем как blocked.")
                await update_user_blocked(int(uid), True)
            except Exception as e:
                logger.error(f"Не удалось отправить рассылку {uid}: {e}")
                
        ctx.chat_data.pop("broadcast_targets", None) # type: ignore
        ctx.chat_data.pop("flow", None) # type: ignore
        await remove_prompt(ctx, chat_id)
        await ctx.bot.send_message(chat_id=chat_id, text=f"✅ Рассылка завершена. Успешно отправлено: {sent_count}.")
        await update_menu(ctx, chat_id, "Главное меню:", main_menu_markup(is_adm))
        return

    if flow.get("action") == "user_reply_admin":
        user_name = user.first_name or user.username or "Пользователь"
        msg_text = f"📩 Ответ от {user_name} ({chat_id}):\n\n{text}"
        
        all_admins = await get_all_admins()
        for admin_id in all_admins:
            try:
                await ctx.bot.send_message(chat_id=int(admin_id), text=msg_text)
            except Forbidden:
                logger.error(f"Бот заблокирован администратором {admin_id}. Помечаем как blocked.")
                await update_user_blocked(int(admin_id), True)
            except Exception as e:
                logger.error(f"Не удалось переслать ответ админу {admin_id} от {chat_id}: {e}")
        
        await ctx.bot.send_message(chat_id=chat_id, text="✅ Ваше сообщение успешно отправлено администраторам.")
            
        ctx.chat_data.pop("flow", None) # type: ignore
        await remove_prompt(ctx, chat_id)
        await update_menu(ctx, chat_id, "Главное меню:", main_menu_markup(is_adm))
        return

    if flow.get("action") == "admin_add_admin_input" and is_main_admin(chat_id):
        new_admin_id = text.strip()
        if not new_admin_id.isdigit():
            await ctx.bot.send_message(chat_id=chat_id, text="❌ Ошибка: ID должен состоять только из цифр.")
            return
            
        async with async_session() as session:
            tid = int(new_admin_id)
            stmt = select(Admin).where(Admin.telegram_id == tid)
            result = await session.execute(stmt)
            if not result.scalar_one_or_none() and tid != config.ADMIN_CHAT_ID:
                session.add(Admin(telegram_id=tid))
                await session.commit()
                await ctx.bot.send_message(chat_id=chat_id, text=f"✅ Пользователь {new_admin_id} добавлен как администратор.")
            else:
                await ctx.bot.send_message(chat_id=chat_id, text=f"ℹ Пользователь {new_admin_id} уже является администратором.")
            
        ctx.chat_data.pop("flow", None) # type: ignore
        await remove_prompt(ctx, chat_id)
        all_admins_list = await get_all_admins()
        await update_menu(ctx, chat_id, "Управление администраторами:", admin_manage_admins_markup(all_admins_list, config.ADMIN_CHAT_ID))
        return

    ctx.chat_data.pop("flow", None) # type: ignore
    await remove_prompt(ctx, chat_id)
    await update_menu(ctx, chat_id, "Главное меню:", main_menu_markup(is_adm))

async def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"Ошибка при обработке update: {ctx.error}", exc_info=ctx.error)
