import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from services.monitoring import process_tx, initialize_address
from core.state import load_state

@pytest.mark.asyncio
async def test_process_tx_new_unconfirmed():
    app = MagicMock()
    app.bot.send_message = AsyncMock()
    state = {"notified_confirmed": {}, "unconfirmed": {}}
    tx = {
        "txid": "tx1",
        "status": {"confirmed": False},
        "vout": [{"scriptpubkey_address": "addr1", "value": 100000000}]
    }
    addr = "addr1"
    subs = [{"uid": 123, "group_name": "Group1", "confirmations": 2}]
    btc_price = 50000
    tip_height = 100
    
    changed = await process_tx(app, state, tx, addr, subs, btc_price, tip_height)
    
    assert changed is True
    assert "tx1" in state["notified_confirmed"]
    assert state["notified_confirmed"]["tx1"]["123"] == ["0"]
    assert "tx1" in state["unconfirmed"]
    app.bot.send_message.assert_called_once()
    assert "unconfirmed" in app.bot.send_message.call_args[1]["text"]

@pytest.mark.asyncio
async def test_process_tx_first_confirmation():
    app = MagicMock()
    app.bot.send_message = AsyncMock()
    state = {"notified_confirmed": {"tx1": {"123": ["0"]}}, "unconfirmed": {"tx1": {}}}
    tx = {
        "txid": "tx1",
        "status": {"confirmed": True, "block_height": 100},
        "vout": [{"scriptpubkey_address": "addr1", "value": 100000000}]
    }
    addr = "addr1"
    subs = [{"uid": 123, "group_name": "Group1", "confirmations": 2}]
    btc_price = 50000
    tip_height = 100 # 1 confirmation
    
    changed = await process_tx(app, state, tx, addr, subs, btc_price, tip_height)
    
    assert changed is True
    assert "1" in state["notified_confirmed"]["tx1"]["123"]
    assert "tx1" not in state["unconfirmed"]
    assert "первое подтверждение" in app.bot.send_message.call_args[1]["text"]

@pytest.mark.asyncio
async def test_process_tx_target_confirmation():
    app = MagicMock()
    app.bot.send_message = AsyncMock()
    state = {"notified_confirmed": {"tx1": {"123": ["0", "1"]}}, "unconfirmed": {}}
    tx = {
        "txid": "tx1",
        "status": {"confirmed": True, "block_height": 99},
        "vout": [{"scriptpubkey_address": "addr1", "value": 100000000}]
    }
    addr = "addr1"
    subs = [{"uid": 123, "group_name": "Group1", "confirmations": 2}]
    btc_price = 50000
    tip_height = 100 # 2 confirmations
    
    changed = await process_tx(app, state, tx, addr, subs, btc_price, tip_height)
    
    assert changed is True
    assert "target" in state["notified_confirmed"]["tx1"]["123"]
    assert "подтверждение" in app.bot.send_message.call_args[1]["text"]

@pytest.mark.asyncio
async def test_process_tx_skip_milestones():
    app = MagicMock()
    app.bot.send_message = AsyncMock()
    state = {"notified_confirmed": {}, "unconfirmed": {}}
    tx = {
        "txid": "tx1",
        "status": {"confirmed": True, "block_height": 90},
        "vout": [{"scriptpubkey_address": "addr1", "value": 100000000}]
    }
    addr = "addr1"
    subs = [{"uid": 123, "group_name": "Group1", "confirmations": 2}]
    btc_price = 50000
    tip_height = 100 # 11 confirmations
    
    changed = await process_tx(app, state, tx, addr, subs, btc_price, tip_height)
    
    assert changed is True
    assert state["notified_confirmed"]["tx1"]["123"] == ["target"]
    assert "✅" in app.bot.send_message.call_args[1]["text"]

@pytest.mark.asyncio
async def test_process_tx_duplicate_prevention():
    app = MagicMock()
    app.bot.send_message = AsyncMock()
    state = {"notified_confirmed": {"tx1": {"123": ["target"]}}, "unconfirmed": {}}
    tx = {
        "txid": "tx1",
        "status": {"confirmed": True, "block_height": 90},
        "vout": [{"scriptpubkey_address": "addr1", "value": 100000000}]
    }
    addr = "addr1"
    subs = [{"uid": 123, "group_name": "Group1", "confirmations": 2}]
    btc_price = 50000
    tip_height = 100
    
    changed = await process_tx(app, state, tx, addr, subs, btc_price, tip_height)
    
    assert changed is False
    app.bot.send_message.assert_not_called()

@pytest.mark.asyncio
async def test_initialize_address():
    state = {"notified_confirmed": {}}
    addr = "addr1"
    uid = 123
    
    mock_txs = [{"txid": "tx1"}, {"txid": "tx2"}]
    
    with patch("httpx.AsyncClient.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200, json=lambda: mock_txs)
        
        success = await initialize_address(state, addr, uid)
        
        assert success is True
        assert state["notified_confirmed"]["tx1"]["123"] == ["target"]
        assert state["notified_confirmed"]["tx2"]["123"] == ["target"]

def test_state_migration(tmp_path):
    state_file = tmp_path / "state.json"
    legacy_data = {
        "notified_confirmed": {
            "tx1": True,
            "tx2": [123, 456]
        },
        "users": {
            "123": {"groups": [{"name": "G1", "addresses": [{"addr": "A1"}]}]}
        }
    }
    import json
    state_file.write_text(json.dumps(legacy_data))
    
    with patch("core.state.STATE_FILE", state_file), \
         patch("core.config.config.ADMIN_CHAT_ID", 123):
        from core.state import load_state
        state = load_state()
        
        # tx1 remains True (handled in process_tx)
        assert state["notified_confirmed"]["tx1"] is True
        # tx2 migrated to dict
        assert state["notified_confirmed"]["tx2"]["123"] == ["target"]
        assert state["notified_confirmed"]["tx2"]["456"] == ["target"]
        # confirmations added
        assert state["users"]["123"]["groups"][0]["addresses"][0]["confirmations"] == 1
