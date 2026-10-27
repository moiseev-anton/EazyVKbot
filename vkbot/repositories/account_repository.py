import logging
from cachetools import TTLCache

from vkbot.context import set_hmac
from vkbot.dto import AuthDTO, AuthResponseDTO
from vkbot.repositories.base_repository import JsonApiBaseRepository
from vkbot.repositories.exceptions import ApiError

logger = logging.getLogger(__name__)


class JsonApiAccountRepository(JsonApiBaseRepository):
    AUTH_URL = "/auth/"
    AUTH_WITH_NONCE_URL = "/auth-with-nonce/"

    def __init__(self, api_client):
        super().__init__(api_client)
        self.authenticated_accounts = TTLCache(maxsize=10000, ttl=3600)

    async def get_current_account_id(self, platform, social_id):
        key = (platform, str(social_id))
        account_id = self.authenticated_accounts.get(key)
        if account_id is not None:
            document, _ = await self._fresh_document(str(self.api_client._url_for_resource("social-accounts", account_id)))
            accounts = document.resources
        else:
            accounts = [account async for account in self._fresh_resources("social-accounts")]
        matches = [account for account in accounts
                   if account.platform == platform and str(account.social_id) == str(social_id)]
        if len(matches) != 1:
            raise ApiError("Current social account could not be uniquely identified")
        self.authenticated_accounts[key] = str(matches[0].id)
        return str(matches[0].id)

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

            if (account_dto.platform, account_dto.social_id) != (auth_dto.platform, auth_dto.social_id):
                raise ApiError("Authorization returned a different social account")
            self.authenticated_accounts[(account_dto.platform, account_dto.social_id)] = str(account_dto.id)
            return account_dto

        except Exception as e:
            raise ApiError(f"Failed to register user: {str(e)}")
