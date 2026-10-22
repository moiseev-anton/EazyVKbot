import asyncio
import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from redis.exceptions import ResponseError

from vkbot.callback import CallbackServer, validate_callback_config


class CallbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = NS(vk_callback_secret="test-secret", vk_confirmation_code="41fe4c87",
                         callback_path="/vk/callback")
        self.redis = NS(eval=AsyncMock(return_value=1), ping=AsyncMock(), xgroup_create=AsyncMock())
        self.bot = NS(process_event=AsyncMock())
        self.server = CallbackServer(self.bot, self.redis, 224758825, self.config)
        app = web.Application(client_max_size=1024 * 1024)
        app.router.add_post(self.config.callback_path, self.server.handle)
        app.router.add_get("/healthz", self.server.health)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    def event(self, **changes):
        return {"type": "message_new", "group_id": 224758825, "secret": "test-secret",
                "event_id": "event-123", "object": {"message": {"text": "Начать"}}, **changes}

    async def test_confirmation_returns_exact_plain_text(self):
        response = await self.client.post("/vk/callback", json=self.event(type="confirmation"))
        self.assertEqual(response.status, 200)
        self.assertEqual(response.content_type, "text/plain")
        self.assertEqual(await response.text(), "41fe4c87")
        self.redis.eval.assert_not_awaited()

    async def test_wrong_secret_or_community_rejected(self):
        for changes in ({"secret": "wrong"}, {"secret": None}, {"group_id": 1}):
            response = await self.client.post("/vk/callback", json=self.event(**changes))
            self.assertEqual(response.status, 403)
        self.redis.eval.assert_not_awaited()

    async def test_events_are_saved_before_ok_without_callback_secret(self):
        for kind in ("message_new", "message_event"):
            response = await self.client.post("/vk/callback", json=self.event(type=kind))
            self.assertEqual(await response.text(), "ok")
            args = self.redis.eval.await_args.args
            saved = json.loads(args[-1])
            self.assertNotIn("secret", saved)
            self.assertEqual(saved["type"], kind)
            self.assertTrue(args[-2].endswith(":event-123"))
        self.bot.process_event.assert_not_awaited()
        self.redis.eval.return_value = 0  # duplicate already persisted by Lua
        response = await self.client.post("/vk/callback", json=self.event())
        self.assertEqual(await response.text(), "ok")

    async def test_storage_failure_returns_503_so_vk_can_retry(self):
        self.redis.eval.side_effect = ConnectionError("Redis unavailable")
        with self.assertLogs("vkbot.callback", level="ERROR"):
            response = await self.client.post("/vk/callback", json=self.event())
        self.assertEqual(response.status, 503)

    async def test_invalid_events_and_unknown_types(self):
        for payload in ([], self.event(event_id=None), self.event(object=[])):
            response = await self.client.post("/vk/callback", json=payload)
            self.assertEqual(response.status, 400)
        response = await self.client.post("/vk/callback", data="not-json")
        self.assertEqual(response.status, 400)
        response = await self.client.post("/vk/callback", json=self.event(type="message_allow"))
        self.assertEqual(await response.text(), "ok")
        self.redis.eval.assert_not_awaited()

    async def test_health(self):
        self.assertEqual((await self.client.get("/healthz")).status, 200)
        self.redis.ping.side_effect = ConnectionError()
        self.assertEqual((await self.client.get("/healthz")).status, 503)

    async def test_prepare_preserves_existing_stream_group(self):
        self.redis.xgroup_create.side_effect = ResponseError("BUSYGROUP Consumer Group name already exists")
        await self.server.prepare()
        self.redis.xgroup_create.side_effect = ResponseError("NOAUTH")
        with self.assertRaises(ResponseError):
            await self.server.prepare()

    async def test_worker_resumes_pending_then_reads_new_and_acknowledges(self):
        event = self.event()
        self.redis.xreadgroup = AsyncMock(side_effect=[
            [[self.server.stream, [("1-0", {"event": json.dumps(event)})]]],
            [[self.server.stream, []]], asyncio.CancelledError(),
        ])
        pipeline = Mock(xack=Mock(), xdel=Mock(), execute=AsyncMock())
        context = AsyncMock()
        context.__aenter__.return_value = pipeline
        self.redis.pipeline = Mock(return_value=context)
        with self.assertRaises(asyncio.CancelledError):
            await self.server.consume()
        self.bot.process_event.assert_awaited_once_with(event)
        pipeline.xack.assert_called_once_with(self.server.stream, "bot", "1-0")
        pipeline.xdel.assert_called_once_with(self.server.stream, "1-0")
        self.assertEqual(self.redis.xreadgroup.call_args.args[2], {self.server.stream: ">"})

    async def test_cancelled_processing_leaves_event_pending(self):
        self.redis.xreadgroup = AsyncMock(return_value=[[
            self.server.stream, [("1-0", {"event": json.dumps(self.event())})]]])
        self.redis.pipeline = Mock()
        self.bot.process_event.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.server.consume()
        self.redis.pipeline.assert_not_called()

    async def test_callback_requires_config(self):
        validate_callback_config(self.config)
        self.config.vk_callback_secret = ""
        with self.assertRaises(ValueError):
            validate_callback_config(self.config)
