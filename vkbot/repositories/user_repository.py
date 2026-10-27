import logging

from jsonapi_client.document import Document
from jsonapi_client.resourceobject import ResourceObject

from vkbot.context import set_hmac
from vkbot.dto import UserDTO
from vkbot.repositories.base_repository import JsonApiBaseRepository
from vkbot.repositories.exceptions import ApiError

logger = logging.getLogger(__name__)


class JsonApiUserRepository(JsonApiBaseRepository):
    CONTEXT_USER_URL = "users/me"

    async def get_user(self) -> UserDTO:
        try:
            with set_hmac(True):
                document: Document = await self.api_client.get(self.CONTEXT_USER_URL)

            user_resource: ResourceObject = document.resource
            return UserDTO.from_jsonapi(user_resource)
        except Exception as e:
            raise ApiError(f"Failed to register user: {str(e)}")
