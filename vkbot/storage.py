"""Redis state is isolated by community and user, and survives bot restarts."""
import json
from contextlib import asynccontextmanager
from uuid import uuid4


class BusyError(Exception):
    pass


class RedisStorage:
    RELEASE = """
    if redis.call('get', KEYS[1]) == ARGV[1] then
        return redis.call('del', KEYS[1])
    end
    return 0
    """

    def __init__(self, redis, namespace: str, ttl: int = 3600, nonce_ttl: int = 300):
        self.redis = redis
        self.namespace = namespace
        self.ttl = ttl
        self.nonce_ttl = nonce_ttl

    def key(self, user_id: int, kind: str) -> str:
        return f"{self.namespace}:{user_id}:{kind}"

    async def get(self, user_id: int) -> dict:
        value = await self.redis.get(self.key(user_id, "state"))
        return json.loads(value) if value else {}

    async def set(self, user_id: int, state: dict):
        await self.redis.set(self.key(user_id, "state"), json.dumps(state), ex=self.ttl)

    async def remember_nonce(self, user_id: int, nonce: str):
        # Keep the first receipt time: repeated ref must not extend its lifetime.
        key = self.key(user_id, "nonce")
        if await self.redis.get(key) != nonce:
            await self.redis.set(key, nonce, ex=self.nonce_ttl)

    async def get_nonce(self, user_id: int):
        return await self.redis.get(self.key(user_id, "nonce"))

    async def forget_nonce(self, user_id: int):
        await self.redis.delete(self.key(user_id, "nonce"))

    @asynccontextmanager
    async def lock(self, user_id: int):
        token = uuid4().hex
        key = self.key(user_id, "lock")
        if not await self.redis.set(key, token, nx=True, ex=60):
            raise BusyError()
        try:
            yield
        finally:
            await self.redis.eval(self.RELEASE, 1, key, token)
