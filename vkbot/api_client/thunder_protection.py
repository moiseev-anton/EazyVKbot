import asyncio
from functools import wraps, partial

from typing import Callable, TypeVar, ParamSpec
from vkbot.context import get_context_prefix

P = ParamSpec("P")  # Для параметров декорируемой функции
R = TypeVar("R")    # Для возвращаемого значения


def thunder_protection(prefix: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    tasks: dict[str, asyncio.Task] = {}

    def _decor(func: Callable[P, R]) -> Callable[P, R]:
        def done_callback(_key: str, _: asyncio.Task):
            tasks.pop(_key, None)

        @wraps(func)
        async def _wrapper(*args, **kwargs):
            # Формируем ключ строго: <context_prefix><prefix>:<arg1>:<arg2>:...:<k=v>
            parts = [str(a) for a in args]
            parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
            full_key = get_context_prefix() + prefix
            if parts:
                full_key += ":" + ":".join(parts)

            if full_key in tasks:
                return await tasks[full_key]

            task = asyncio.create_task(func(*args, **kwargs))
            tasks[full_key] = task
            task.add_done_callback(partial(done_callback, full_key))
            return await task

        return _wrapper

    return _decor
