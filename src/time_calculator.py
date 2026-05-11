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

    def calculate_transition_time_with_pause(
        self,
        history: List[Dict],
        start_statuses: List[str],
        end_statuses: List[str],
        paused_statuses: List[str],
    ) -> Optional[float]:
        """Calculate business hours between status transitions, excluding periods in paused statuses.

        This method tracks multiple active/paused cycles and sums up only the active time.

        Args:
            history: List of history entries with timestamp and status
            start_statuses: List of statuses that start/resume the timer
            end_statuses: List of statuses that stop the timer
            paused_statuses: List of statuses that pause the timer

        Returns:
            Hours elapsed (business hours, excluding paused periods) or None
        """
        if not history:
            return None

        # CRITICAL: Ensure history is sorted by timestamp
        # The algorithm assumes chronological order for correct state tracking
        issue_key = history[0].get("key", "unknown")
        is_sorted = all(history[i]["timestamp"] <= history[i + 1]["timestamp"] for i in range(len(history) - 1))

        if not is_sorted:
            logger.warning(f"History for {issue_key} was not sorted. Re-sorting {len(history)} entries.")
            history = sorted(history, key=lambda x: x["timestamp"])
        else:
            logger.debug(f"History for {issue_key} is properly sorted with {len(history)} entries")

        # Remove exact duplicates (same timestamp + from + to)
        seen = set()
        unique_history = []
        duplicate_count = 0

        for entry in history:
            # Create key from timestamp + from + to to identify exact duplicates
            key = (entry['timestamp'], entry['from'], entry['to'])
            if key not in seen:
                seen.add(key)
                unique_history.append(entry)
            else:
                duplicate_count += 1
                logger.debug(f"  Duplicate entry removed: {entry['timestamp']} {entry['from']} → {entry['to']}")

        if duplicate_count > 0:
            logger.warning(f"Removed {duplicate_count} duplicate entries from {issue_key} (kept {len(unique_history)} unique)")

        history = unique_history

        # Debug: Log all history entries for problematic tickets
        if logger.level <= logging.DEBUG:
            for i, entry in enumerate(history):
                logger.debug(f"  Entry {i}: {entry['timestamp']} {entry['from']} → {entry['to']}")

        # Normalize status lists for comparison
        start_statuses_lower = [s.lower() for s in start_statuses]
        end_statuses_lower = [s.lower() for s in end_statuses]
        paused_statuses_lower = [s.lower() for s in paused_statuses]

        active_periods = []
        currently_active = False
        current_period_start = None

        for entry in history:
            status = entry["to"].lower()
            timestamp = entry["timestamp"]

            if entry["key"] == "POSKDS-3392":
                logger.info(f"Processing entry: DATE {entry['timestamp']} FROM {entry['from']} TO {status} ACTIVE {currently_active}")
            # Entering active status (start or resume)
            if status in start_statuses_lower and not currently_active:
                currently_active = True
                current_period_start = timestamp
                logger.debug(f"Start/resume timer at {current_period_start} when entering '{entry['to']}'")
                if entry["key"] == "POSKDS-3392":
                    logger.info(f"Start/resume timer at {current_period_start} when entering '{entry['to']}'")

            # Exiting to end status (stop)
            elif status in end_statuses_lower and currently_active:
                if current_period_start:
                    active_periods.append((current_period_start, timestamp))
                    logger.debug(f"Stop timer at {timestamp} when entering '{entry['to']}'")
                currently_active = False
                current_period_start = None
                if entry["key"] == "POSKDS-3392":
                    logger.info(f"Stop timer at {timestamp} when entering '{entry['to']}'")
                break  # Reached final status, stop processing

            # Entering paused status
            elif status in paused_statuses_lower and currently_active:
                if current_period_start:
                    active_periods.append((current_period_start, timestamp))
                    logger.debug(f"Pause timer at {timestamp} when entering '{entry['to']}'")
                if entry["key"] == "POSKDS-3392":
                    logger.info(f"Pause timer at {timestamp} when entering '{entry['to']}'")
                
                currently_active = False
                current_period_start = None

        # If still active at end of history (ticket currently in active status)
        if currently_active and current_period_start:
            logger.debug(f"Timer still active at end of history for status '{history[-1]['to']}'")
            # Don't count incomplete period

        # Calculate total business hours across all active periods
        total_hours = 0.0
        for period_start, period_end in active_periods:
            period_hours = self.calculate_business_hours(period_start, period_end)
            total_hours += period_hours
            logger.debug(f"Active period {period_start} to {period_end}: {period_hours}h")

        return round(total_hours, 2) if total_hours > 0 else None

