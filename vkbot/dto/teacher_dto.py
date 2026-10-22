from typing import Optional

from vkbot.dto.base_dto import SubscriptableDTO


class TeacherDTO(SubscriptableDTO):
    # id: int
    # endpoint: Optional[str] = None
    full_name: str
    short_name: str

    class Config:
        frozen = True
        _resource_type = "teachers"
        _relation_name = "teacher"
        allowed_filters = set()
        _subscription_resource_type = "teacher-subscriptions"

    @property
    def display_name(self):
        return self.short_name

    @property
    def button_name(self):
        return self.short_name

    @classmethod
    def from_jsonapi(cls, t: "ResourceObject") -> "TeacherDTO":
        return cls(
            id=int(t.id),
            full_name=t.full_name,
            short_name=t.short_name,
            endpoint=getattr(t, "endpoint", None),
        )
