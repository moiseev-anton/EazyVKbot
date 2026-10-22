from typing import Optional

from vkbot.dto.base_dto import SubscriptableDTO
from vkbot.dto.faculty_dto import FacultyDTO


class GroupDTO(SubscriptableDTO):
    # id: int
    #endpoint: Optional[str] = None
    title: str
    grade: int
    faculty_id: int
    faculty: Optional[FacultyDTO] = None

    class Config:
        frozen = True
        _resource_type = "groups"
        _relation_name = "group"
        allowed_filters = {"subgroup"}
        _subscription_resource_type = "group-subscriptions"

    @property
    def display_name(self):
        return self.title or 'n/a'

    @property
    def button_name(self):
        return self.title or 'n/a'

    @classmethod
    def from_jsonapi(cls, g: "ResourceObject", faculty: Optional[FacultyDTO] = None) -> "GroupDTO":
        return cls(
            id=int(g.id),
            title=g.title,
            endpoint=getattr(g, "endpoint", None),
            grade=int(g.grade),
            faculty_id=int(g.faculty._resource_identifier.id),
            faculty=faculty
        )
