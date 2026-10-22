import re


# Функции для Monkey-patch пакета jsonapi_client:
# По умолчанию библиотека превращает поля с подчеркиваниями (snake_case) в JSON-ключи с дефисами (kebab-case).
# Спецификация JSON:API предписывает основной формат camelCase
# Так как DRF JSON API настроен на camelCase (shortTitle), переопределяем функции сериализации,
# чтобы атрибуты корректно отображались и были доступны как обычные свойства: faculty.short_title.

def camelize_attribute_name(name: str) -> str:
    """
    Преобразует snake_case строку в camelCase.
    Используется для преобразования python -> JSON:API
    """
    name = name.replace('__', '.')
    parts = name.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def decamelize_attribute_name(name):
    """
    Преобразует camelCase строку в snake_case.
    Используется для преобразования JSON:API -> python
    """
    name = name.replace('.', '__')
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
