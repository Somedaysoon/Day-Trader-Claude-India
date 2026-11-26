"""common/in_market.py: Indian time, the NSE calendar and the dated session profiles."""

import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "common"))
import in_market as mk  # noqa: E402

D = datetime.date
DT = datetime.datetime
T = datetime.time


class IndianTime(unittest.TestCase):
    def test_offset_is_fixed_530(self):
        self.assertEqual(mk.to_ist(DT(2026, 11, 23, 3, 45)), DT(2026, 11, 23, 9, 15))
        self.assertEqual(mk.ist_to_utc(DT(2026, 11, 23, 9, 15)), DT(2026, 11, 23, 3, 45))
        self.assertEqual(mk.ts_to_ist(0), DT(1970, 1, 1, 5, 30))

    def test_no_daylight_saving_anywhere_in_the_year(self):
        for m in (1, 4, 7, 10):
            u = DT(2026, m, 15, 12, 0)
            self.assertEqual(mk.to_ist(u) - u, datetime.timedelta(hours=5, minutes=30))

    def test_parse_now_reads_plain_times_as_ist(self):
        self.assertEqual(mk.parse_now("2026-11-23 09:15"), DT(2026, 11, 23, 3, 45))
        self.assertEqual(mk.parse_now("2026-11-23T03:45:00Z"), DT(2026, 11, 23, 3, 45))
        self.assertEqual(mk.parse_now("2026-11-23T09:15:00+05:30"), DT(2026, 11, 23, 3, 45))

    def test_parse_now_rejects_junk_with_the_callers_error(self):
        class Boom(Exception):
            pass
        with self.assertRaises(Boom) as cm:
            mk.parse_now("tuesday-ish", error=Boom)
        self.assertIn("Indian time", str(cm.exception))

    def test_parse_hhmm(self):
        self.assertEqual(mk.parse_hhmm("9:30"), T(9, 30))
        for bad in ("25:00", "9:60", "ten", ""):
            with self.assertRaises(mk.MarketError):
                mk.parse_hhmm(bad)

    def test_labels(self):
        self.assertEqual(mk.clock_label(T(15, 15)), "3:15 PM")
        self.assertEqual(mk.clock_label(T(9, 5)), "9:05 AM")
        self.assertEqual(mk.day_label(D(2026, 11, 23)), "Mon 2026-11-23")


class Calendar(unittest.TestCase):
    def test_tomorrow_is_a_holiday_from_the_nse_circular(self):
        ok, why = mk.trading_day(D(2026, 11, 24))
        self.assertFalse(ok)
        self.assertIn("Guru Nanak", why)

    def test_christmas_and_republic_day(self):
        self.assertFalse(mk.trading_day(D(2026, 12, 25))[0])
        self.assertFalse(mk.trading_day(D(2026, 1, 26))[0])

    def test_all_nineteen_weekday_holidays_fall_on_weekdays(self):
        hol = mk.calendar()["holidays"]
        self.assertEqual(len(hol), 19)
        for iso in hol:
            self.assertLess(D.fromisoformat(iso).weekday(), 5, iso)

    def test_weekend_holidays_fall_on_weekends(self):
        for iso in mk.calendar()["weekend_holidays"]:
            self.assertGreaterEqual(D.fromisoformat(iso).weekday(), 5, iso)

    def test_ordinary_days_and_weekends(self):
        self.assertEqual(mk.trading_day(D(2026, 11, 23)), (True, ""))
        self.assertEqual(mk.trading_day(D(2026, 11, 28)), (False, "weekend"))

    def test_muhurat_sunday_is_closed_for_the_strategy(self):
        ok, why = mk.trading_day(D(2026, 11, 8))
        self.assertFalse(ok)
        self.assertIn("muhurat", why)

    def test_next_and_previous_trading_day_skip_the_holiday(self):
        self.assertEqual(mk.next_trading_day(D(2026, 11, 23)), D(2026, 11, 25))
        self.assertEqual(mk.previous_trading_day(D(2026, 11, 25)), D(2026, 11, 23))
        self.assertEqual(mk.next_trading_day(D(2026, 12, 24)), D(2026, 12, 28))

    def test_a_year_the_list_does_not_cover_is_flagged_not_guessed(self):
        self.assertTrue(mk.calendar_known(D(2026, 6, 1)))
        self.assertFalse(mk.calendar_known(D(2027, 1, 26)))
        self.assertEqual(mk.calendar_years(), (2026,))


class SessionProfiles(unittest.TestCase):
    def test_before_3_aug_everything_runs_to_1530(self):
        d = D(2026, 7, 31)
        self.assertEqual(mk.session(d, "cas"), (T(9, 15), T(15, 30)))
        self.assertEqual(mk.session(d, "other"), (T(9, 15), T(15, 30)))
        self.assertIsNone(mk.auction(d, "cas"))
        self.assertEqual(mk.session_bars(d), 75)

    def test_from_3_aug_auction_stocks_stop_at_1515(self):
        d = D(2026, 8, 3)
        self.assertEqual(mk.session(d, "cas"), (T(9, 15), T(15, 15)))
        self.assertEqual(mk.session(d, "other"), (T(9, 15), T(15, 30)))
        self.assertEqual(mk.auction(d, "cas"), (T(15, 15), T(15, 35)))
        self.assertEqual(mk.session_bars(d, "cas"), 72)
        self.assertEqual(mk.session_bars(d, "other"), 75)

    def test_unknown_group_takes_the_earlier_end(self):
        d = D(2026, 11, 23)
        self.assertEqual(mk.session(d), (T(9, 15), T(15, 15)))
        self.assertEqual(mk.session(d, "any"), mk.session(d, "cas"))

    def test_last_day_of_the_old_rule_and_first_day_of_the_new(self):
        self.assertEqual(mk.session(D(2026, 12, 4), "cas")[1], T(15, 15))
        self.assertEqual(mk.session(D(2026, 12, 7), "cas")[1], T(15, 30))
        self.assertEqual(mk.auction(D(2026, 12, 7), "cas"), (T(15, 30), T(15, 40)))
        self.assertEqual(mk.session_bars(D(2026, 12, 7)), 75)

    def test_closed_days_have_no_session(self):
        self.assertIsNone(mk.session(D(2026, 11, 24)))
        self.assertIsNone(mk.session(D(2026, 11, 28)))
        self.assertEqual(mk.session_bars(D(2026, 11, 24)), 0)
        self.assertEqual(mk.bar_slots(D(2026, 11, 24)), [])

    def test_bad_group_is_an_error(self):
        with self.assertRaises(mk.MarketError):
            mk.session(D(2026, 11, 23), "nifty")

    def test_derived_times_follow_the_profile(self):
        d = D(2026, 11, 23)          # auction-group end 15:15
        self.assertEqual(mk.opening_range_end(d), T(9, 30))
        self.assertEqual(mk.no_entries_after(d), T(14, 45))
        self.assertEqual(mk.flat_by(d), T(15, 5))
        d = D(2026, 12, 7)           # end 15:30
        self.assertEqual(mk.no_entries_after(d), T(15, 0))
        self.assertEqual(mk.flat_by(d), T(15, 20))

    def test_bar_slots_match_the_bar_count_and_end_on_the_last_full_bar(self):
        s = mk.bar_slots(D(2026, 11, 23))
        self.assertEqual((len(s), s[0], s[-1]), (72, T(9, 15), T(15, 10)))
        s = mk.bar_slots(D(2026, 12, 7))
        self.assertEqual((len(s), s[0], s[-1]), (75, T(9, 15), T(15, 25)))

    def test_every_profile_has_both_groups_and_orders_by_date(self):
        ps = mk.sessions_data()["profiles"]
        self.assertEqual([p["effective_from"] for p in ps], sorted(p["effective_from"] for p in ps))
        for p in ps:
            self.assertEqual(sorted(p["continuous"]), ["cas", "other"])


class MarketState(unittest.TestCase):
    def at(self, text, group="any"):
        return mk.state(DT.fromisoformat(text), group)[0]

    def test_a_normal_day_from_pre_open_to_the_close(self):
        self.assertEqual(self.at("2026-11-23 08:59"), "closed")
        self.assertEqual(self.at("2026-11-23 09:05"), "pre-open")
        self.assertEqual(self.at("2026-11-23 09:15"), "open")
        self.assertEqual(self.at("2026-11-23 15:14"), "open")
        self.assertEqual(self.at("2026-11-23 15:15"), "closing-auction")
        self.assertEqual(self.at("2026-11-23 15:40"), "closed")

    def test_other_stocks_keep_trading_until_1530(self):
        self.assertEqual(self.at("2026-11-23 15:20", "other"), "open")
        self.assertEqual(self.at("2026-11-23 15:30", "other"), "closed")

    def test_holiday_is_closed_with_the_reason(self):
        st, why = mk.state(DT(2026, 11, 24, 10, 0))
        self.assertEqual(st, "closed")
        self.assertIn("holiday", why)

    def test_next_open_skips_the_holiday(self):
        self.assertEqual(mk.next_open(DT(2026, 11, 23, 16, 0)), DT(2026, 11, 25, 9, 15))
        self.assertEqual(mk.next_open(DT(2026, 11, 23, 7, 0)), DT(2026, 11, 23, 9, 15))
        self.assertEqual(mk.next_open(DT(2026, 11, 27, 16, 0)), DT(2026, 11, 30, 9, 15))


if __name__ == "__main__":
    unittest.main()
