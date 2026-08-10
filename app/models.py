from dataclasses import dataclass
from typing import Optional

@dataclass
class Employee:
    full_name: str
    email: str
    status: str
    telegram_id: Optional[int] = None
    row_index: int = 0  # строка в листе (для обновлений)
