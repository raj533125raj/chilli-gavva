"""R23 schedule checks with fixed clocks only."""
import datetime
import unittest

import publish_schedule as schedule


class PublishScheduleTests(unittest.TestCase):
    def test_each_day_has_the_required_repeatable_slots_in_ist_windows(self):
        for day in (datetime.date(2026, 10, 20), datetime.date(2026, 10, 21), datetime.date(2026, 10, 22)):
            first = schedule.daily_slots(day)
            self.assertEqual(first, schedule.daily_slots(day))
            self.assertEqual(len(first["pinterest"]), 3)
            self.assertEqual(len(first["telegram"]), 2)
            for slot, window in zip(first["pinterest"], schedule.PINTEREST_WINDOWS):
                self.assertEqual(slot.tzinfo, schedule.IST)
                self.assertGreaterEqual((slot.hour, slot.minute), window[:2])
                self.assertLess((slot.hour, slot.minute), window[2:])
            for slot, window in zip(first["telegram"], schedule.TELEGRAM_WINDOWS):
                self.assertGreaterEqual((slot.hour, slot.minute), window[:2])
                self.assertLess((slot.hour, slot.minute), window[2:])

    def test_only_the_chosen_window_minute_is_due(self):
        slot = schedule.daily_slots(datetime.date(2026, 10, 22))["pinterest"][0]
        self.assertTrue(schedule.due("pinterest", slot))
        self.assertFalse(schedule.due("pinterest", slot + datetime.timedelta(minutes=1)))
        self.assertTrue(schedule.due("gumroad", slot))

    def test_rest_and_no_commit_windows_block_every_type(self):
        for local in (datetime.datetime(2026, 10, 22, 6, 3, tzinfo=schedule.IST),
                      datetime.datetime(2026, 10, 22, 2, 0, tzinfo=schedule.IST)):
            self.assertTrue(schedule.blocked(local))
            for publish_type in ("gumroad", "pinterest", "telegram"):
                self.assertFalse(schedule.due(publish_type, local))


if __name__ == "__main__":
    unittest.main()
