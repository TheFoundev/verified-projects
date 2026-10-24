"""The schedule of a check and the state it is in.

A check runs on an interval or on a schedule of the week. The schedule of
the week is written as `mon-sun,HH:MM`: the days it runs on and the time of
the day, and `*` stands for every day.
"""
import re
from datetime import datetime, timedelta

from django.utils import timezone

#: The days of the week, the way they are written in a schedule.
WEEKDAYS = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}

#: The pattern of a schedule of the week.
CRON_PATTERN = re.compile(
    r"^\s*(?P<days>[a-z*,\-]+)\s*,\s*(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*$"
)


class CronError(ValueError):
    """The schedule of the week could not be read."""


def parse_cron(spec):
    """Read a schedule of the week.

    The answer is the days it runs on and the time of the day.
    """
    if not spec:
        raise CronError("the schedule is empty")

    match = CRON_PATTERN.match(str(spec))
    if not match:
        raise CronError(f"{spec!r} is not a schedule of the week")

    days = []
    for part in match.group("days").split(","):
        part = part.strip().lower()
        if part == "*":
            days = list(range(7))
            break
        if "-" in part:
            first, _, last = part.partition("-")
            if first not in WEEKDAYS or last not in WEEKDAYS:
                raise CronError(f"{part!r} is not a range of days")
            start = WEEKDAYS[first]
            end = WEEKDAYS[last]
            if end < start:
                raise CronError(f"{part!r} is not a range of days")
            days.extend(range(start, end + 1))
        else:
            if part not in WEEKDAYS:
                raise CronError(f"{part!r} is not a day")
            days.append(WEEKDAYS[part])

    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    if hour > 23 or minute > 59:
        raise CronError(f"{spec!r} has a time that does not exist")

    return sorted(set(days)), hour, minute


def next_occurrence(spec, after=None):
    """The next moment the schedule of the week comes round.

    The moment is the first day of the schedule that is after the moment
    that is given.
    """
    days, hour, minute = parse_cron(spec)
    after = after or timezone.now()

    # The clock of the day is in the timezone of the project.
    local = timezone.localtime(after) if timezone.is_aware(after) else after
    for offset in range(0, 8):
        day = local.date() + timedelta(days=offset)
        if day.weekday() not in days:
            continue
        moment = datetime(
            day.year, day.month, day.day, hour, minute, tzinfo=local.tzinfo
        )
        if moment > local:
            return moment
    raise CronError(f"the schedule {spec!r} never comes round")


def compute_next_scheduled_at(check):
    """The moment the check runs next."""
    if check.schedule_type == "cron" and check.cron_spec:
        return next_occurrence(check.cron_spec, check.last_ping or timezone.now())
    if check.schedule_type == "interval" and check.interval_seconds:
        base = check.last_ping or timezone.now()
        return base + timedelta(seconds=check.interval_seconds)
    if check.interval_seconds:
        base = check.last_ping or timezone.now()
        return base + timedelta(seconds=check.interval_seconds)
    return (check.last_ping or timezone.now()) + timedelta(seconds=300)


def check_deadline_exceeded(check):
    """Check if a ping is due and grace/timeout exceeded based on last_ping."""
    now = timezone.now()
    if not check.last_ping:
        # For new checks with no pings yet, use next_scheduled_at or created_at
        if check.next_scheduled_at and now > check.next_scheduled_at + timedelta(seconds=check.grace_period_seconds):
            return 'late'
        return None

    if check.schedule_type == 'cron' and check.cron_spec:
        try:
            expected_by = next_occurrence(check.cron_spec, check.last_ping)
        except CronError:
            expected_by = check.last_ping + timedelta(seconds=check.interval_seconds or 300)
    else:
        interval = check.interval_seconds or 300
        expected_by = check.last_ping + timedelta(seconds=interval)

    grace = check.grace_period_seconds or 60
    timeout = check.timeout_seconds or 600

    late_by = expected_by + timedelta(seconds=grace)
    down_by = expected_by + timedelta(seconds=timeout)

    if now > down_by:
        return 'down'
    elif now > late_by:
        return 'late'
    return None


def should_alert_on_transition(old_status, new_status):
    # Alert once per transition to late/down, and on recovery to up
    if old_status != new_status:
        if new_status in ('late', 'down'):
            return True
        if old_status in ('late', 'down') and new_status == 'up':
            return True
    return False


def transition_check(check, new_status):
    old_status = check.status
    check.status = new_status
    check.save(update_fields=['status', 'updated_at'])
    return old_status, new_status
