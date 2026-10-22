import logging
from typing import List
from pathlib import Path

from jsonapi_client.document import Document

from vkbot.api_client import AsyncClientSession
from vkbot.dto import TeacherDTO

logger = logging.getLogger(__name__)


class JsonApiTeacherRepository:
    resource_name = "teachers"

    def __init__(self, api_client: AsyncClientSession, cache_file_path: str):
        self.api_client = api_client
        self.cache_file_path = Path(cache_file_path)

    async def get_teacher(self, teacher_id: str) -> TeacherDTO:
        document: Document = await self.api_client.get(self.resource_name, teacher_id)
        group_res = document.resource
        return TeacherDTO.from_jsonapi(group_res)

    async def get_teachers(self) -> List[TeacherDTO]:
        document: Document = await self.api_client.get(self.resource_name)
        return [TeacherDTO.from_jsonapi(teacher_res) for teacher_res in document.resources]
