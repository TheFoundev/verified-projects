"""The tests of the schedule of the week and the state of a check."""
from datetime import datetime, timedelta

from django.test import TestCase
from django.utils import timezone

from core.models import Check
from core.state import (
    CronError,
    check_deadline_exceeded,
    compute_next_scheduled_at,
    next_occurrence,
    parse_cron,
    should_alert_on_transition,
    transition_check,
)


class CronSpecTests(TestCase):
    def test_a_schedule_of_every_day(self):
        days, hour, minute = parse_cron("mon-sun,09:30")
        self.assertEqual(days, [0, 1, 2, 3, 4, 5, 6])
        self.assertEqual(hour, 9)
        self.assertEqual(minute, 30)

    def test_a_star_stands_for_every_day(self):
        days, hour, minute = parse_cron("*,12:00")
        self.assertEqual(days, [0, 1, 2, 3, 4, 5, 6])
        self.assertEqual(hour, 12)

    def test_a_single_day(self):
        days, hour, minute = parse_cron("fri,07:15")
        self.assertEqual(days, [4])
        self.assertEqual((hour, minute), (7, 15))

    def test_a_few_days(self):
        days, _, _ = parse_cron("mon,wed,fri,08:00")
        self.assertEqual(days, [0, 2, 4])

    def test_a_schedule_that_is_not_right_is_refused(self):
        for spec in ("", "nope", "mon-sun,25:00", "mon-sun,09:70", "funday,09:00", "sun-mon,09:00"):
            with self.subTest(spec=spec):
                with self.assertRaises(CronError):
                    parse_cron(spec)


class NextOccurrenceTests(TestCase):
    def test_the_next_moment_of_a_schedule(self):
        # A Monday at 08:00.
        start = datetime(2026, 4, 20, 7, 0, tzinfo=timezone.get_current_timezone())
        moment = next_occurrence("mon,08:00", start)
        self.assertEqual(moment.hour, 8)
        self.assertEqual(moment.minute, 0)
        self.assertEqual(moment.weekday(), 0)
        self.assertGreater(moment, start)

    def test_the_next_moment_is_the_next_day_of_the_schedule(self):
        # A Wednesday at 08:00.
        start = datetime(2026, 4, 20, 7, 0, tzinfo=timezone.get_current_timezone())
        moment = next_occurrence("wed,08:00", start)
        self.assertEqual(moment.weekday(), 2)
        self.assertEqual(moment.day, 22)

    def test_a_schedule_of_every_day_comes_round_the_next_day(self):
        start = datetime(2026, 4, 20, 9, 0, tzinfo=timezone.get_current_timezone())
        moment = next_occurrence("*,08:00", start)
        self.assertEqual(moment.day, 21)


class CheckStateTests(TestCase):
    def test_the_next_moment_of_an_interval(self):
        check = Check(name="every minute", schedule_type="interval", interval_seconds=60)
        check.last_ping = timezone.now()
        following = compute_next_scheduled_at(check)
        self.assertAlmostEqual(
            (following - check.last_ping).total_seconds(), 60, delta=1
        )

    def test_the_next_moment_of_a_schedule_of_the_week(self):
        check = Check(name="every monday", schedule_type="cron", cron_spec="mon,08:00")
        check.last_ping = timezone.now()
        following = compute_next_scheduled_at(check)
        self.assertIsNotNone(following)
        self.assertGreater(following, check.last_ping)

    def test_a_ping_that_is_late(self):
        check = Check(name="late", schedule_type="interval", interval_seconds=60)
        check.last_ping = timezone.now() - timedelta(seconds=200)
        check.grace_period_seconds = 60
        check.timeout_seconds = 600
        self.assertEqual(check_deadline_exceeded(check), "late")

    def test_a_ping_that_is_way_late(self):
        check = Check(name="down", schedule_type="interval", interval_seconds=60)
        check.last_ping = timezone.now() - timedelta(seconds=1000)
        check.grace_period_seconds = 60
        check.timeout_seconds = 600
        self.assertEqual(check_deadline_exceeded(check), "down")

    def test_a_ping_that_is_in_time(self):
        check = Check(name="up", schedule_type="interval", interval_seconds=600)
        check.last_ping = timezone.now()
        self.assertIsNone(check_deadline_exceeded(check))

    def test_the_alerts_are_only_sent_on_a_change(self):
        self.assertTrue(should_alert_on_transition("up", "late"))
        self.assertTrue(should_alert_on_transition("late", "down"))
        self.assertTrue(should_alert_on_transition("down", "up"))
        self.assertFalse(should_alert_on_transition("up", "up"))
        self.assertFalse(should_alert_on_transition("late", "late"))

    def test_the_state_of_a_check_is_kept(self):
        check = Check.objects.create(name="a check", schedule_type="interval", interval_seconds=60)
        old, new = transition_check(check, "up")
        self.assertEqual(old, "new")
        self.assertEqual(new, "up")
        check.refresh_from_db()
        self.assertEqual(check.status, "up")
