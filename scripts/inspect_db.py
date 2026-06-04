import asyncio
import json
from core.database import engine
from sqlalchemy import text

async def main():
    async with engine.connect() as conn:
        res = await conn.execute(text("SELECT count(*) FROM transactions"))
        print(f"Total transactions: {res.scalar()}")
        
        res = await conn.execute(text("SELECT * FROM transactions ORDER BY id DESC LIMIT 20"))
        print("Sample rows:")
        for row in res.fetchall():
            print(row)

if __name__ == "__main__":
    asyncio.run(main())
