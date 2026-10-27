from vkbot.repositories.exceptions import ApiError

NOTIFICATION_TYPES = ("schedule_updated", "lesson_upcoming")


class NotificationPreferences:
    def __init__(self, accounts, preferences, platform):
        self.accounts, self.preferences, self.platform = accounts, preferences, platform

    async def get_preferences(self, social_id):
        account_id = await self.accounts.get_current_account_id(self.platform, str(social_id))
        result = {}
        for preference in await self.preferences.get_preferences():
            if preference.social_account_id != account_id:
                continue
            if preference.channel != self.platform:
                raise ApiError("Notification preference platform mismatch")
            if preference.notification_type not in NOTIFICATION_TYPES:
                continue
            if preference.notification_type in result:
                raise ApiError("Duplicate notification preference for current account")
            result[preference.notification_type] = preference
        if set(result) != set(NOTIFICATION_TYPES):
            raise ApiError("Notification preferences for current account are missing")
        return result

    async def set_enabled(self, social_id, notification_type, enabled, version):
        # The caller holds the existing per-user Redis lock.
        if notification_type not in NOTIFICATION_TYPES or type(enabled) is not bool:
            raise ValueError("Invalid notification preference change")
        preferences = await self.get_preferences(social_id)
        current = preferences[notification_type]
        if current.version != version or current.enabled == enabled:
            return preferences
        confirmed = await self.preferences.set_enabled(current.id, enabled)
        if (confirmed.id != current.id or confirmed.social_account_id != current.social_account_id
                or confirmed.channel != self.platform or confirmed.notification_type != notification_type
                or confirmed.enabled != enabled):
            raise ApiError("Notification preference change was not confirmed")
        preferences[notification_type] = confirmed
        return preferences
