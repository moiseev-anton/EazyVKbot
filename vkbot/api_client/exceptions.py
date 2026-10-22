from jsonapi_client.exceptions import JsonApiClientError


class NotModifiedError(JsonApiClientError):
    """Raised when server responds with HTTP 304 Not Modified"""
    pass
