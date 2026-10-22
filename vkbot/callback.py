"""Authenticated VK webhook with a durable Redis inbox (one bot process)."""
import asyncio
import json
import logging
import secrets

from aiohttp import web
from redis.exceptions import ResponseError

logger = logging.getLogger(__name__)

# Enqueue and mark delivery together: a retry cannot enqueue the same event twice.
ENQUEUE = """
if redis.call('EXISTS', KEYS[2]) == 1 then return 0 end
redis.call('XADD', KEYS[1], '*', 'event', ARGV[1])
redis.call('SET', KEYS[2], '1', 'EX', 86400)
return 1
"""


class CallbackServer:
    def __init__(self, bot, redis, group_id, config):
        self.bot, self.redis, self.group_id, self.config = bot, redis, group_id, config
        self.stream = f"vkbot:{group_id}:callback:inbox"
        self.consumer_group = "bot"
        self.consumer = "single-worker"

    async def handle(self, request):
        try:
            event = await request.json()
        except (ValueError, UnicodeDecodeError):
            raise web.HTTPBadRequest(text="Invalid JSON") from None
        if not isinstance(event, dict):
            raise web.HTTPBadRequest(text="Expected object")
        secret = event.get("secret")
        if (not isinstance(secret, str) or not secrets.compare_digest(
                secret.encode(), self.config.vk_callback_secret.encode())
                or event.get("group_id") != self.group_id):
            raise web.HTTPForbidden(text="Forbidden")
        if event.get("type") == "confirmation":
            return web.Response(text=self.config.vk_confirmation_code)
        if event.get("type") not in ("message_new", "message_event"):
            return web.Response(text="ok")
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id or len(event_id) > 256 or not isinstance(event.get("object"), dict):
            raise web.HTTPBadRequest(text="Invalid event")
        event.pop("secret", None)
        try:
            async with asyncio.timeout(3):
                await self.redis.eval(ENQUEUE, 2, self.stream,
                                      f"vkbot:{self.group_id}:callback:seen:{event_id}",
                                      json.dumps(event, ensure_ascii=False))
        except Exception:
            logger.exception("Could not enqueue VK callback")
            raise web.HTTPServiceUnavailable(text="Retry later") from None
        return web.Response(text="ok")

    async def health(self, request):
        try:
            async with asyncio.timeout(2):
                await self.redis.ping()
        except Exception:
            raise web.HTTPServiceUnavailable(text="Redis unavailable") from None
        return web.Response(text="ok")

    async def prepare(self):
        try:
            await self.redis.xgroup_create(self.stream, self.consumer_group, id="0", mkstream=True)
        except ResponseError as exc:
            if not str(exc).startswith("BUSYGROUP"):
                raise

    async def consume(self):
        # Stable consumer name resumes unfinished events after process restart.
        pending = True
        while True:
            batches = await self.redis.xreadgroup(
                self.consumer_group, self.consumer,
                {self.stream: "0" if pending else ">"}, count=1,
                **({} if pending else {"block": 1000}),
            )
            entries = [entry for _, messages in batches for entry in messages]
            if not entries:
                pending = False
                continue
            for entry_id, fields in entries:
                await self.bot.process_event(json.loads(fields["event"]))
                async with self.redis.pipeline(transaction=True) as pipeline:
                    pipeline.xack(self.stream, self.consumer_group, entry_id)
                    pipeline.xdel(self.stream, entry_id)
                    await pipeline.execute()

    async def run(self):
        await self.prepare()
        app = web.Application(client_max_size=1024 * 1024)
        app.router.add_post(self.config.callback_path, self.handle)
        app.router.add_get("/healthz", self.health)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        try:
            await web.TCPSite(runner, self.config.callback_host, self.config.callback_port).start()
            logger.info("VK Callback HTTP server listening on %s:%s%s",
                        self.config.callback_host, self.config.callback_port, self.config.callback_path)
            await self.consume()
        finally:
            await runner.cleanup()


def validate_callback_config(config):
    if not config.vk_callback_secret or not config.vk_confirmation_code:
        raise ValueError("Callback mode requires VK_CALLBACK_SECRET and VK_CONFIRMATION_CODE")
    if not config.callback_path.startswith("/") or "?" in config.callback_path or config.callback_path == "/healthz":
        raise ValueError("CALLBACK_PATH must be an absolute URL path distinct from /healthz")
