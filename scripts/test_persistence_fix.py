import asyncio
import logging
from core.database import init_db, async_session
from core.state import update_notified_confs, get_notified_confs
from core.models import Transaction
from sqlalchemy import select, delete

logging.basicConfig(level=logging.INFO)

async def test_persistence():
    await init_db()
    txid = "test_tx_persistence_fix"
    uid = "user123"
    milestone = "0"
    
    # Clean up
    async with async_session() as session:
        await session.execute(delete(Transaction).where(Transaction.txid == txid))
        await session.commit()
    
    print(f"Testing persistence for {txid}...")
    
    # 1. First update (create)
    await update_notified_confs(txid, uid, milestone)
    
    # 2. Verify
    notified = await get_notified_confs(txid)
    print(f"After first update: {notified}")
    assert notified.get(uid) == [milestone]
    
    # 3. Second update (mutate)
    new_milestone = "1"
    await update_notified_confs(txid, uid, new_milestone)
    
    # 4. Verify again
    notified = await get_notified_confs(txid)
    print(f"After second update: {notified}")
    assert new_milestone in notified.get(uid)
    assert milestone in notified.get(uid)
    
    # 5. Third update (new user)
    uid2 = "user456"
    await update_notified_confs(txid, uid2, "target")
    
    # 6. Final verify
    notified = await get_notified_confs(txid)
    print(f"After third update: {notified}")
    assert notified.get(uid2) == ["target"]
    assert len(notified.get(uid)) == 2
    
    print("Persistence test PASSED!")

if __name__ == "__main__":
    asyncio.run(test_persistence())
