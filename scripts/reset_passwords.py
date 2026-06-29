import asyncio
import sys

import bcrypt

sys.path.insert(0, '/app')

from sqlalchemy import select

from packages.auth.models import LocalUserModel
from packages.common.config import load_settings
from packages.data.storage.session import create_async_db_engine, create_session_factory

USERS = {
    'admin': '123456',
    'editor1': '123456',
    'editor2': '123456',
    'viewer1': '123456',
    'viewer2': '123456',
}

async def main():
    s = load_settings()
    engine = create_async_db_engine(s.database_url)
    factory = create_session_factory(engine)
    async with factory() as session:
        for username, password in USERS.items():
            result = await session.execute(
                select(LocalUserModel).where(LocalUserModel.username == username)
            )
            user = result.scalar_one_or_none()
            if user:
                user.password_hash = bcrypt.hashpw(
                    password.encode(), bcrypt.gensalt()
                ).decode()
                print(f'OK {username:10s} -> {password}', flush=True)
            else:
                print(f'MISS {username}', flush=True)
        await session.commit()
        print('DONE', flush=True)
    await engine.dispose()

asyncio.run(main())
