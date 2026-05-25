import asyncio
import unittest
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

# Add current directory to sys.path to ensure imports work
sys.path.append(os.getcwd())

# Mocking the database before importing anything else
test_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
test_async_session = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)

# Patch core.database BEFORE importing other modules
with patch("core.database.engine", test_engine), \
     patch("core.database.async_session", test_async_session):
    
    from core.database import Base
    from core.models import User, AddressGroup, Address, Transaction
    from core.state import get_or_create_user, add_address_group, add_address, get_notified_confs, update_user_blocked
    from services.monitoring import process_tx
    from bot.handlers import cmd_start
    from telegram.error import Forbidden

    class TestNotifications(unittest.IsolatedAsyncioTestCase):
        async def asyncSetUp(self):
            # Initialize in-memory database
            async with test_engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            
            self.app = MagicMock()
            self.app.bot.send_message = AsyncMock()
            
            # Create test user
            self.user_id = 12345
            self.user = await get_or_create_user(self.user_id, "testuser", "Test")
            self.group = await add_address_group(self.user_id, "Test Group")
            self.addr_str = "1TestAddress"
            self.address = await add_address(self.group.id, self.addr_str, confirmations=1)
            
            self.btc_price = 60000
            self.tip_height = 1000

        async def asyncTearDown(self):
            async with test_engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)

        async def test_successful_notification(self):
            tx = {
                "txid": "tx1",
                "status": {"confirmed": True, "block_height": 1000},
                "vout": [{"scriptpubkey_address": self.addr_str, "value": 100000000}]
            }
            
            subs = [{
                "uid": str(self.user_id),
                "group_name": "Test Group",
                "disabled": False,
                "bot_blocked": False,
                "confirmations": 1
            }]
            
            result = await process_tx(self.app, tx, self.addr_str, subs, self.btc_price, self.tip_height)
            
            self.assertTrue(result)
            self.app.bot.send_message.assert_called_once()
            
            notified = await get_notified_confs("tx1")
            self.assertIn(f"{self.user_id}:{self.addr_str}", notified)
            self.assertIn("target", notified[f"{self.user_id}:{self.addr_str}"])

        async def test_network_error_retry(self):
            tx = {
                "txid": "tx2",
                "status": {"confirmed": True, "block_height": 1000},
                "vout": [{"scriptpubkey_address": self.addr_str, "value": 100000000}]
            }
            subs = [{
                "uid": str(self.user_id),
                "group_name": "Test Group",
                "disabled": False,
                "bot_blocked": False,
                "confirmations": 1
            }]
            
            # Simulate network error
            self.app.bot.send_message.side_effect = Exception("Network error")
            
            result = await process_tx(self.app, tx, self.addr_str, subs, self.btc_price, self.tip_height)
            
            self.assertFalse(result)
            notified = await get_notified_confs("tx2")
            self.assertEqual(notified, {})
            
            # Simulate success on retry
            self.app.bot.send_message.side_effect = None
            self.app.bot.send_message.return_value = MagicMock()
            
            result = await process_tx(self.app, tx, self.addr_str, subs, self.btc_price, self.tip_height)
            
            self.assertTrue(result)
            notified = await get_notified_confs("tx2")
            self.assertIn("target", notified[f"{self.user_id}:{self.addr_str}"])

        async def test_bot_blocked_forbidden(self):
            tx = {
                "txid": "tx3",
                "status": {"confirmed": True, "block_height": 1000},
                "vout": [{"scriptpubkey_address": self.addr_str, "value": 100000000}]
            }
            subs = [{
                "uid": str(self.user_id),
                "group_name": "Test Group",
                "disabled": False,
                "bot_blocked": False,
                "confirmations": 1
            }]
            
            # Simulate Forbidden
            self.app.bot.send_message.side_effect = Forbidden("Bot blocked")
            
            result = await process_tx(self.app, tx, self.addr_str, subs, self.btc_price, self.tip_height)
            
            self.assertFalse(result)
            
            # Check if user is marked as blocked
            async with test_async_session() as session:
                from sqlalchemy import select
                stmt = select(User).where(User.telegram_id == self.user_id)
                res = await session.execute(stmt)
                user = res.scalar_one()
                self.assertTrue(user.bot_blocked)

        async def test_unblock_on_interaction(self):
            # Set user as blocked
            await update_user_blocked(self.user_id, True)
            
            # Verify blocked
            async with test_async_session() as session:
                from sqlalchemy import select
                stmt = select(User).where(User.telegram_id == self.user_id)
                res = await session.execute(stmt)
                user = res.scalar_one()
                self.assertTrue(user.bot_blocked)
                
            # Mock Update and Context
            update = MagicMock()
            update.effective_chat.id = self.user_id
            update.effective_user.id = self.user_id
            update.effective_user.username = "testuser"
            update.effective_user.first_name = "Test"
            
            context = MagicMock()
            context.bot.send_message = AsyncMock()
            context.chat_data = {}
            
            # Call cmd_start
            await cmd_start(update, context)
            
            # Verify unblocked
            async with test_async_session() as session:
                from sqlalchemy import select
                stmt = select(User).where(User.telegram_id == self.user_id)
                res = await session.execute(stmt)
                user = res.scalar_one()
                self.assertFalse(user.bot_blocked)

if __name__ == "__main__":
    unittest.main()
