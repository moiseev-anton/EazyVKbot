import logging
from typing import List
from pathlib import Path


from jsonapi_client import Inclusion
from jsonapi_client.document import Document

from vkbot.api_client import AsyncClientSession
from vkbot.dto import GroupDTO
from vkbot.dto.faculty_dto import FacultyDTO

logger = logging.getLogger(__name__)


class JsonApiGroupRepository:
    resource_name = "groups"

    def __init__(self, api_client: AsyncClientSession, cache_file_path: str):
        self.api_client = api_client
        self.cache_file_path = Path(cache_file_path)

    async def get_group(self, group_id: str) -> GroupDTO:
        document: Document = await self.api_client.get(self.resource_name, group_id)
        group_res = document.resource
        return GroupDTO.from_jsonapi(group_res)

    async def get_groups(self) -> List[GroupDTO]:
        document: Document = await self.api_client.get(self.resource_name)
        return [GroupDTO.from_jsonapi(group_res) for group_res in document.resources]

    async def get_groups_with_faculties(self) -> List[GroupDTO]:
        document: Document = await self.api_client.get(self.resource_name, Inclusion("faculty"))

        faculties = {
            f.id: FacultyDTO.from_jsonapi(f)
            for f in document.included if f.type == "faculties"
        }

        return [
            GroupDTO.from_jsonapi(g, faculty=faculties.get(g.faculty._resource_identifier.id))
            for g in document.resources
        ]
