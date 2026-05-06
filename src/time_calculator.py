"""Time calculation utilities for business hours and status transitions."""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pytz

logger = logging.getLogger(__name__)


class TimeCalculator:
    """Calculate elapsed time between status transitions."""

    def __init__(
        self,
        working_start: str = "08:00",
        working_end: str = "17:00",
        timezone: str = "Asia/Jakarta",
        weekends: Optional[List[str]] = None,
        holidays: Optional[List[Dict[str, str]]] = None,
    ):
        """Initialize time calculator.

        Args:
            working_start: Start of business hours (HH:MM format)
            working_end: End of business hours (HH:MM format)
            timezone: Timezone for business hours
            weekends: List of weekend day names
            holidays: List of holiday dicts with 'date' and 'name'
        """
        self.working_start = working_start
        self.working_end = working_end
        self.timezone = pytz.timezone(timezone)
        self.weekends = weekends or ["Saturday", "Sunday"]
        self.holidays = holidays or []

        # Parse holiday dates
        self.holiday_dates = set()
        for h in self.holidays:
            try:
                self.holiday_dates.add(datetime.strptime(h["date"], "%Y-%m-%d").date())
            except (ValueError, KeyError):
                logger.warning(f"Invalid holiday format: {h}")

    def calculate_transition_time(
        self,
        history: List[Dict],
        start_status: str = None,
        end_statuses: List[str] = None,
        from_status: str = None,
        to_status: str = None,
    ) -> Optional[float]:
        """Calculate hours between status transitions.

        Args:
            history: List of history entries with timestamp and status
            start_status: Status that starts the timer (when ticket ENTERS this status)
            end_statuses: List of statuses that stop the timer (when ticket ENTERS any)
            from_status: DEPRECATED - Status that starts the timer (when leaving)
            to_status: DEPRECATED - Status that stops the timer (when entering)

        Returns:
            Hours elapsed (business hours) or None if transition not found
        """
        # Support both old and new config formats
        if start_status and end_statuses:
            return self._calculate_by_status_entry(history, start_status, end_statuses)
        elif from_status and to_status:
            # Legacy format support
            return self._calculate_by_status_entry(history, from_status, [to_status])
        else:
            return None

    def _calculate_by_status_entry(
        self,
        history: List[Dict],
        start_status: str,
        end_statuses: List[str],
    ) -> Optional[float]:
        """Calculate time from entering start_status to entering any end_status.

        Args:
            history: List of history entries with timestamp and status
            start_status: Status that starts the timer (when ticket ENTERS this status)
            end_statuses: List of statuses that stop the timer (when ticket ENTERS any)

        Returns:
            Hours elapsed (business hours) or None if transition not found
        """
        start_time = None
        end_time = None

        for entry in history:
            # Find first time ticket ENTERS start_status
            if entry["to"].lower() == start_status.lower() and start_time is None:
                start_time = entry["timestamp"]
                logger.debug(f"Start timer at {start_time} when entering '{start_status}'")

            # Find first time AFTER start when ticket ENTERS any end_status
            if start_time and entry["to"].lower() in map(str.lower, end_statuses) and end_time is None:
                end_time = entry["timestamp"]
                logger.debug(f"Stop timer at {end_time} when entering '{entry['to']}'")
                break

        if start_time is None:
            logger.debug(f"Ticket never entered '{start_status}'")
            return None

        if end_time is None:
            logger.debug(f"Ticket never entered any of {end_statuses} after '{start_status}'")
            return None

        if end_time < start_time:
            logger.warning(f"Invalid timeline: {end_time} before {start_time}")
            return None

        return self.calculate_business_hours(start_time, end_time)

    def calculate_business_hours(self, start: datetime, end: datetime) -> float:
        """Calculate business hours between two timestamps.

        Args:
            start: Start datetime (timezone-aware)
            end: End datetime (timezone-aware)

        Returns:
            Number of business hours
        """
        # Convert to business timezone
        if start.tzinfo is None:
            start = pytz.utc.localize(start)
        if end.tzinfo is None:
            end = pytz.utc.localize(end)

        start = start.astimezone(self.timezone)
        end = end.astimezone(self.timezone)

        total_hours = 0.0
        current = start

        # Parse working hours
        start_hour, start_min = map(int, self.working_start.split(":"))
        end_hour, end_min = map(int, self.working_end.split(":"))

        while current < end:
            # Skip weekends
            if current.strftime("%A") in self.weekends:
                current = (current + timedelta(days=1)).replace(
                    hour=start_hour, minute=start_min, second=0, microsecond=0
                )
                continue

            # Skip holidays
            if current.date() in self.holiday_dates:
                current = (current + timedelta(days=1)).replace(
                    hour=start_hour, minute=start_min, second=0, microsecond=0
                )
                continue

            # Set start of workday
            work_start = current.replace(
                hour=start_hour, minute=start_min, second=0, microsecond=0
            )

            # Set end of workday
            work_end = current.replace(
                hour=end_hour, minute=end_min, second=0, microsecond=0
            )

            # Adjust if current is before work start
            if current < work_start:
                current = work_start

            # Calculate hours for this day
            day_end = min(end, work_end)
            day_hours = (day_end - current).total_seconds() / 3600

            if day_hours > 0:
                total_hours += day_hours

            # Move to next day
            current = (current + timedelta(days=1)).replace(
                hour=start_hour, minute=start_min, second=0, microsecond=0
            )

        return round(total_hours, 2)
