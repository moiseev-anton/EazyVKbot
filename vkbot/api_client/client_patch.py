#  Monkey-патч для jsonapi_client:
# По умолчанию библиотека превращает поля с подчеркиваниями (snake_case) в JSON-ключи с дефисами (kebab-case).
# Спецификация JSON:API предписывает основной формат camelCase
# Так как DRF JSON API настроен на camelCase (shortTitle), переопределяем функции сериализации,
# чтобы атрибуты корректно отображались и были доступны как обычные свойства: faculty.short_title.

import sys
import types

from .utils import camelize_attribute_name, decamelize_attribute_name


def patch_jsonapi_client(verbose: bool = True):
    """
    Патчит библиотеку jsonapi_client, заменяя snake_case функции сериализации
    на camelCase, соответствующие спецификации JSON:API.
    """

    def log(msg: str):
        if verbose:
            print(f"[jsonapi_patch] {msg}")

    log("Starting patch...")

    # 1️⃣ Проверяем, что библиотека загружена
    if "jsonapi_client.common" not in sys.modules:
        log("⚠️ jsonapi_client.common не загружен — выполняем импорт...")
        import jsonapi_client.common  # noqa

    # 2️⃣ Получаем модуль common
    common_mod = sys.modules["jsonapi_client.common"]

    # 3️⃣ Патчим функции в основном модуле
    log("Patching jsonapi_client.common")
    common_mod.jsonify_attribute_name = camelize_attribute_name
    common_mod.dejsonify_attribute_name = decamelize_attribute_name

    # 4️⃣ Проверяем все загруженные подмодули библиотеки
    replaced_count = 0
    for name, module in list(sys.modules.items()):
        if not isinstance(module, types.ModuleType):
            continue
        if not name.startswith("jsonapi_client"):
            continue

        # 5️⃣ Проверяем и заменяем ссылки, если они указывают на старые функции
        for func_name in ("jsonify_attribute_name", "dejsonify_attribute_name"):
            if hasattr(module, func_name):
                current_func = getattr(module, func_name)
                new_func = getattr(common_mod, func_name)
                if current_func is not new_func:
                    setattr(module, func_name, new_func)
                    replaced_count += 1
                    log(f"🔄 Replaced {name}.{func_name}")

    log(f"✅ Patch complete. Updated {replaced_count} references.")
