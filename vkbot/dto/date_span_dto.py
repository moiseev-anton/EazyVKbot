from datetime import date
from typing import Union

from pydantic import BaseModel, field_validator, model_validator


class DateSpanDTO(BaseModel):
    start: date
    end: date

    @property
    def start_str(self) -> str:
        """Возвращает дату начала диапазона в ISO-формате ('YYYY-MM-DD')."""
        return self.start.isoformat()

    @property
    def end_str(self) -> str:
        """Возвращает дату конца диапазона в ISO-формате ('YYYY-MM-DD')."""
        return self.end.isoformat()

    @field_validator("start", "end", mode="before")
    @classmethod
    def parse_date(cls, value: Union[str, date]) -> date:
        """Позволяет передавать дату как строку ('YYYY-MM-DD') или объект date."""
        if isinstance(value, date):
            return value
        try:
            return date.fromisoformat(value)
        except Exception:
            raise ValueError(f"Invalid date format: {value}. Expected 'YYYY-MM-DD' or date object.")

    @model_validator(mode="after")
    def validate_range(self):
        """Проверка корректности диапазона: начало не позже конца."""
        if self.start > self.end:
            raise ValueError("date_from cannot be later than date_to.")
        return self
