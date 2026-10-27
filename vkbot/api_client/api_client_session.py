from vkbot.api_client.contextual_prefixed_cache import ContextualCache
from vkbot.api_client.thunder_protection import thunder_protection

import copy
import hashlib
import hmac
import json
import logging
import time
from typing import Optional, Dict, List, Tuple, Any

import yarl
from jsonapi_client import Session
from jsonapi_client.common import HttpStatus, error_from_response, HttpMethod
from jsonapi_client.document import Document

from jsonapi_client.exceptions import DocumentError
from vkbot.api_client.exceptions import NotModifiedError
from vkbot.context import request_context

logger = logging.getLogger(__name__)


def safe_error_detail(response_data, request_data):
    """Keep validation errors readable without echoing submitted profile/nonce values."""
    values = []

    def collect(value):
        if isinstance(value, dict):
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)
        elif isinstance(value, str) and len(value) >= 4:
            values.append(value)

    collect(request_data)

    def redact(value):
        if isinstance(value, dict):
            return {key: ("[redacted]" if any(word in key.lower() for word in
                    ("token", "secret", "password", "authorization", "signature")) else redact(child))
                    for key, child in value.items()}
        if isinstance(value, list):
            return [redact(child) for child in value]
        if isinstance(value, str):
            for submitted in sorted(values, key=len, reverse=True):
                value = value.replace(submitted, "[redacted]")
        return value

    return json.dumps(redact(response_data), ensure_ascii=False)[:3000]


class AsyncClientSession(Session):
    def __init__(
        self,
        server_url: str,
        platform: str,
        hmac_secret: Optional[str],
        schema: dict = None,
        request_kwargs: dict = None,
        use_relationship_iterator: bool = False,
    ) -> None:
        request_kwargs = request_kwargs or {}

        # Устанавливаем X-Platform в заголовки, если он еще не установлен
        headers = request_kwargs.setdefault("headers", {})
        headers.setdefault("X-Platform", platform)

        super().__init__(
            server_url=server_url,
            enable_async=True,
            schema=schema,
            request_kwargs=request_kwargs,
            use_relationship_iterator=use_relationship_iterator,
        )

        self.resources_by_resource_identifier = ContextualCache(maxsize=10_000, ttl=600)
        self.resources_by_link = ContextualCache(maxsize=10_000, ttl=600)
        self.documents_by_link = ContextualCache(maxsize=10_000, ttl=600)

        self.hmac_secret = hmac_secret.encode("utf-8") if hmac_secret else None
        self.platform = platform

    def _url_for_resource(
        self, resource_type: str, resource_id: str = None, filter: "Modifier" = None
    ) -> str:
        """
        Формирует url по имени ресурса и id и/или фильтрам.
        Адаптация метода. Добавляет / после id для избежания редиректа на сервере.
        Применяется только для GET
        """
        url = f"{self.url_prefix}/{resource_type}/"  # добавляем / в конце
        if resource_id is not None:
            url = f"{url}{resource_id}/"  # добавляем / в конце
        if filter:
            url = filter.url_with_modifiers(url)
        return yarl.URL(url)

    @staticmethod
    def ensure_trailing_slash(url: str):
        """
        Добавляет trailing slash в конец пути перед query-строкой (или в конец URL),
        чтобы предотвратить 301-редирект со стороны сервера.

        Зачем это нужно:
        - Без слеша сервер часто делает 301-редирект на вариант со слешем
        - После редиректа URL меняется → HMAC-подпись становится невалидной

        Применяется только к мутирующим запросам (POST, PATCH, DELETE),
        так как для GET-методов слеш уже добавляется в _url_for_resource.

        URL для мутирующих запросов формируется внутри ResourceObject,
        поэтому проще добавить слеш постфактум, чем патчить весь класс.
        """
        if "?" in url:
            path, query = url.split("?", 1)
            return path.rstrip("/") + "/?" + query
        return url.rstrip("/") + "/"

    def _get_hmac_headers(
            self,
            method: str,
            url: str,
            social_id: str = "",
            body: Optional[bytes] = None,
    ) -> Dict[str, str]:
        if not self.hmac_secret or not social_id:
            return {}

        timestamp = str(int(time.time()))
        body_hash = hashlib.sha256(body or b"").hexdigest()

        u = yarl.URL(url)
        full_path = u.raw_path_qs

        message = f"{method.upper()}\n{full_path}\n{timestamp}\n{self.platform}\n{social_id}\n{body_hash}".encode("utf-8")
        signature = hmac.new(self.hmac_secret, message, hashlib.sha256).hexdigest()
        # logger.info(message)

        return {
            "X-Signature": signature,
            "X-Timestamp": timestamp,
        }

    def _build_authenticated_request_kwargs(
            self,
            method: str,
            url: str,
            body: Optional[bytes] = None,
    ) -> Dict[str, Any]:
        ctx = request_context.get({})
        social_id = ctx.get("user_id")
        use_hmac = ctx.get("hmac", False)

        # Пока deepcopy не даёт накладных расходов, используем его
        # для устойчивости при возможном расширении request_kwargs
        kwargs = copy.deepcopy(self._request_kwargs)
        headers = kwargs.setdefault("headers", {})

        if use_hmac and not social_id:
            raise RuntimeError("Authenticated API request requires user context")
        if social_id:
            headers["X-Social-ID"] = social_id
            if use_hmac:
                hmac_headers = self._get_hmac_headers(method, url, social_id, body)
                headers.update(hmac_headers)

        if method in (HttpMethod.POST, HttpMethod.PATCH):
            headers['Content-Type'] = 'application/vnd.api+json'
        headers.setdefault('Accept', 'application/vnd.api+json')

        return kwargs

    async def fetch_document_by_url_async(self, url: str) -> 'Document':
        """Fetch a Document from cache or server by URL, with ETag validation for cached document"""
        document = self.documents_by_link.get(url)
        try:
            return await self._ext_fetch_by_url_async(url)
        except NotModifiedError:
            return document

    @thunder_protection(prefix="_ext_fetch_by_url_async")
    async def _ext_fetch_by_url_async(self, url: str) -> 'Document':
        json_data, etag = await self._fetch_json_async(url)
        return self.read(json_data, url, etag=etag)

    def read(self, json_data: dict, url='', etag=None, no_cache=False) -> 'Document':
        """Read document from json_data dictionary instead of fetching it from the server."""
        from vkbot.api_client.document import CustomDocument
        doc = self.documents_by_link[url] = CustomDocument(self, json_data, url, etag=etag, no_cache=no_cache)
        return doc

    async def fetch_resource_by_resource_identifier_async(
                self,
                resource: 'Union[ResourceIdentifier, ResourceObject, ResourceTuple]',
                cache_only=False,
                force=False) -> 'Optional[ResourceObject]':
        """
        Internal use. Async version.

        Fetch resource from server by resource identifier.
        """
        type_, id_ = resource.type, resource.id
        new_res = not force and self.resources_by_resource_identifier.get((type_, id_))
        if new_res:
            return new_res
        elif cache_only:
            return None
        else:
            # Note: Document creation will add its resources to cache via .add_resources,
            # no need to do it manually here
            return (await self._ext_fetch_by_url_async(f"{resource.url}/")).resource

    async def _fetch_json_async(self, url: str) -> Tuple[dict, Optional[str]]:
        """
        Internal use. Async version.

        Fetch document raw json from server using aiohttp library.
        """
        self.assert_async()
        logger.info('Fetching document from url %s', url)

        request_kwargs = self._build_authenticated_request_kwargs("GET", url)
        if document := self.documents_by_link.get(url):
            if document_etag := document.etag:
                headers = request_kwargs.setdefault("headers", {})
                headers["If-None-Match"] = document_etag

        async with self._aiohttp_session.get(url, **request_kwargs) as response:
            if response.status == 304:
                raise NotModifiedError("Document not modified")

            response_content = await response.json(content_type='application/vnd.api+json')

            if response.status == HttpStatus.OK_200:
                new_etag = response.headers.get("ETag")
                return response_content, new_etag
            else:
                raise DocumentError(f'Error {response.status}: '
                                    f'{error_from_response(response_content)}',
                                    errors={'status_code': response.status},
                                    response=response)

    async def http_request_async(
        self,
        http_method: str,
        url: str,
        send_json: dict = None,
        expected_statuses: List[str] = None,
    ) -> Tuple[int, dict, str]:
        """Method to make PATCH/POST requests to server using aiohttp library."""

        self.assert_async()

        expected_statuses = expected_statuses or HttpStatus.ALL_OK
        content_type = "" if http_method == HttpMethod.DELETE else "application/vnd.api+json"
        url = self.ensure_trailing_slash(url)

        body_bytes = json.dumps(send_json, ensure_ascii=False).encode("utf-8") if send_json else b""

        request_kwargs = self._build_authenticated_request_kwargs(http_method, url, body_bytes)

        async with self._aiohttp_session.request(http_method, url, data=body_bytes, **request_kwargs) as response:
            response_json = {} if response.status == 204 else await response.json(content_type=None)

            if response.status not in expected_statuses:
                raise DocumentError(
                    f"Could not {http_method.upper()} "
                    f"({response.status}): "
                    f"{safe_error_detail(response_json, send_json)}",
                    errors={"status_code": response.status},
                    response=response,
                    json_data=send_json,
                )

            return (
                response.status,
                response_json or {},
                response.headers.get("Location"),
            )
