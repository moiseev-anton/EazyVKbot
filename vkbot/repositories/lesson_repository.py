import logging
from typing import Any

from jsonapi_client import Filter, Inclusion, Modifier
from jsonapi_client.document import Document

from vkbot.dto import DateSpanDTO, GroupDTO, LessonDTO, TeacherDTO
from vkbot.dto.base_dto import SubscriptableDTO
from vkbot.repositories.base_repository import JsonApiBaseRepository
from vkbot.repositories.exceptions import ApiError

logger = logging.getLogger(__name__)


class JsonApiLessonRepository(JsonApiBaseRepository):
    resource_name = "lessons"

    allowed_filters = {"subgroup", "group", "lesson", "date_from", "date_to"}

    included_types_map = {
        "teachers": TeacherDTO,
        "groups": GroupDTO,
    }

    def _get_or_create_dto(self, resource, cache: dict):
        key = (resource.type, resource.id)
        if key not in cache:
            DtoClass = self.included_types_map.get(resource.type)
            if not DtoClass:
                raise ValueError(f"Unsupported related resource type: {resource.type}")
            cache[key] = DtoClass.from_jsonapi(resource)
        return cache[key]

    async def get_lesson(self, lesson_id: str) -> LessonDTO:
        document: Document = await self.api_client.get(self.resource_name, lesson_id)
        lesson_res = document.resource
        return LessonDTO.from_jsonapi(lesson_res)

    async def get_lessons(self, obj: SubscriptableDTO, date_span: DateSpanDTO, **filters):
        modifiers = [
            Filter(**{obj.relation_name: obj.id}),
            Filter(date_from=date_span.start_str),
            Filter(date_to=date_span.end_str),
            Inclusion('teacher', 'group'),
        ]

        # Проверка дополнительных фильтров
        for k, v in filters.items():
            if k not in self.allowed_filters:
                raise ValueError(f"Filter '{k}' not allowed for {obj.__class__.__name__}")
            modifiers.append(Filter(**{k: v}))

        modifier = sum(modifiers, Modifier())

        try:
            document = await self.api_client.get(self.resource_name, modifier)

            lessons: list[LessonDTO] = []
            related_dto_cache: dict[tuple[str, str], Any] = {}

            for lesson in document.resources:
                await lesson.group.fetch()
                await lesson.teacher.fetch()

                group_dto = self._get_or_create_dto(lesson.group.resource, related_dto_cache)
                teacher_dto = self._get_or_create_dto(res, related_dto_cache) if (res := lesson.teacher.resource) else None

                lessons.append(LessonDTO.from_jsonapi(lesson, group_dto, teacher_dto))

            return lessons

        except Exception as e:
            raise ApiError(f"Failed to get lessons for {obj.__class__.__name__} [ID={obj.id}]: {e}")
