from contextvars import ContextVar
from typing import TypedDict
from contextlib import contextmanager


class RequestContext(TypedDict, total=False):
    user_id: str
    hmac: bool


request_context: ContextVar[RequestContext] = ContextVar("request_context", default={})


def get_context_prefix() -> str:
    """Определяет префикс ключа на основе ContextVar."""

    ctx = request_context.get({})
    prefix = f"{ctx.get('user_id', 'anonymous')}:" if ctx.get("hmac", False) else "public:"
    return prefix


@contextmanager
def set_hmac(flag: bool):
    ctx = request_context.get().copy()
    old_flag = ctx.get("hmac", False)
    ctx["hmac"] = flag
    token = request_context.set(ctx)
    try:
        yield
    finally:
        # восстанавливаем старый флаг
        ctx["hmac"] = old_flag
        request_context.reset(token)