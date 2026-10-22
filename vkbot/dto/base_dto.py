from typing import Optional

from pydantic import BaseModel


class SubscriptableDTO(BaseModel):
    """Базовый класс для объектов, на которые можно подписываться."""
    id: int
    endpoint: Optional[str] = None

    @property
    def button_name(self) -> str:
        raise NotImplementedError

    @property
    def display_name(self) -> str:
        raise NotImplementedError

    @property
    def resource_type(self):
        return self.Config._resource_type

    @property
    def subscription_resource_type(self):
        return self.Config._subscription_resource_type

    @property
    def relation_name(self):
        return self.Config._relation_name