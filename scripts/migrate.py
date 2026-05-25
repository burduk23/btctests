import asyncio
import json
import logging
import os
from pathlib import Path
from sqlalchemy import select
from core.config import STATE_FILE, config, BASE_DIR
from core.database import init_db, async_session
from core.models import User, Admin, AddressGroup, Address, Transaction

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("migration")

async def migrate():
    if not STATE_FILE.exists():
        logger.info("state.json не найден, миграция не требуется.")
        return

    logger.info("Начало миграции из state.json в SQLite...")
    
    try:
        try:
            with open(STATE_FILE, "r", encoding="utf-8-sig") as f:
                state = json.load(f)
        except UnicodeDecodeError:
            logger.info("UnicodeDecodeError при чтении в utf-8-sig, пробуем cp1251...")
            with open(STATE_FILE, "r", encoding="cp1251") as f:
                state = json.load(f)
    except Exception as e:
        logger.error(f"Ошибка при чтении state.json: {e}")
        return

    await init_db()

    async with async_session() as session:
        # 1. Миграция админов
        admins_list = state.get("admins", [])
        main_admin_id = config.ADMIN_CHAT_ID
        if main_admin_id and str(main_admin_id) not in admins_list:
            admins_list.append(str(main_admin_id))
        
        for admin_tid in admins_list:
            try:
                tid = int(admin_tid)
                stmt = select(Admin).where(Admin.telegram_id == tid)
                result = await session.execute(stmt)
                if not result.scalar_one_or_none():
                    session.add(Admin(telegram_id=tid))
            except ValueError:
                continue

        # 2. Миграция пользователей и их адресов
        users_data = state.get("users", {})
        for tid_str, udata in users_data.items():
            try:
                tid = int(tid_str)
            except ValueError:
                continue
            
            stmt = select(User).where(User.telegram_id == tid)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            
            if not user:
                user = User(
                    telegram_id=tid,
                    username=udata.get("username"),
                    first_name=udata.get("first_name")
                )
                session.add(user)
                await session.flush() # Чтобы получить user.id
            
            for gdata in udata.get("groups", []):
                group = AddressGroup(
                    user_id=user.id,
                    name=gdata.get("name", "Default")
                )
                session.add(group)
                await session.flush()
                
                for adata in gdata.get("addresses", []):
                    address = Address(
                        group_id=group.id,
                        address=adata.get("addr"),
                        confirmations_target=adata.get("confirmations", 1),
                        notify_disabled=adata.get("notify_disabled", False)
                    )
                    session.add(address)

        # 3. Миграция транзакций (notified_confirmed)
        notified_confirmed = state.get("notified_confirmed", {})
        for txid, notified_data in notified_confirmed.items():
            # notified_data может быть True (legacy), list (legacy) или dict
            if notified_data is True:
                # Мы не знаем кому уведомляли, оставим пустым или пропустим
                notified_confs = {}
            elif isinstance(notified_data, list):
                notified_confs = {str(uid): ["target"] for uid in notified_data}
            elif isinstance(notified_data, dict):
                notified_confs = notified_data
            else:
                notified_confs = {}

            stmt = select(Transaction).where(Transaction.txid == txid)
            result = await session.execute(stmt)
            if not result.scalar_one_or_none():
                session.add(Transaction(txid=txid, notified_confs=notified_confs))

        await session.commit()
        logger.info("Миграция успешно завершена.")

    # Переименовываем state.json в state.json.bak
    bak_file = STATE_FILE.with_suffix(".json.bak")
    try:
        STATE_FILE.replace(bak_file)
        logger.info(f"state.json переименован в {bak_file}")
    except Exception as e:
        logger.error(f"Не удалось переименовать state.json: {e}")

if __name__ == "__main__":
    asyncio.run(migrate())
