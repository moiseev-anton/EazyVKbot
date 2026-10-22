from collections import defaultdict

from jsonapi_client.resourceobject import ResourceObject

from vkbot.api_client import AsyncClientSession


class JsonApiBaseRepository:
    def __init__(self, api_client: AsyncClientSession):
        self.api_client = api_client

    @staticmethod
    def _separate_included_resources(document):
        if not getattr(document, "included", None):
            return {}

        grouped: dict[str, dict[str, ResourceObject]] = defaultdict(dict)
        for resource in document.included:
            grouped[resource.type][resource.id] = resource

        return dict(grouped)
