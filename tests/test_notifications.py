import os
from copy import deepcopy
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

os.environ.update(BOT_TOKEN="test-token", API_BASE_URL="https://backend.invalid/api/v1/",
                  HMAC_SECRET="test-secret", BASE_SCRAPING_URL="https://site.invalid")

from vkbot.api_client import AsyncClientSession, models_as_jsonschema
from vkbot.api_client.client_patch import patch_jsonapi_client
from vkbot.context import request_context
from vkbot.dto import AuthDTO, UserDTO
from vkbot.notifications import NotificationPreferences
from vkbot.repositories.account_repository import JsonApiAccountRepository
from vkbot.repositories.notification_preference_repository import JsonApiNotificationPreferenceRepository
from vkbot.repositories.exceptions import ApiError


def account(account_id="45", social_id="42", platform="vk"):
    return {"type": "social-accounts", "id": account_id,
            "attributes": {"platform": platform, "socialId": social_id, "chatId": social_id, "extraData": {}},
            "relationships": {"user": {"data": {"type": "users", "id": "900"}}},
            "meta": {"nonceStatus": "authenticated"}}


def preference(pref_id="123", account_id="45", kind="lesson_upcoming", enabled=True, channel="vk", updated="2026-10-27T00:00:00Z"):
    return {"type": "notification-preferences", "id": pref_id,
            "attributes": {"notificationType": kind, "channel": channel, "enabled": enabled,
                           "createdAt": updated, "updatedAt": updated},
            "relationships": {"socialAccount": {"data": {"type": "social-accounts", "id": account_id}}}}


class NotificationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        patch_jsonapi_client(False)
        self.token = request_context.set({"user_id": "42", "hmac": False})
        self.client = AsyncClientSession(server_url="https://backend.invalid/api/v1/", platform="vk",
                                         hmac_secret="test-secret", schema=models_as_jsonschema)
        self.accounts = JsonApiAccountRepository(self.client)
        self.repo = JsonApiNotificationPreferenceRepository(self.client)
        self.service = NotificationPreferences(self.accounts, self.repo, "vk")
        self.root = "https://backend.invalid/api/v1/"
        self.pages = {
            self.root + "social-accounts/": {"data": [account("44", "999"), account("46", "42", "telegram")],
                                            "links": {"next": "?page=2"}},
            self.root + "social-accounts/?page=2": {"data": [account()]},
            self.root + "social-accounts/45/": {"data": account()},
            self.root + "notification-preferences/": {"data": [preference("999", "44"), preference("998", "46", channel="telegram")],
                                                       "links": {"next": {"href": "?page=2"}}},
            self.root + "notification-preferences/?page=2": {"data": [], "links": {"next": "?page=3"}},
            self.root + "notification-preferences/?page=3": {"data": [preference(), preference("124", kind="schedule_updated")]},
            self.root + "notification-preferences/123/": {"data": preference()},
        }
        self.patches = []

        async def http(method, url, send_json=None, **kwargs):
            method = method.upper()
            self.assertTrue(request_context.get()["hmac"])
            self.assertEqual(request_context.get()["user_id"], "42")
            if method == "PATCH":
                self.patches.append((url, send_json))
                updated = preference(enabled=send_json["data"]["attributes"]["enabled"], updated="2026-10-27T01:00:00Z")
                self.pages[url] = {"data": updated}
                self.pages[self.root + "notification-preferences/?page=3"]["data"][0] = updated
                return 204, {}, None
            if method == "POST":
                return 201, {"data": account()}, None
            self.assertEqual(method, "GET")
            return 200, deepcopy(self.pages[url]), None

        self.client.http_request_async = AsyncMock(side_effect=http)

    async def asyncTearDown(self):
        request_context.reset(self.token)
        await self.client.close()

    async def test_filters_exact_account_across_pages_including_empty_page(self):
        result = await self.service.get_preferences("42")
        self.assertEqual(set(result), {"lesson_upcoming", "schedule_updated"})
        self.assertEqual(result["lesson_upcoming"].id, "123")
        self.assertTrue(all(p.social_account_id == "45" for p in result.values()))
        self.assertFalse(request_context.get()["hmac"])

    async def test_auth_account_id_is_reused_and_reauth_never_changes_preferences(self):
        for _ in range(2):
            await self.accounts.get_or_create(AuthDTO(social_id="42", chat_id="42", first_name="Иван", last_name=""))
        self.assertEqual(self.accounts.authenticated_accounts[("vk", "42")], "45")
        await self.service.get_preferences("42")
        calls = self.client.http_request_async.await_args_list
        self.assertFalse(any(c.args[1] == self.root + "social-accounts/" for c in calls))
        self.assertEqual(len([c for c in calls if c.args[0].upper() == "POST"]), 2)
        self.assertEqual(self.patches, [])

    async def test_patch_uses_preference_id_and_only_boolean_enabled(self):
        before = (await self.service.get_preferences("42"))["lesson_upcoming"]
        result = await self.service.set_enabled("42", "lesson_upcoming", False, before.version)
        self.assertFalse(result["lesson_upcoming"].enabled)
        self.assertEqual(self.patches, [(self.root + "notification-preferences/123/", {
            "data": {"type": "notification-preferences", "id": "123", "attributes": {"enabled": False}}})])
        await self.service.set_enabled("42", "lesson_upcoming", False, before.version)
        self.assertEqual(len(self.patches), 1)

    async def test_stale_version_does_not_patch_even_when_enabled_returns_to_old_value(self):
        before = (await self.service.get_preferences("42"))["lesson_upcoming"]
        self.pages[self.root + "notification-preferences/?page=3"]["data"][0] = preference(updated="2026-10-27T01:00:00Z")
        await self.service.set_enabled("42", "lesson_upcoming", False, before.version)
        self.assertEqual(self.patches, [])

    async def test_reassigned_preference_does_not_patch(self):
        before = (await self.service.get_preferences("42"))["lesson_upcoming"]
        self.pages[self.root + "notification-preferences/?page=3"]["data"][0] = preference(account_id="44")
        with self.assertRaises(ApiError):
            await self.service.set_enabled("42", "lesson_upcoming", False, before.version)
        self.assertEqual(self.patches, [])

    async def test_missing_duplicate_or_wrong_channel_fails_closed(self):
        url = self.root + "notification-preferences/?page=3"
        for rows in ([preference()], [preference(), preference(), preference("124", kind="schedule_updated")],
                     [preference(channel="telegram"), preference("124", kind="schedule_updated")]):
            self.pages[url] = {"data": rows}
            with self.assertRaises(ApiError):
                await self.service.get_preferences("42")
        self.assertEqual(self.patches, [])

    async def test_cached_account_is_checked_against_social_id(self):
        self.accounts.authenticated_accounts[("vk", "42")] = "45"
        self.pages[self.root + "social-accounts/45/"] = {"data": account(social_id="999")}
        with self.assertRaises(ApiError):
            await self.service.get_preferences("42")

    async def test_patch_error_and_unconfirmed_state_are_not_success(self):
        before = (await self.service.get_preferences("42"))["lesson_upcoming"]
        self.repo.set_enabled = AsyncMock(side_effect=RuntimeError("400"))
        with self.assertRaises(RuntimeError):
            await self.service.set_enabled("42", "lesson_upcoming", False, before.version)
        self.repo.set_enabled = AsyncMock(return_value=before)
        with self.assertRaises(ApiError):
            await self.service.set_enabled("42", "lesson_upcoming", False, before.version)

    async def test_only_strict_boolean_is_accepted(self):
        for enabled in (0, 1, "false", None):
            with self.assertRaises(TypeError):
                await self.repo.set_enabled("123", enabled)
        self.client.http_request_async.assert_not_awaited()

    async def test_pagination_cycle_and_external_origin_are_rejected(self):
        for next_link in (self.root + "notification-preferences/", "https://foreign.invalid/preferences"):
            self.pages[self.root + "notification-preferences/"]["links"]["next"] = next_link
            with self.assertRaises(ValueError):
                await self.repo.get_preferences()

    async def test_user_without_removed_attributes_deserializes(self):
        doc = self.client.read({"data": {"type": "users", "id": "900",
            "attributes": {"firstName": "Иван", "lastName": "", "username": "test"},
            "relationships": {"accounts": {"data": [{"type": "social-accounts", "id": "45"}]},
                              "subscriptions": {"data": []}}}}, no_cache=True)
        user = UserDTO.from_jsonapi(doc.resource)
        self.assertEqual(user.id, "900")
        self.assertEqual(user.account_ids, [45])
