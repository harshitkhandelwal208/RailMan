import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.services.recommendation_engine import recommend_sync, _recommend_with_trains


IST = timezone(timedelta(hours=5, minutes=30), name="IST")


class RecommendationEngineTests(unittest.TestCase):
    def test_returns_next_departure_for_andheri_to_dadar(self):
        rec = recommend_sync("Andheri", "Dadar", "14:33", "balanced")

        self.assertNotIn("error", rec)
        self.assertEqual(rec["best"]["departs"], "14:53")
        self.assertEqual(rec["best"]["wait_minutes"], 20)
        self.assertEqual(rec["best"]["departure_day_offset"], 0)

    def test_returns_next_departure_for_bandra_to_borivali(self):
        rec = recommend_sync("Bandra", "Borivali", "14:33", "balanced")

        self.assertNotIn("error", rec)
        self.assertEqual(rec["best"]["departs"], "14:44")
        self.assertEqual(rec["best"]["wait_minutes"], 11)

    def test_rolls_over_to_next_day_after_last_departure(self):
        rec = recommend_sync("Andheri", "Dadar", "23:55", "balanced")

        self.assertNotIn("error", rec)
        self.assertEqual(rec["best"]["departs"], "00:07")
        self.assertEqual(rec["best"]["wait_minutes"], 12)
        self.assertEqual(rec["best"]["departure_day_offset"], 1)

    def test_uses_mumbai_time_when_no_time_is_provided(self):
        mocked_now = datetime(2026, 4, 22, 14, 33, tzinfo=IST)

        with patch("app.services.recommendation_engine.get_service_now", return_value=mocked_now):
            rec = recommend_sync("Andheri", "Dadar")

        self.assertNotIn("error", rec)
        self.assertEqual(rec["meta"]["query_time"], "14:33")
        self.assertEqual(rec["meta"]["service_timezone"], "Asia/Kolkata")
        self.assertEqual(rec["best"]["departs"], "14:53")

    def test_least_crowded_preference_can_choose_later_train(self):
        trains = [
            {
                "id": "early_fast",
                "name": "Early Fast",
                "type": "fast",
                "direction": 1,
                "start_index": 20,
                "stop_indices": [20, 21],
                "departs_hour": 9,
                "departs_minute": 0,
            },
            {
                "id": "later_slow",
                "name": "Later Slow",
                "type": "slow",
                "direction": 1,
                "start_index": 20,
                "stop_indices": [20, 21],
                "departs_hour": 9,
                "departs_minute": 8,
            },
        ]
        mocked_now = datetime(2026, 4, 22, 8, 59, tzinfo=IST)

        crowd_lookup = {
            (9, 0, "fast"): ("Extreme", "#f00", 95),
            (9, 8, "slow"): ("Low", "#0f0", 5),
        }

        def fake_predict(hour, minute, zone=None, train_type=None, is_weekend=False):
            return crowd_lookup[(hour, minute, train_type)]

        with patch("app.services.recommendation_engine.get_service_now", return_value=mocked_now), \
             patch("app.services.recommendation_engine.predict_crowd", side_effect=fake_predict):
            rec = _recommend_with_trains(
                trains,
                "Borivali",
                "Dahisar",
                "08:59",
                "least_crowded",
            )

        self.assertEqual(rec["best"]["name"], "Later Slow")
        self.assertEqual(rec["best"]["wait_minutes"], 9)




    def test_balanced_preference_can_choose_faster_transfer_route(self):
        trains = [
            {
                "id": "slow_first_leg",
                "name": "Mahim–Bandra Slow",
                "type": "slow",
                "line": "western",
                "direction": 1,
                "start_index": 10,
                "stop_ids": ["MH", "BD"],
                "departs_hour": 9,
                "departs_minute": 0,
                "mins_per_hop": 4,
            },
            {
                "id": "slow_direct",
                "name": "Mahim–Borivali Slow",
                "type": "slow",
                "line": "western",
                "direction": 1,
                "start_index": 10,
                "stop_ids": ["MH", "BD", "KR", "SC", "VP", "AN", "JG", "GN", "MA", "KD", "BV"],
                "departs_hour": 9,
                "departs_minute": 50,
                "mins_per_hop": 6,
            },
            {
                "id": "fast_second_leg",
                "name": "Bandra–Borivali Fast",
                "type": "fast",
                "line": "western",
                "direction": 1,
                "start_index": 11,
                "stop_ids": ["BD", "KR", "SC", "VP", "AN", "JG", "GN", "MA", "KD", "BV"],
                "departs_hour": 9,
                "departs_minute": 10,
                "mins_per_hop": 2,
            },
        ]
        mocked_now = datetime(2026, 4, 22, 8, 55, tzinfo=IST)

        crowd = lambda *args, **kwargs: ("Low", "#0f0", 10)

        with patch("app.services.recommendation_engine.get_service_now", return_value=mocked_now), \
             patch("app.services.recommendation_engine.predict_crowd", side_effect=crowd):
            rec = _recommend_with_trains(
                trains,
                "Mahim",
                "Borivali",
                "08:55",
                "balanced",
            )

        self.assertNotIn("error", rec)
        self.assertEqual(rec["best"]["kind"], "transfer")
        self.assertEqual(rec["best"]["transfer_station"], "BD")

    def test_fastest_preference_can_switch_trains_on_same_line(self):
        trains = [
            {
                "id": "slow_to_bandra",
                "name": "Mahim–Bandra Slow",
                "type": "slow",
                "line": "western",
                "direction": 1,
                "start_index": 10,
                "stop_ids": ["MH", "BD"],
                "departs_hour": 9,
                "departs_minute": 0,
                "mins_per_hop": 4,
            },
            {
                "id": "direct_slow",
                "name": "Mahim–Borivali Slow",
                "type": "slow",
                "line": "western",
                "direction": 1,
                "start_index": 10,
                "stop_ids": ["MH", "BD", "KR", "SC", "VP", "AN", "JG", "GN", "MA", "KD", "BV"],
                "departs_hour": 9,
                "departs_minute": 50,
                "mins_per_hop": 6,
            },
            {
                "id": "fast_from_bandra",
                "name": "Bandra–Borivali Fast",
                "type": "fast",
                "line": "western",
                "direction": 1,
                "start_index": 11,
                "stop_ids": ["BD", "KR", "SC", "VP", "AN", "JG", "GN", "MA", "KD", "BV"],
                "departs_hour": 9,
                "departs_minute": 10,
                "mins_per_hop": 2,
            },
        ]
        mocked_now = datetime(2026, 4, 22, 8, 55, tzinfo=IST)

        crowd = lambda *args, **kwargs: ("Low", "#0f0", 10)

        with patch("app.services.recommendation_engine.get_service_now", return_value=mocked_now),              patch("app.services.recommendation_engine.predict_crowd", side_effect=crowd):
            rec = _recommend_with_trains(
                trains,
                "Mahim",
                "Borivali",
                "08:55",
                "fastest",
            )

        self.assertNotIn("error", rec)
        self.assertEqual(rec["best"]["kind"], "transfer")
        self.assertEqual(rec["best"]["transfer_station"], "BD")
        self.assertEqual(rec["best"]["legs"][0]["train_id"], "slow_to_bandra")
        self.assertEqual(rec["best"]["legs"][1]["train_id"], "fast_from_bandra")

if __name__ == "__main__":
    unittest.main()
