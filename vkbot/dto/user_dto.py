from typing import List, Optional, TYPE_CHECKING

from pydantic import BaseModel, field_validator

from vkbot.dto.subscription_dto import SubscriptionDTO

if TYPE_CHECKING:
    from .account_dto import AccountDTO
    from .subscription_dto import SubscriptionDTO


class UserDTO(BaseModel):
    id: str
    first_name: str
    last_name: Optional[str] = None
    username: str
    subscription_ids: List[int] = []
    account_ids: List[int] = []

    subscriptions: Optional[List["SubscriptionDTO"]] = None
    accounts: Optional[List["AccountDTO"]] = None

    class Config:
        _resource_name = "users"

    @property
    def name(self) -> str:
        parts = [self.first_name, self.last_name]
        full = " ".join(p.strip() for p in parts if p and p.strip())
        return full or "Anonymous"


    @field_validator("id", "username")
    def validate_non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Value cannot be empty")
        return value

    @classmethod
    def from_jsonapi(
            cls,
            user: "ResourceObject",
            subscriptions: Optional[List["SubscriptionDTO"]] = None,
            accounts: Optional[List["AccountDTO"]] = None,
    ) -> "UserDTO":
        return cls(
            id=str(user.id),
            first_name=user.first_name,
            last_name=user.last_name,
            username=user.username,
            subscription_ids=[sub.id for sub in user.subscriptions._resource_identifiers],
            account_ids=[acc.id for acc in user.accounts._resource_identifiers],

            subscriptions=subscriptions,
            accounts=accounts,
        )
