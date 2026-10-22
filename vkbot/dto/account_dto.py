from typing import Optional, Dict, Any, List, TYPE_CHECKING

from vkbottle_types.objects import UsersUserFull
from pydantic import BaseModel, field_validator

from vkbot.config import settings

if TYPE_CHECKING:
    from .user_dto import UserDTO

class AuthDTO(BaseModel):
    platform: str = settings.platform
    social_id: str
    chat_id: str
    first_name: str = "Anonymous"
    last_name: str
    extra_data: Dict[str, Any] = {}
    nonce: Optional[str] = None

    class Config:
        _resource_name = "social-accounts"

    @field_validator("social_id", )
    def validate_non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("social_id cannot be empty")
        return value

    @classmethod
    def from_vk(cls, vk_user: UsersUserFull, chat_id) -> "AuthDTO":
        return cls(
            social_id=str(vk_user.id),
            chat_id=str(chat_id),
            first_name=vk_user.first_name,
            last_name=vk_user.last_name or "",
            extra_data={
                "nickname": vk_user.nickname,
                "domain": vk_user.domain,
                "bdate": vk_user.bdate,
                "city": vk_user.city.model_dump(mode="json") if vk_user.city else None,
                "email": getattr(vk_user, "email", None),
                "mobile_phone": vk_user.mobile_phone,
                "photo_200": vk_user.photo_200,
                "screen_name": vk_user.screen_name,
                "sex": vk_user.sex.value if vk_user.sex is not None else None,
                "timezone": vk_user.timezone,
            },
        )


class AccountDTO(BaseModel):
    id: int
    platform: str
    social_id: str
    chat_id: str
    extra_data: Dict[str, Any] = {}
    user_id: str
    user: Optional["UserDTO"] = None

    class Config:
        _resource_name = "social-accounts"

    @field_validator("id", "social_id", "platform", "user_id")
    def validate_non_empty(cls, value: str, field):
        if not str(value).strip():
            raise ValueError(f"{field.field_name} cannot be empty")
        return value

    @classmethod
    def from_jsonapi(cls, acc: "ResourceObject", user: Optional["UserDTO"] = None) -> "AccountDTO":
        return cls(
            id=int(acc.id),
            platform=acc.platform,
            social_id=acc.social_id,
            chat_id=acc.chat_id,
            extra_data=acc.extra_data,
            user_id=str(acc.user._resource_identifier.id),
            user=user
        )

class AuthResponseDTO(AccountDTO):
    created: bool = False
    nonce_status: Optional[str] = None
