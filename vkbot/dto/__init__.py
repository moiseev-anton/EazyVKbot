from .account_dto import AccountDTO, AuthDTO, AuthResponseDTO
from .date_span_dto import DateSpanDTO
from .faculty_dto import FacultyDTO
from .group_dto import GroupDTO
from .lesson_dto import LessonDTO
from .subscription_dto import SubscriptionDTO
from .teacher_dto import TeacherDTO
from .user_dto import UserDTO

AccountDTO.model_rebuild()
AuthResponseDTO.model_rebuild()
UserDTO.model_rebuild()