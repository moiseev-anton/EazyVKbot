from jsonapi_client.resourceobject import ResourceObject
from pydantic import BaseModel

from vkbot.dto.base_dto import SubscriptableDTO
from vkbot.dto.group_dto import GroupDTO
from vkbot.dto.teacher_dto import TeacherDTO


class SubscriptionDTO(BaseModel):
    id: int
    user_id: str
    object: SubscriptableDTO

    class Config:
        frozen = True

    @property
    def is_group(self) -> bool:
        return isinstance(self.object, GroupDTO)

    @property
    def is_teacher(self) -> bool:
        return isinstance(self.object, TeacherDTO)

    @property
    def display_name(self) -> str:
        return self.object.display_name

    @property
    def button_name(self) -> str:
        return self.object.button_name

    @property
    def endpoint(self):
        return self.object.endpoint

    @classmethod
    def from_jsonapi(cls, sub: 'ResourceObject', object: SubscriptableDTO) -> "SubscriptionDTO":
        return cls(
            id=sub.id,
            user_id=sub.user._resource_identifier.id,
            object=object
        )
