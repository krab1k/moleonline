"""Alembic's environment: migrates the connection storage.upgrade gives it, or the empty database of DATABASE_URL for
the alembic command making the revision anew (README.md#database)"""

import asyncio
import os

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from moleonline.storage import metadata


def migrate(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=metadata)
    with context.begin_transaction():
        context.run_migrations()


async def from_url() -> None:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    async with engine.begin() as connection:
        await connection.run_sync(migrate)
    await engine.dispose()


if (connection := context.config.attributes.get("connection")) is not None:
    migrate(connection)
else:
    asyncio.run(from_url())
