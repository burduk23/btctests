import asyncio
from core.database import engine, async_session
from core.models import Transaction
from sqlalchemy import select

async def test_mutation():
    async with async_session() as session:
        # Create a test transaction
        txid = "test_tx_123"
        tx = Transaction(txid=txid, notified_confs={"u1": ["0"]})
        session.add(tx)
        await session.commit()
        
    async with async_session() as session:
        # Load and update
        stmt = select(Transaction).where(Transaction.txid == txid)
        res = await session.execute(stmt)
        tx = res.scalar_one()
        
        print(f"Initial: {tx.notified_confs}")
        
        confs = dict(tx.notified_confs)
        confs["u1"].append("1")
        tx.notified_confs = confs
        
        await session.commit()
        print("Updated and committed.")

    async with async_session() as session:
        # Check if saved
        stmt = select(Transaction).where(Transaction.txid == txid)
        res = await session.execute(stmt)
        tx = res.scalar_one()
        print(f"After commit: {tx.notified_confs}")
        
        if "1" in tx.notified_confs["u1"]:
            print("SUCCESS: Mutation saved.")
        else:
            print("FAILURE: Mutation NOT saved.")

if __name__ == "__main__":
    asyncio.run(test_mutation())
