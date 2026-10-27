from collections import defaultdict
from urllib.parse import urljoin, urlsplit

from jsonapi_client.resourceobject import ResourceObject

from vkbot.api_client import AsyncClientSession
from vkbot.api_client.document import CustomDocument
from vkbot.context import set_hmac


class JsonApiBaseRepository:
    def __init__(self, api_client: AsyncClientSession):
        self.api_client = api_client

    async def _fresh_document(self, url):
        with set_hmac(True):
            _, payload, _ = await self.api_client.http_request_async("GET", url)
        return CustomDocument(self.api_client, payload, url, no_cache=True), payload

    async def _fresh_resources(self, resource_type):
        url = str(self.api_client._url_for_resource(resource_type))
        origin = urlsplit(url)
        visited = set()
        while url:
            target = urlsplit(url)
            if (target.scheme, target.netloc) != (origin.scheme, origin.netloc):
                raise ValueError("Cross-origin JSON:API pagination")
            if url in visited:
                raise ValueError("Cyclic JSON:API pagination")
            visited.add(url)
            document, payload = await self._fresh_document(url)
            for resource in document.resources:
                yield resource
            next_link = (payload.get("links") or {}).get("next")
            if isinstance(next_link, dict):
                next_link = next_link.get("href")
            url = urljoin(url, next_link) if next_link else None

    @staticmethod
    def _separate_included_resources(document):
        if not getattr(document, "included", None):
            return {}

        grouped: dict[str, dict[str, ResourceObject]] = defaultdict(dict)
        for resource in document.included:
            grouped[resource.type][resource.id] = resource

        return dict(grouped)
