import logging
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase


logger = logging.getLogger(__name__)


class MongoManager:
    def __init__(self) -> None:
        self._client: Optional[AsyncIOMotorClient] = None
        self._database: Optional[AsyncIOMotorDatabase] = None

    async def connect(self, uri: str, db_name: str) -> None:
        if self._client is not None:
            return

        self._client = AsyncIOMotorClient(
            uri,
            uuidRepresentation="standard",
            serverSelectionTimeoutMS=5000,
        )
        await self._client.admin.command("ping")
        self._database = self._client[db_name]
        logger.info("MongoDB connected", extra={"db_name": db_name})

    async def close(self) -> None:
        if self._client is None:
            return

        self._client.close()
        self._client = None
        self._database = None
        logger.info("MongoDB connection closed")

    @property
    def database(self) -> AsyncIOMotorDatabase:
        if self._database is None:
            raise RuntimeError("MongoDB is not connected")
        return self._database


mongo_manager = MongoManager()


async def connect_to_mongo(uri: str, db_name: str) -> None:
    await mongo_manager.connect(uri=uri, db_name=db_name)


async def close_mongo() -> None:
    await mongo_manager.close()


def get_database() -> AsyncIOMotorDatabase:
    return mongo_manager.database
