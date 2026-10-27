import asyncio
import json
import logging
import os
import unittest
from uuid import UUID
from datetime import date
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

# No real tokens, backend or .env values are used by tests.
os.environ.update(BOT_TOKEN="test-token", API_BASE_URL="https://backend.invalid/api/v1/",
                  HMAC_SECRET="test-secret",
                  BASE_SCRAPING_URL="https://site.invalid", BOT_MODE="polling")

from vkbottle import Bot
from vkbottle_types.objects import UsersUserFull
from vkbot.app import main, register_handlers
from vkbot.context import request_context, set_hmac
from vkbot.controller import Controller, payload_dict
from vkbot.dto import AuthDTO, FacultyDTO, GroupDTO, TeacherDTO, LessonDTO, DateSpanDTO
from vkbot.dto.notification_preference_dto import NotificationPreferenceDTO
from vkbot.storage import RedisStorage, BusyError
from vkbot.ui import date_span, schedule_pages, schedule_format_data, keyboard, persistent_keyboard, paginate
from vkbot.api_client import AsyncClientSession
from vkbot.api_client.client_patch import patch_jsonapi_client
from vkbot.api_client import models_as_jsonschema
from vkbot.repositories import JsonApiAccountRepository


class StartupTests(unittest.TestCase):
    def test_main_logs_traceback_and_exception_message(self):
        def fail(coroutine):
            coroutine.close()
            raise ValueError("Only BOT_MODE=polling is supported")

        with patch("vkbot.app.vk_logger", logging.Logger("test.vkbottle")), \
             patch("vkbot.app.run", new_callable=AsyncMock), \
             patch("vkbot.app.asyncio.run", side_effect=fail), \
             patch("vkbot.app.logging.basicConfig"), \
             self.assertLogs("vkbot.app", level="ERROR") as logs:
            with self.assertRaises(SystemExit) as exit_info:
                main()
        self.assertEqual(exit_info.exception.code, 1)
        text = "\n".join(logs.output)
        self.assertIn("Traceback (most recent call last)", text)
        self.assertIn("ValueError: Only BOT_MODE=polling is supported", text)
        self.assertIn("in fail", text)

    def check_main(self, sdk_logger):
        # Exercise the actual entry point without opening sockets or starting polling.
        with patch("vkbot.app.vk_logger", sdk_logger), \
             patch("vkbot.app.run", new_callable=AsyncMock) as run, \
             patch("vkbot.app.asyncio.run", side_effect=lambda coroutine: coroutine.close()) as runner, \
             patch("vkbot.app.logging.basicConfig"):
            main()
            run.assert_called_once_with()
            runner.assert_called_once()

    def test_main_with_standard_logging_adapter(self):
        logger = logging.Logger("test.vkbottle")
        self.check_main(logging.LoggerAdapter(logger, {}))
        self.assertTrue(logger.disabled)

    def test_main_with_standard_logger(self):
        logger = logging.Logger("test.vkbottle")
        self.check_main(logger)
        self.assertTrue(logger.disabled)

    def test_main_with_optional_loguru(self):
        logger = Mock(spec=["disable"])
        self.check_main(logger)
        logger.disable.assert_called_once_with("vkbottle")


class MemoryRedis:
    """Deterministic Redis stand-in; clock advances explicitly, no real sleeps."""
    def __init__(self):
        self.values = {}
        self.now = 0

    async def get(self, key):
        item = self.values.get(key)
        if item and item[1] > self.now:
            return item[0]
        return None

    async def set(self, key, value, ex, nx=False):
        if nx and await self.get(key) is not None:
            return False
        self.values[key] = (value, self.now + ex)
        return True

    async def delete(self, key):
        self.values.pop(key, None)

    async def eval(self, script, count, key, token):
        if await self.get(key) == token:
            await self.delete(key)
            return 1
        return 0


class BotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.context_token = request_context.set({"user_id": "42", "hmac": False})
        self.redis = MemoryRedis()
        self.storage = RedisStorage(self.redis, "vkbot:1", ttl=600, nonce_ttl=10)
        self.api = NS(messages=NS(send=AsyncMock(return_value=1), edit=AsyncMock(return_value=True),
                                 send_message_event_answer=AsyncMock()),
                      users=NS(get=AsyncMock(return_value=[UsersUserFull(id=42, first_name="Иван", last_name="")])) )
        self.faculty = FacultyDTO(id=1, title="Факультет", short_title="ФАК")
        self.group = GroupDTO(id=1, title="А-101", grade=1, faculty_id=1, faculty=self.faculty)
        self.teacher = TeacherDTO(id=2, full_name="Иванов Иван Иванович", short_name="Иванов И. И.")
        self.user = NS(name="Иван", subscriptions=[])
        self.preferences = {kind: NotificationPreferenceDTO(
            id=str(index), social_account_id="45", notification_type=kind, channel="vk",
            enabled=True, updated_at="2026-10-27T00:00:00Z")
            for index, kind in enumerate(("lesson_upcoming", "schedule_updated"), 123)}
        self.services = NS(
            auth_user=AsyncMock(return_value=NS(nonce_status="authenticated", user=self.user)),
            get_user=AsyncMock(return_value=self.user), refresh=AsyncMock(),
            groups={1: self.group}, teachers={2: self.teacher},
            target=lambda kind, i: (self.group if kind == "group" else self.teacher),
            subscriptions=NS(get_user_subscriptions=AsyncMock(return_value=[]),
                             get_subscription_by_target=AsyncMock(return_value=None),
                             create=AsyncMock(), delete=AsyncMock()),
            users=NS(get_user=AsyncMock(return_value=self.user)),
            notifications=NS(get_preferences=AsyncMock(return_value=self.preferences), set_enabled=AsyncMock()),
            lessons=NS(get_lessons=AsyncMock(return_value=[])),
        )
        self.settings = NS(base_link="https://site.invalid", schedule_timezone="Europe/Moscow", show_entity_links=True)
        self.controller = Controller(self.api, self.services, self.storage, self.settings)

    async def asyncTearDown(self):
        request_context.reset(self.context_token)

    def message(self, **kwargs):
        return NS(**{"from_id": 42, "peer_id": 42, "text": "Начать", "ref": None, "payload": None, **kwargs})

    async def click(self, command, **kwargs):
        state = await self.storage.get(42)
        await self.controller.handle_callback({"object": {
            "user_id": 42, "peer_id": 42, "event_id": "event", "conversation_message_id": 10,
            "payload": {"command": command, "revision": state.get("revision"), **kwargs},
        }})

    def sent_text(self):
        return "\n".join(c.kwargs["message"] for c in self.api.messages.send.await_args_list)

    async def test_link_requires_confirmation_and_overrides_other_command(self):
        await self.controller.handle_message(self.message(ref="nonce", payload='{"command":"mine"}'))
        self.services.auth_user.assert_not_awaited()
        self.assertEqual((await self.storage.get(42))["screen"]["name"], "auth")
        await self.click("auth_confirm")
        self.assertEqual(self.services.auth_user.await_args.args[0].nonce, "nonce")
        self.assertIsNone(await self.storage.get_nonce(42))
        self.assertIn("Вход подтверждён", self.sent_text())
        self.assertNotIn("nonce", self.sent_text())

    async def test_start_without_nonce_does_not_claim_site_login(self):
        await self.controller.handle_message(self.message(payload='{"command":"start"}'))
        self.assertIsNone(self.services.auth_user.await_args.args[0].nonce)
        self.assertNotIn("Вход подтверждён", self.sent_text())
        self.assertNotIn("Эта кнопка", self.sent_text())
        self.api.messages.send.assert_awaited_once()
        self.assertEqual((await self.storage.get(42))["screen"]["name"], "main")

    async def test_pending_nonce_survives_controller_restart(self):
        await self.controller.handle_message(self.message(text="Привет", ref="pending"))
        replacement = Controller(self.api, self.services, RedisStorage(self.redis, "vkbot:1"), self.settings)
        await replacement.handle_message(self.message(payload='{"command":"auth_start"}'))
        self.assertEqual(self.services.auth_user.await_args.args[0].nonce, "pending")

    async def test_explicit_start_with_ref_authenticates_without_confirmation(self):
        for payload, text in (({"command": "start"}, "Начать"),
                              ({"command": "auth_start"}, "🔐 Начать авторизацию"),
                              ({"command": "start"}, "🔐 Начать авторизацию"),
                              (None, "Начать")):
            self.services.auth_user.reset_mock()
            self.api.messages.send.reset_mock()
            await self.storage.remember_nonce(42, "previous")
            await self.controller.handle_message(self.message(
                text=text, payload=json.dumps(payload) if payload else None, ref="pending"))
            self.services.auth_user.assert_awaited_once()
            self.assertEqual(self.services.auth_user.await_args.args[0].nonce, "pending")
            self.assertIsNone(await self.storage.get_nonce(42))
            self.assertIn("Вход подтверждён", self.sent_text())
            self.assertNotIn("Подтвердить вход", self.sent_text())
            self.assertEqual((await self.storage.get(42))["screen"]["name"], "main")

    async def test_direct_auth_network_failure_retains_nonce_for_retry(self):
        self.services.auth_user.side_effect = TimeoutError()
        await self.controller.handle_message(self.message(payload='{"command":"auth_start"}', ref="retry"))
        self.assertEqual(await self.storage.get_nonce(42), "retry")
        self.services.auth_user.side_effect = None
        await self.controller.handle_message(self.message(payload='{"command":"auth_start"}'))
        self.assertEqual(self.services.auth_user.await_args.args[0].nonce, "retry")
        self.assertIsNone(await self.storage.get_nonce(42))

    async def test_new_and_legacy_auth_buttons_generate_login_link_without_nonce(self):
        nonces = []
        for command in ("auth_start", "start"):
            self.api.messages.send.reset_mock()
            await self.controller.handle_message(self.message(
                text="🔐 Начать авторизацию", payload=json.dumps({"command": command})))
            nonce = self.services.auth_user.await_args.args[0].nonce
            nonces.append(nonce)
            self.assertEqual(UUID(nonce).version, 4)
            self.assertEqual(str(UUID(nonce)), nonce)
            self.api.messages.send.assert_awaited_once()
            sent = self.api.messages.send.await_args.kwargs
            self.assertEqual(sent["message"], "Авторизация прошла успешно, нажмите кнопку для входа")
            action = json.loads(sent["keyboard"])["buttons"][0][0]["action"]
            self.assertEqual(action, {"type": "open_link", "label": "Войти",
                                      "link": "https://eazyclass.ru/schedule?nonce=" + nonce})
            self.assertIsNone(await self.storage.get_nonce(42))
        self.assertNotEqual(*nonces)

    async def test_deeplink_and_generated_nonce_produce_identical_http_requests(self):
        patch_jsonapi_client(verbose=False)
        client = AsyncClientSession(server_url="https://backend.invalid/api/v1/", platform="vk",
                                    hmac_secret="test-secret", schema=models_as_jsonschema)
        repository = JsonApiAccountRepository(client)
        self.services.auth_user.side_effect = repository.get_or_create
        response = NS(status=201, headers={}, json=AsyncMock(return_value={"data": {
            "type": "social-accounts", "id": "1",
            "attributes": {"platform": "vk", "socialId": "42", "chatId": "42", "extraData": {}},
            "relationships": {"user": {"data": {"type": "users", "id": "7"}}},
            "meta": {"nonceStatus": "authenticated"}}}))
        context = AsyncMock()
        context.__aenter__.return_value = response
        nonce = "187d06d3-26ac-4ed8-a066-7a44ecc717b3"
        try:
            with patch.object(client._aiohttp_session, "request", return_value=context) as request, \
                    patch("vkbot.api_client.api_client_session.time.time", return_value=1234567890):
                await self.controller.handle_message(self.message(text="Привет", ref=nonce))
                await self.click("auth_confirm")
                self.assertEqual(request.call_count, 1)
                deeplink_request = request.call_args
                with patch("vkbot.controller.uuid4", return_value=UUID(nonce)):
                    await self.controller.handle_message(self.message(payload='{"command":"auth_start"}'))
                self.assertEqual(request.call_count, 2)
                self.assertEqual(deeplink_request, request.call_args)
                method, url = request.call_args.args
                self.assertEqual(method.upper(), "POST")
                self.assertEqual(url, "https://backend.invalid/api/v1/auth-with-nonce/")
                body = json.loads(request.call_args.kwargs["data"])
                self.assertEqual(body["data"]["type"], "social-accounts")
                self.assertEqual(body["data"]["attributes"]["nonce"], nonce)
                self.assertTrue(request.call_args.kwargs["headers"]["X-Signature"])
        finally:
            await client.close()

    async def test_bot_generated_nonce_failure_never_returns_login_link(self):
        for status in ("failed", None):
            self.services.auth_user.return_value.nonce_status = status
            self.api.messages.send.reset_mock()
            await self.controller.handle_message(self.message(payload='{"command":"auth_start"}'))
            self.assertNotIn("Авторизация прошла успешно", self.sent_text())
            self.assertIsNone(self.api.messages.send.await_args.kwargs["keyboard"])

    async def test_expired_nonce_never_reaches_backend(self):
        await self.controller.handle_message(self.message(text="Привет", ref="old"))
        self.redis.now = 11
        await self.click("auth_confirm")
        self.services.auth_user.assert_not_awaited()
        self.assertIn("устарела", self.sent_text())

    async def test_rejected_nonce_never_reports_success(self):
        self.services.auth_user.return_value.nonce_status = "failed"
        await self.controller.handle_message(self.message(text="Привет", ref="bad"))
        await self.click("auth_confirm")
        self.assertNotIn("✅ Вход подтверждён", self.sent_text())
        self.assertIsNone(await self.storage.get_nonce(42))

    async def test_auth_retry_retains_nonce_on_network_failure(self):
        self.services.auth_user.side_effect = TimeoutError()
        await self.controller.handle_message(self.message(text="Привет", ref="retry"))
        await self.click("auth_confirm")
        self.assertEqual(await self.storage.get_nonce(42), "retry")

    async def test_cancel_clears_nonce(self):
        await self.controller.handle_message(self.message(text="Привет", ref="cancel"))
        await self.click("auth_cancel")
        self.assertIsNone(await self.storage.get_nonce(42))
        self.assertIsNone(self.services.auth_user.await_args.args[0].nonce)

    async def test_expired_state_does_not_execute_mutation(self):
        await self.controller.show(42, 42, {"name": "settings"}, [])
        self.redis.now = 601
        await self.click("toggle", notification_type="lesson_upcoming")
        self.services.notifications.set_enabled.assert_not_awaited()
        self.assertEqual((await self.storage.get(42))["screen"]["name"], "main")

    async def test_replayed_callback_does_not_toggle_twice(self):
        await self.controller.show(42, 42, {"name": "settings"}, [])
        revision = (await self.storage.get(42))["revision"]
        await self.click("toggle", notification_type="lesson_upcoming")
        await self.click("toggle", notification_type="lesson_upcoming", revision=revision)
        self.services.notifications.set_enabled.assert_awaited_once_with(
            "42", "lesson_upcoming", False, self.preferences["lesson_upcoming"].version)

    async def test_settings_uses_confirmed_state_and_ignores_callback_preference_id(self):
        async def change(social_id, kind, enabled, version):
            self.preferences[kind] = self.preferences[kind].model_copy(update={"enabled": enabled})
            return self.preferences
        self.services.notifications.set_enabled.side_effect = change
        await self.controller.show(42, 42, {"name": "settings"}, [])
        before = self.preferences["lesson_upcoming"].version
        await self.click("toggle", notification_type="lesson_upcoming", id="foreign-id", enabled=True)
        self.services.notifications.set_enabled.assert_awaited_once_with("42", "lesson_upcoming", False, before)
        keyboard_data = json.loads(self.api.messages.edit.await_args.kwargs["keyboard"])
        labels = [item["action"]["label"] for row in keyboard_data["buttons"] for item in row]
        self.assertIn("🔕 Предстоящие занятия", labels)
        self.assertIn("🔔 Изменения расписания", labels)

    async def test_old_notification_callback_only_refreshes(self):
        await self.controller.show(42, 42, {"name": "settings"}, [])
        await self.click("toggle", field="legacy_field")
        self.services.notifications.set_enabled.assert_not_awaited()
        self.assertEqual((await self.storage.get(42))["screen"]["name"], "settings")

    async def test_failed_notification_update_does_not_allow_callback_replay(self):
        await self.controller.show(42, 42, {"name": "settings"}, [])
        revision = (await self.storage.get(42))["revision"]
        self.services.notifications.set_enabled.side_effect = RuntimeError("PATCH rejected")
        with self.assertLogs("vkbot.controller", level="ERROR"):
            await self.click("toggle", notification_type="lesson_upcoming")
        self.assertIn("Не удалось выполнить запрос", self.sent_text())
        await self.click("toggle", notification_type="lesson_upcoming", revision=revision)
        self.services.notifications.set_enabled.assert_awaited_once()

    async def test_group_navigation_schedule_and_back(self):
        await self.controller.show(42, 42, {"name": "main"}, [])
        for screen in ({"name": "faculties"}, {"name": "grades", "faculty": 1},
                       {"name": "groups", "faculty": 1, "grade": 1},
                       {"name": "entity", "kind": "group", "id": 1},
                       {"name": "schedule", "kind": "group", "id": 1, "mode": "week"}):
            await self.click("open", screen=screen)
            self.assertEqual((await self.storage.get(42))["screen"]["name"], screen["name"])
        self.assertIn("Занятий нет", self.api.messages.edit.await_args.kwargs["message"])
        await self.click("back")
        self.assertEqual((await self.storage.get(42))["screen"]["name"], "entity")

    async def test_teacher_navigation(self):
        for screen in ({"name": "alphabet"}, {"name": "teachers", "letter": "И"},
                       {"name": "entity", "kind": "teacher", "id": 2}):
            text, rows = await self.controller.render(screen)
            self.assertTrue(rows)
            self.assertTrue(text)

    async def test_subscription_requires_confirmation_when_replacing(self):
        self.services.subscriptions.get_user_subscriptions.return_value = [NS(id=3)]
        await self.controller.show(42, 42, {"name": "entity", "kind": "group", "id": 1}, [])
        await self.click("subscribe")
        self.services.subscriptions.create.assert_not_awaited()
        self.assertEqual((await self.storage.get(42))["screen"]["name"], "confirm")
        await self.click("confirm")
        self.services.subscriptions.create.assert_awaited_once_with(self.group)

    async def test_external_subscription_change_invalidates_confirmation(self):
        self.services.subscriptions.get_user_subscriptions.return_value = [NS(id=4)]
        await self.controller.show(42, 42, {"name": "confirm", "kind": "group", "id": 1, "expected": [3]}, [])
        await self.click("confirm")
        self.services.subscriptions.create.assert_not_awaited()

    async def test_cannot_unsubscribe_another_users_subscription(self):
        await self.controller.show(42, 42, {"name": "settings"}, [])
        await self.click("unsubscribe", id=123)
        self.services.subscriptions.delete.assert_not_awaited()

    async def test_panel_main_and_schedule_send_only_one_message(self):
        self.user.subscriptions = [NS(id=3, object=self.group, display_name=self.group.display_name)]
        for command, expected in (("main", "main"), ("mine", "schedule"), ("faculties", "faculties"), ("alphabet", "alphabet")):
            self.api.messages.send.reset_mock()
            self.api.messages.edit.reset_mock()
            await self.controller.handle_message(self.message(payload=json.dumps({"command": command})))
            self.api.messages.send.assert_awaited_once()
            sent = self.api.messages.send.await_args.kwargs
            self.assertNotEqual(sent["message"], "Главное меню")
            self.assertFalse(json.loads(sent["keyboard"])["inline"])
            edited = self.api.messages.edit.await_args.kwargs
            self.assertEqual(edited["message_id"], 1)
            self.assertEqual(edited["message"], sent["message"])
            self.assertTrue(json.loads(edited["keyboard"])["inline"])
            self.assertEqual((await self.storage.get(42))["screen"]["name"], expected)

    async def test_entity_link_visibility(self):
        for target, kind in ((self.group, "group"), (self.teacher, "teacher")):
            target = target.model_copy(update={"endpoint": "/schedule/1"})
            self.services.target = lambda kind, entity_id: target
            for enabled in (True, False):
                self.settings.show_entity_links = enabled
                _, rows = await self.controller.render({"name": "entity", "kind": kind, "id": target.id})
                links = [b for row in rows for b in row if b["action"]["type"] == "open_link"]
                self.assertEqual(bool(links), enabled)

    async def test_restart_discards_pending_auth_and_history_not_subscription(self):
        await self.storage.remember_nonce(42, "pending")
        await self.storage.set(42, {"screen": {"name": "auth"}, "history": [{"name": "settings"}]})
        await self.controller.handle_message(self.message(payload='{"command":"restart"}'))
        self.assertIsNone(await self.storage.get_nonce(42))
        self.assertIsNone(self.services.auth_user.await_args.args[0].nonce)
        self.services.subscriptions.delete.assert_not_awaited()
        state = await self.storage.get(42)
        self.assertEqual(state["screen"]["name"], "main")
        self.assertEqual(state["history"], [])

    async def test_picker_text_does_not_repeat_button_labels(self):
        for screen, label in (({"name": "faculties"}, "ФАК"),
                              ({"name": "grades", "faculty": 1}, "1 курс"),
                              ({"name": "groups", "faculty": 1, "grade": 1}, "А-101"),
                              ({"name": "teachers", "letter": "И"}, self.teacher.short_name)):
            text, rows = await self.controller.render(screen)
            self.assertNotIn(label, text)
            self.assertNotIn("1/1", text)
            self.assertIn(label, [b["action"]["label"] for row in rows for b in row])
            self.assertNotIn(self.teacher.full_name, json.dumps(rows, ensure_ascii=False))

    async def test_teacher_short_name_in_every_entity_screen(self):
        for name in ("entity", "confirm", "schedule"):
            text, _ = await self.controller.render({"name": name, "kind": "teacher", "id": 2})
            self.assertIn(self.teacher.short_name, text)
            self.assertNotIn(self.teacher.full_name, text)

    async def test_main_has_single_schedule_entry_and_unsubscribe_is_in_settings(self):
        text, rows = await self.controller.render({"name": "main"})
        self.assertNotIn("🗓 Расписание", [b["action"]["label"] for row in rows for b in row])
        self.assertIn("Выберите группу или преподавателя", text)
        sub = NS(id=3, object=self.teacher, display_name=self.teacher.display_name)
        self.user.subscriptions = [sub]
        text, rows = await self.controller.render({"name": "main"})
        labels = [b["action"]["label"] for row in rows for b in row]
        self.assertIn("🗓 Расписание", labels)
        self.assertNotIn("Сегодня", labels)
        self.assertNotIn("✖ Отписаться", labels)
        self.assertIn(self.teacher.short_name, text)
        self.assertNotIn("Выберите группу или преподавателя", text)
        _, rows = await self.controller.render({"name": "settings"})
        self.assertIn("✖ Отписаться", [b["action"]["label"] for row in rows for b in row])
        self.services.subscriptions.get_user_subscriptions.return_value = [sub]
        await self.controller.show(42, 42, {"name": "settings"}, [])
        await self.click("unsubscribe", id=3)
        self.services.subscriptions.delete.assert_awaited_once_with(3)

    async def test_period_buttons_are_arrows(self):
        _, rows = await self.controller.render({"name": "schedule", "kind": "group", "id": 1})
        self.assertEqual([b["action"]["label"] for b in rows[0]], ["⬅️", "➡️"])
        labels = [b["action"]["label"] for row in rows for b in row]
        self.assertNotIn("Завтра", labels)
        self.assertNotIn("На 3 дня", labels)
        self.assertEqual([b["action"]["label"] for b in rows[1]], ["Сегодня", "Неделя"])

    async def test_schedule_formatting_sent_edited_and_cleared(self):
        screen = {"name": "schedule", "kind": "group", "id": 1, "mode": "week"}
        await self.controller.show(42, 42, screen.copy(), [], install_keyboard=True)
        sent = self.api.messages.send.await_args.kwargs
        edited = self.api.messages.edit.await_args.kwargs
        self.assertEqual(sent["format_data"], edited["format_data"])
        self.assertEqual(len(json.loads(sent["format_data"])["items"]), 7)
        await self.controller.show(42, 42, screen.copy(), [], cmid=10)
        self.assertEqual(len(json.loads(self.api.messages.edit.await_args.kwargs["format_data"])["items"]), 7)
        await self.controller.show(42, 42, {"name": "main"}, [], cmid=10)
        self.assertEqual(json.loads(self.api.messages.edit.await_args.kwargs["format_data"])["items"], [])

    async def test_entity_opens_shared_schedule_screen(self):
        for kind, entity_id in (("group", 1), ("teacher", 2)):
            _, rows = await self.controller.render({"name": "entity", "kind": kind, "id": entity_id})
            labels = [b["action"]["label"] for row in rows for b in row]
            self.assertIn("🗓 Расписание", labels)
            self.assertFalse(set(labels) & {"Сегодня", "Завтра", "На 3 дня", "Неделя"})
            screen = json.loads(rows[0][0]["action"]["payload"])["screen"]
            self.assertEqual(screen, {"name": "schedule", "kind": kind, "id": entity_id, "mode": "1day", "shift": 0})
            _, schedule_rows = await self.controller.render(screen)
            self.assertIn("Сегодня", [b["action"]["label"] for row in schedule_rows for b in row])

    async def test_context_is_scoped_to_event_even_on_error(self):
        token = request_context.set({"user_id": "outer"})
        async def auth(dto):
            self.assertEqual(request_context.get()["user_id"], "42")
            raise TimeoutError()
        self.services.auth_user.side_effect = auth
        try:
            await self.controller.handle_message(self.message())
            self.assertEqual(request_context.get(), {"user_id": "outer"})
        finally:
            request_context.reset(token)

    async def test_group_chat_is_ignored(self):
        await self.controller.handle_message(self.message(peer_id=2000000001))
        self.services.auth_user.assert_not_awaited()
        self.api.messages.send.assert_not_awaited()

    async def test_edit_restriction_falls_back_to_new_message(self):
        class EditExpired(Exception):
            code = 909
        self.api.messages.edit.side_effect = EditExpired()
        await self.controller.show(42, 42, {"name": "main"}, [], cmid=10)
        self.api.messages.send.assert_awaited_once()
        self.assertTrue(await self.storage.get(42))

    async def test_unexpected_edit_failure_does_not_send_duplicate(self):
        self.api.messages.edit.side_effect = TimeoutError()
        with self.assertRaises(TimeoutError):
            await self.controller.show(42, 42, {"name": "main"}, [], cmid=10)
        self.api.messages.send.assert_not_awaited()

    async def test_real_vkbottle_router_dispatches_both_event_types(self):
        bot = Bot("test")
        handlers = NS(handle_message=AsyncMock(wraps=self.controller.handle_message),
                      handle_callback=AsyncMock(wraps=self.controller.handle_callback))
        register_handlers(bot, handlers)
        await bot.process_event({"type": "message_new", "group_id": 1, "object": {
            "message": {"id": 1, "date": 1, "from_id": 42, "peer_id": 42, "out": 0,
                        "text": "Начать", "attachments": [], "fwd_messages": [], "conversation_message_id": 1, "version": 1},
            "client_info": {"button_actions": ["text", "callback"], "keyboard": True, "inline_keyboard": True},
        }})
        handlers.handle_message.assert_awaited_once()
        self.services.auth_user.assert_awaited_once()
        state = await self.storage.get(42)
        await bot.process_event({"type": "message_event", "group_id": 1, "object": {
            "event_id": "event", "user_id": 42, "peer_id": 42,
            "payload": {"command": "open", "screen": {"name": "settings"}, "revision": state["revision"]},
            "conversation_message_id": 1,
        }})
        handlers.handle_callback.assert_awaited_once()
        self.assertEqual((await self.storage.get(42))["screen"]["name"], "settings")


class StorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_lock_excludes_concurrent_mutations_and_releases_on_failure(self):
        store = RedisStorage(MemoryRedis(), "test")
        with self.assertRaises(ValueError):
            async with store.lock(1):
                with self.assertRaises(BusyError):
                    async with store.lock(1):
                        self.fail("Concurrent lock accepted")
                raise ValueError()
        async with store.lock(1):
            pass

    async def test_state_and_nonce_are_isolated_and_expire(self):
        redis = MemoryRedis()
        first, second = RedisStorage(redis, "one", nonce_ttl=10), RedisStorage(redis, "two")
        await first.set(1, {"name": "main"})
        await first.remember_nonce(1, "a")
        self.assertEqual(await second.get(1), {})
        self.assertIsNone(await first.get_nonce(2))
        redis.now = 9
        await first.remember_nonce(1, "a")
        redis.now = 11
        self.assertIsNone(await first.get_nonce(1))


class FormattingTests(unittest.TestCase):
    def test_bold_dates_use_utf16_offsets_after_emoji(self):
        text = "🗓 Группа\n\n▰▰▰▰ ВТ 20.10.2026 ▰▰▰▰\n\n1️⃣ Предмет 😀\n\n▰▰▰▰ СР 21.10.2026 ▰▰▰▰"
        items = json.loads(schedule_format_data(text))["items"]
        encoded = text.encode("utf-16-le")
        self.assertEqual(len(items), 2)
        for item, expected in zip(items, ("ВТ 20.10.2026", "СР 21.10.2026")):
            self.assertEqual(item["type"], "bold")
            self.assertEqual(encoded[item["offset"] * 2:(item["offset"] + item["length"]) * 2].decode("utf-16-le"), expected)

    def test_short_list_needs_no_paging_and_long_list_leaves_navigation_slots(self):
        choices, page, pages = paginate(list(range(8)), 0)
        self.assertEqual(len(choices), 8)
        self.assertEqual(pages, 1)
        choices, page, pages = paginate(list(range(9)), 0)
        self.assertEqual(len(choices), 6)
        self.assertEqual(pages, 2)

    def test_panel_keeps_auth_without_restart_or_catalog_actions(self):
        panel = json.loads(persistent_keyboard())
        commands = [json.loads(b["action"]["payload"])["command"] for row in panel["buttons"] for b in row]
        self.assertIn("auth_start", commands)
        self.assertNotIn("start", commands)
        self.assertNotIn("restart", commands)
        self.assertFalse(set(commands) & {"mine", "faculties", "alphabet"})

    def test_daily_heading_is_not_repeated_for_each_lesson(self):
        target = GroupDTO(id=1, title="Группа", grade=1, faculty_id=1)
        lessons = [LessonDTO(id=i, number=i, date="2026-10-10", startTime=None,
                            endTime=None, subject="Предмет", classroom="101", subgroup="0") for i in (1, 2)]
        span = DateSpanDTO(start=date(2026, 10, 10), end=date(2026, 10, 10))
        pages = schedule_pages(target, lessons, span)
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0].count("▰▰▰▰ СБ 10.10.2026 ▰▰▰▰\n\n"), 1)
        self.assertNotIn("━━━━━━━━━━━━", pages[0])
        self.assertIn("1️⃣ --:--–--:-- 📍 101", pages[0])
        self.assertNotIn("10.10.2026 — 10.10.2026", pages[0])
        span = DateSpanDTO(start=date(2026, 10, 10), end=date(2026, 10, 11))
        pages = schedule_pages(target, lessons, span)
        self.assertNotIn("━━━━━━━━━━━━", pages[0])
        self.assertIn("\n\n▰▰▰▰ ВС 11.10.2026 ▰▰▰▰\n\nЗанятий нет", pages[0])

    def test_missing_vk_profile_fields(self):
        dto = AuthDTO.from_vk(UsersUserFull(id=1, first_name="Иван", last_name=""), 1)
        self.assertEqual(dto.platform, "vk")
        self.assertIsNone(dto.extra_data["city"])

    def test_invalid_payload(self):
        for value in ("[]", "null", "broken", None, 4):
            self.assertEqual(payload_dict(value), {})

    def test_period_bounds(self):
        span, shift, _, _ = date_span("week", 999, "Europe/Moscow")
        self.assertEqual(shift, 1)
        self.assertEqual(span.start.weekday(), 0)
        self.assertEqual((span.end - span.start).days, 6)

    def test_long_schedule_keeps_all_data_and_respects_size(self):
        target = GroupDTO(id=1, title="Группа", grade=1, faculty_id=1)
        lessons = [LessonDTO(id=i, number=i, part=1, date="2026-10-10", startTime="09:00",
                            endTime="10:30", subject=f"Предмет {i} " + "я" * 1000,
                            annotation="Практика", classroom="101", subgroup="2") for i in range(1, 20)]
        span = DateSpanDTO(start=date(2026, 10, 10), end=date(2026, 10, 10))
        pages = schedule_pages(target, lessons, span)
        self.assertGreater(len(pages), 1)
        self.assertTrue(all(len(p) <= 3500 for p in pages))
        for i in range(1, 20):
            self.assertIn(f"Предмет {i} ", "\n".join(pages))
        self.assertIn("Практика", "\n".join(pages))


class ApiContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_auth_validation_error_preserves_backend_detail_without_nonce(self):
        client = AsyncClientSession(server_url="https://backend.invalid", platform="vk", hmac_secret="secret")
        response = NS(status=400, json=AsyncMock(return_value={
            "nonce": ["Must be a UUID: private-nonce-value"], "access_token": "private-token"}))
        context = AsyncMock()
        context.__aenter__.return_value = response
        token = request_context.set({"user_id": "42", "hmac": True})
        try:
            with patch.object(client._aiohttp_session, "request", return_value=context):
                with self.assertRaises(Exception) as caught:
                    await client.http_request_async("POST", "https://backend.invalid/auth-with-nonce/",
                        {"data": {"attributes": {"nonce": "private-nonce-value"}}})
            detail = str(caught.exception)
            self.assertIn("400", detail)
            self.assertIn("Must be a UUID", detail)
            self.assertNotIn("private-nonce-value", detail)
            self.assertNotIn("private-token", detail)
        finally:
            request_context.reset(token)
            await client.close()

    async def test_account_auth_serialization_and_meta_with_real_jsonapi_client(self):
        patch_jsonapi_client(verbose=False)
        client = AsyncClientSession(server_url="https://backend.invalid/api/v1/", platform="vk", hmac_secret="secret", schema=models_as_jsonschema)
        client.http_request_async = AsyncMock(return_value=(201, {"data": {
            "type": "social-accounts", "id": "1",
            "attributes": {"platform": "vk", "socialId": "42", "chatId": "42", "extraData": {}},
            "relationships": {"user": {"data": {"type": "users", "id": "7"}}},
            "meta": {"created": True, "nonceStatus": "authenticated"},
        }}, "https://backend.invalid/api/v1/social-accounts/1/"))
        token = request_context.set({"user_id": "42"})
        try:
            dto = AuthDTO(social_id="42", chat_id="42", first_name="Иван", last_name="", nonce="nonce")
            result = await JsonApiAccountRepository(client).get_or_create(dto)
            self.assertEqual(result.nonce_status, "authenticated")
            self.assertTrue(result.created)
            self.assertEqual(result.user_id, "7")
            args = client.http_request_async.await_args.args
            self.assertTrue(args[1].endswith("/auth-with-nonce/"))
            self.assertEqual(args[2]["data"]["attributes"]["socialId"], "42")
        finally:
            request_context.reset(token)
            await client.close()

    async def test_read_without_etag_fetches_updated_document(self):
        client = AsyncClientSession(server_url="https://backend.invalid", platform="vk", hmac_secret="secret")
        url = "https://backend.invalid/lessons/"
        try:
            client.read({"data": []}, url)
            updated = object()
            client._ext_fetch_by_url_async = AsyncMock(return_value=updated)
            self.assertIs(await client.fetch_document_by_url_async(url), updated)
            client._ext_fetch_by_url_async.assert_awaited_once_with(url)
        finally:
            await client.close()

    async def test_private_requests_require_context_and_sign_correct_user(self):
        client = AsyncClientSession(server_url="https://backend.invalid/api/v1/", platform="vk", hmac_secret="secret")
        try:
            token = request_context.set({"hmac": True})
            try:
                with self.assertRaises(RuntimeError):
                    client._build_authenticated_request_kwargs("GET", "https://backend.invalid/api/v1/users/me")
            finally:
                request_context.reset(token)
            token = request_context.set({"hmac": True, "user_id": "42"})
            try:
                headers = client._build_authenticated_request_kwargs("GET", "https://backend.invalid/api/v1/users/me")["headers"]
                self.assertEqual(headers["X-Social-ID"], "42")
                self.assertEqual(headers["X-Platform"], "vk")
                self.assertTrue(headers["X-Signature"])
            finally:
                request_context.reset(token)
        finally:
            await client.close()


if __name__ == "__main__":
    unittest.main()
