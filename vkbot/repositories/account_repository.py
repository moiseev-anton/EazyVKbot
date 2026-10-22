import logging

from vkbot.context import set_hmac
from vkbot.dto import AuthDTO, AuthResponseDTO
from vkbot.repositories.base_repository import JsonApiBaseRepository
from vkbot.repositories.exceptions import ApiError

logger = logging.getLogger(__name__)


class JsonApiAccountRepository(JsonApiBaseRepository):
    AUTH_URL = "/auth/"
    AUTH_WITH_NONCE_URL = "/auth-with-nonce/"

    async def get_or_create(self, auth_dto: AuthDTO) -> AuthResponseDTO:
        try:
            attrs = auth_dto.model_dump(exclude_none=True)
            url_suffix = self.AUTH_WITH_NONCE_URL if auth_dto.nonce else self.AUTH_URL

            with set_hmac(True):
                account_resource = self.api_client.create(_type="social-accounts", **attrs)
                await account_resource.commit(custom_url=f'{self.api_client.url_prefix}{url_suffix}')

            account_meta = getattr(account_resource, "meta", {}) or {}

            account_dto = AuthResponseDTO.from_jsonapi(account_resource, )
            if isinstance(account_meta, dict):
                account_dto.created = account_meta.get("created", False)
                account_dto.nonce_status = account_meta.get("nonceStatus")
            else:
                account_dto.created = getattr(account_meta, "created", False)
                account_dto.nonce_status = getattr(account_meta, "nonceStatus", None)

            return account_dto

        except Exception as e:
            raise ApiError(f"Failed to register user: {str(e)}")
