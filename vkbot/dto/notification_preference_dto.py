from hashlib import sha256

from pydantic import BaseModel, StrictBool


class NotificationPreferenceDTO(BaseModel):
    id: str
    social_account_id: str
    notification_type: str
    channel: str
    enabled: StrictBool
    updated_at: str

    @property
    def version(self) -> str:
        value = f"{self.id}:{self.social_account_id}:{self.enabled}:{self.updated_at}"
        return sha256(value.encode()).hexdigest()[:12]

    @classmethod
    def from_jsonapi(cls, resource):
        return cls(
            id=str(resource.id),
            social_account_id=str(resource.social_account._resource_identifier.id),
            notification_type=resource.notification_type,
            channel=resource.channel,
            enabled=resource.enabled,
            updated_at=resource.updated_at,
        )
