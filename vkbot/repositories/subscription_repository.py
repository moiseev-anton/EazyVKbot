import logging
from typing import List, Optional

from jsonapi_client import Filter, Inclusion
from jsonapi_client.document import Document

from vkbot.context import set_hmac
from vkbot.dto import GroupDTO, SubscriptionDTO, TeacherDTO
from vkbot.dto.base_dto import SubscriptableDTO
from vkbot.repositories.base_repository import JsonApiBaseRepository
from vkbot.repositories.exceptions import ApiError

logger = logging.getLogger(__name__)


class JsonApiSubscriptionRepository(JsonApiBaseRepository):
    resource_name = "subscriptions"

    related_object_map = {
        "teacher": TeacherDTO,
        "group": GroupDTO,
    }

    async def _map_document_to_dtos(
            self,
            document: Document,
            rel_names: Optional[tuple[str, ...]] = None
    ) -> List[SubscriptionDTO]:
        """
        Преобразует ресурсы из JSON:API документа в список DTO подписок.

        Для каждого ресурса документа:
          1. Определяет связанный объект по имени отношения (rel_name).
          2. Загружает ресурс через `await rel.fetch()`.
          3. Преобразует ресурс в соответствующий DTO (TeacherDTO или GroupDTO).
          4. Создает SubscriptionDTO, объединяющий подписку и объект подписки.
          5. Добавляет результат в итоговый список.
        """
        rel_names = rel_names or tuple(self.related_object_map.keys())
        subscriptions = []

        for sub in document.resources:
            for rel_name in rel_names:
                rel = getattr(sub, rel_name, None)
                if rel is not None:
                    await rel.fetch()
                    SchemaDTO = self.related_object_map[rel_name]
                    obj = SchemaDTO.from_jsonapi(rel.resource)
                    subscriptions.append(SubscriptionDTO.from_jsonapi(sub, obj))
                    break

        return subscriptions

    async def get_user_subscriptions(self) -> List[SubscriptionDTO]:
        """ Получает все подписки текущего пользователя. (текущий пользователь по контексту) """
        try:
            related_names = tuple(self.related_object_map.keys())

            with set_hmac(True):
                document: Document = await self.api_client.get(
                    self.resource_name,
                    Inclusion(*related_names)
                )

                return await self._map_document_to_dtos(document, related_names)
        except Exception as e:
            raise ApiError(f"Failed to getting user subscriptions: {str(e)}")

    async def get_subscription_by_target(self, target_obj: SubscriptableDTO) -> Optional[SubscriptionDTO]:
        """
        Получает подписку пользователя на конкретный объект (группу или учителя).

        Замечания:
            - Предполагается, что пользователь может иметь не более одной подписки на конкретный объект.
            - Метод возвращает либо единственный SubscriptionDTO, либо None, если подписки нет.
        """
        try:
            rel_name = target_obj.relation_name
            obj_id = target_obj.id

            with set_hmac(True):
                document: Document = await self.api_client.get(
                    self.resource_name,
                    Inclusion(rel_name) + Filter(**{rel_name: obj_id})
                )

                subscriptions = await self._map_document_to_dtos(document, (rel_name,))
                return subscriptions[0] if subscriptions else None

        except Exception as e:
            raise ApiError(f"Failed to getting subscription by Subscriptable object: {str(e)}")

    async def count_user_subscriptions(self) -> int:
        """ Получает количество подписок пользователя """
        with set_hmac(True):
            document: Document = await self.api_client.get(self.resource_name)
            return len(document.resources)

    async def create(self, target_obj: SubscriptableDTO) -> SubscriptionDTO:
        sub_type = target_obj.subscription_resource_type
        rel_name = target_obj.relation_name
        rel_id = target_obj.id

        with set_hmac(True):
            new_subscription = self.api_client.create(sub_type, {rel_name: str(rel_id)})
            await new_subscription.commit()

        return SubscriptionDTO.from_jsonapi(new_subscription, target_obj)

    async def delete(self, sub_id: int | str) -> None:
        """ Удаляет подписку пользователя по её ID. """
        try:
            with set_hmac(True):
                document = await self.api_client.get(self.resource_name, sub_id)
                sub = document.resource
                sub.delete()
                await sub.commit()
        except Exception as e:
            raise ApiError(f"Failed to delete subscription [ID={sub_id}]: {str(e)}")
