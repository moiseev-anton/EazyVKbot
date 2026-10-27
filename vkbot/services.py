"""Application services independent of VK events and keyboards."""
import asyncio
from time import monotonic
from vkbot.notifications import NotificationPreferences
from vkbot.repositories.notification_preference_repository import JsonApiNotificationPreferenceRepository

from vkbot.repositories import (
    JsonApiAccountRepository, JsonApiUserRepository, JsonApiSubscriptionRepository,
    JsonApiGroupRepository, JsonApiTeacherRepository, JsonApiLessonRepository,
)


class Services:
    def __init__(self, client, settings):
        self.accounts = JsonApiAccountRepository(client)
        self.users = JsonApiUserRepository(client)
        self.notifications = NotificationPreferences(
            self.accounts, JsonApiNotificationPreferenceRepository(client), settings.platform)
        self.subscriptions = JsonApiSubscriptionRepository(client)
        self.groups_repo = JsonApiGroupRepository(client, settings.groups_cache_file_path)
        self.teachers_repo = JsonApiTeacherRepository(client, settings.teachers_cache_file_path)
        self.lessons = JsonApiLessonRepository(client)
        self.groups = {}
        self.teachers = {}
        self._refreshed_at = 0
        self._refresh_lock = asyncio.Lock()

    async def auth_user(self, auth):
        account = await self.accounts.get_or_create(auth)
        account.user = await self.users.get_user()
        return account

    async def get_user(self):
        user = await self.users.get_user()
        user.subscriptions = await self.subscriptions.get_user_subscriptions()
        return user

    async def refresh(self):
        async with self._refresh_lock:
            if self._refreshed_at and monotonic() - self._refreshed_at < 600:
                return
            groups, teachers = await asyncio.gather(
                self.groups_repo.get_groups_with_faculties(), self.teachers_repo.get_teachers()
            )
            # Swap only after both requests succeed.
            self.groups = {g.id: g for g in groups}
            self.teachers = {t.id: t for t in teachers}
            self._refreshed_at = monotonic()

    def target(self, kind, entity_id):
        if kind not in ("group", "teacher"):
            raise ValueError("Unknown target kind")
        return (self.groups if kind == "group" else self.teachers)[int(entity_id)]
