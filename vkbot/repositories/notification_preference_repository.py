from vkbot.context import set_hmac
from jsonapi_client.common import HttpMethod
from vkbot.dto.notification_preference_dto import NotificationPreferenceDTO
from vkbot.repositories.base_repository import JsonApiBaseRepository


class JsonApiNotificationPreferenceRepository(JsonApiBaseRepository):
    resource_type = "notification-preferences"

    async def get_preferences(self):
        return [NotificationPreferenceDTO.from_jsonapi(resource)
                async for resource in self._fresh_resources(self.resource_type)]

    async def set_enabled(self, preference_id: str, enabled: bool):
        if type(enabled) is not bool:
            raise TypeError("enabled must be a boolean")
        url = str(self.api_client._url_for_resource(self.resource_type, preference_id))
        with set_hmac(True):
            await self.api_client.http_request_async(HttpMethod.PATCH, url, send_json={
                "data": {
                    "type": self.resource_type,
                    "id": preference_id,
                    "attributes": {"enabled": enabled},
                },
            })
        document, _ = await self._fresh_document(url)
        return NotificationPreferenceDTO.from_jsonapi(document.resource)
