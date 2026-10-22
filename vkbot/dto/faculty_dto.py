from pydantic import BaseModel


class FacultyDTO(BaseModel):
    id: int
    title: str
    short_title: str

    class Config:
        frozen = True
        _resource_name = "faculties"

    @property
    def button_name(self):
        return self.short_title or 'n/a'

    @classmethod
    def from_jsonapi(cls, f: "ResourceObject") -> "FacultyDTO":
        return cls(
            id=int(f.id),
            title=f.title,
            short_title=f.short_title,
        )
