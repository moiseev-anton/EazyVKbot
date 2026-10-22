from typing import Hashable, Any, Union

from cachetools import TTLCache
import time

from vkbot.context import get_context_prefix


class PrefixedKey:
    __slots__ = ("key", "prefixed")

    def __init__(self, key, prefixed=False):
        if isinstance(key, PrefixedKey):
            self.key = key.key
            self.prefixed = key.prefixed or prefixed
        else:
            self.key = key
            self.prefixed = prefixed

    def with_prefix(self):
        if self.prefixed:
            return self
        return PrefixedKey(f"{get_context_prefix()}:{self.key}", True)

    def __hash__(self):
        return hash(self.key)

    def __eq__(self, other):
        if isinstance(other, PrefixedKey):
            return self.key == other.key
        return self.key == other

    def __repr__(self):
        return f"<PrefixedKey {self.key} prefixed={self.prefixed}>"

    def __str__(self):
        return str(self.key)


class ContextualCache(TTLCache):
    def __getitem__(self, key):
        return super().__getitem__(PrefixedKey(key).with_prefix())

    def __setitem__(self, key, value):
        return super().__setitem__(PrefixedKey(key).with_prefix(), value)

    def __delitem__(self, key):
        return super().__delitem__(PrefixedKey(key).with_prefix())

    def __contains__(self,  key):
        return super().__contains__(PrefixedKey(key).with_prefix())

    def get(self, key, default=None):
        return super().get(PrefixedKey(key).with_prefix(), default)

    def pop(self, key, default=None):
        return super().pop(PrefixedKey(key).with_prefix(), default)


# Пример использования
if __name__ == "__main__":
    # Создаём кэш с TTL 5 секунд и максимальным размером 10
    cache = ContextualCache(maxsize=10, ttl=3)

    # Тестируем как словарь
    cache['a'] = 1
    cache['b'] = 2

    print(cache['a'])  # Вывод: 1
    print(cache.get('b', 0))  # Вывод: 2
    print(list(cache.values()))  # Вывод: [1, 2]
    print("Вызов values")

    # Ждём истечения TTL
    time.sleep(3.5)
    print(cache.get('a', 0))  # Вывод: 0 (ключ истёк)
    print(list(cache.values()))  # Вывод: [] (все ключи истекли)

    # Удаление и очистка
    cache['c'] = 3
    del cache['c']
    cache['d'] = 4
    cache.clear()
    print(cache.get('d', None))  # Вывод: None (кэш очищен)
