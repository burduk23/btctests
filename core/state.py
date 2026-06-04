import logging
from typing import List, Optional, Dict, Any
from sqlalchemy import select, delete
from sqlalchemy.orm import selectinload, flag_modified
from .database import async_session
from .models import User, Admin, AddressGroup, Address, Transaction
from .config import config

logger = logging.getLogger("btc_notify")

async def get_user(telegram_id: int) -> Optional[User]:
    async with async_session() as session:
        stmt = select(User).where(User.telegram_id == telegram_id).options(
            selectinload(User.groups).selectinload(AddressGroup.addresses)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

async def get_or_create_user(telegram_id: int, username: str = None, first_name: str = None) -> User:
    async with async_session() as session:
        stmt = select(User).where(User.telegram_id == telegram_id).options(
            selectinload(User.groups).selectinload(AddressGroup.addresses)
        )
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            user = User(telegram_id=telegram_id, username=username, first_name=first_name)
            session.add(user)
            await session.commit()
            await session.refresh(user)
        else:
            updated = False
            if username is not None and username != user.username:
                user.username = username
                updated = True
            if first_name is not None and first_name != user.first_name:
                user.first_name = first_name
                updated = True
            
            if updated:
                await session.commit()
                await session.refresh(user)
            
        return user

async def get_all_admins() -> List[int]:
    async with async_session() as session:
        stmt = select(Admin.telegram_id)
        result = await session.execute(stmt)
        admins = list(result.scalars().all())
        
        main_admin = config.ADMIN_CHAT_ID
        if main_admin and main_admin not in admins:
            admins.insert(0, main_admin)
        return admins

async def is_admin(telegram_id: int) -> bool:
    admins = await get_all_admins()
    return telegram_id in admins

async def delete_admin(telegram_id: int):
    async with async_session() as session:
        stmt = select(Admin).where(Admin.telegram_id == telegram_id)
        result = await session.execute(stmt)
        admin = result.scalar_one_or_none()
        if admin:
            await session.delete(admin)
            await session.commit()

def is_main_admin(telegram_id: int) -> bool:
    return telegram_id == config.ADMIN_CHAT_ID

async def update_user_blocked(telegram_id: int, status: bool):
    async with async_session() as session:
        stmt = select(User).where(User.telegram_id == telegram_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        if user:
            user.bot_blocked = status
            await session.commit()

async def get_address_to_subs() -> Dict[str, List[Dict[str, Any]]]:
    async with async_session() as session:
        stmt = select(Address).options(selectinload(Address.group).selectinload(AddressGroup.user))
        result = await session.execute(stmt)
        addresses = result.scalars().all()
        
        addr_to_subs = {}
        for addr_obj in addresses:
            if not addr_obj.group or not addr_obj.group.user:
                continue
            
            addr = addr_obj.address
            if addr not in addr_to_subs:
                addr_to_subs[addr] = []
            
            addr_to_subs[addr].append({
                "uid": str(addr_obj.group.user.telegram_id),
                "group_name": addr_obj.group.name,
                "disabled": addr_obj.notify_disabled,
                "bot_blocked": addr_obj.group.user.bot_blocked,
                "confirmations": addr_obj.confirmations_target
            })
        return addr_to_subs

async def get_notified_confs(txid: str) -> Dict[str, List[str]]:
    async with async_session() as session:
        stmt = select(Transaction).where(Transaction.txid == txid)
        result = await session.execute(stmt)
        tx = result.scalar_one_or_none()
        if tx:
            return tx.notified_confs
        return {}

async def update_notified_confs(txid: str, uid: str, milestone: str):
    async with async_session() as session:
        stmt = select(Transaction).where(Transaction.txid == txid)
        result = await session.execute(stmt)
        tx = result.scalar_one_or_none()
        
        if not tx:
            tx = Transaction(txid=txid, notified_confs={uid: [milestone]})
            session.add(tx)
        else:
            if uid not in tx.notified_confs:
                tx.notified_confs[uid] = []
            if milestone not in tx.notified_confs[uid]:
                tx.notified_confs[uid].append(milestone)
                flag_modified(tx, "notified_confs")
            
        await session.commit()

async def add_address_group(telegram_id: int, name: str) -> AddressGroup:
    user = await get_or_create_user(telegram_id)
    async with async_session() as session:
        group = AddressGroup(user_id=user.id, name=name)
        session.add(group)
        await session.commit()
        await session.refresh(group)
        return group

async def delete_address_group(group_id: int):
    async with async_session() as session:
        group = await session.get(AddressGroup, group_id)
        if group:
            await session.delete(group)
            await session.commit()

async def add_address(group_id: int, address: str, confirmations: int = 1) -> Address:
    async with async_session() as session:
        addr = Address(group_id=group_id, address=address, confirmations_target=confirmations)
        session.add(addr)
        await session.commit()
        await session.refresh(addr)
        return addr

async def delete_address(address_id: int):
    async with async_session() as session:
        addr = await session.get(Address, address_id)
        if addr:
            await session.delete(addr)
            await session.commit()
