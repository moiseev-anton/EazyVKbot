from jsonapi_client.document import Document


class CustomDocument(Document):
    def __init__(
        self,
        session: "AsyncClientSession",
        json_data: dict,
        url: str,
        etag: str = None,
        no_cache: bool = False,
    ) -> None:
        super().__init__(session, json_data, url, no_cache)
        self._etag = etag

    @property
    def etag(self) -> str:
        return self._etag
