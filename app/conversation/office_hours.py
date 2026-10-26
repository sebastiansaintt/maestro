from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


class OfficeHoursPolicy:
    """Horario de oficina por tenant (PRD §4.8). Mismo horario todos los días, incluidos domingos y festivos."""

    def __init__(
        self,
        timezone: str = "America/Bogota",
        start_hour: int = 8,
        end_hour: int = 18,
    ) -> None:
        self._tz = ZoneInfo(timezone)
        self._start_hour = start_hour
        self._end_hour = end_hour
        self.start_hour = start_hour

    def is_off_hours(self, moment: datetime) -> bool:
        local = moment.astimezone(self._tz)
        return not (self._start_hour <= local.hour < self._end_hour)
