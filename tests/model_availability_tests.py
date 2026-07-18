import unittest

from gateway.model_availability import (
    attach_spendable_availability,
    availability_status,
    expand_capability_cost_rows,
    public_availability_items,
    sort_availability_items,
)


class ModelAvailabilityTests(unittest.TestCase):
    def sample_caps(self):
        return {
            "image": {
                "models": [
                    {
                        "name": "Google Nano Banana 2",
                        "enabled": True,
                        "experimental": False,
                        "verification_status": "live_verified",
                        "point_cost": [
                            {"resolution": "2K", "point": 8},
                            {"resolution": "4K", "point": 12},
                        ],
                    }
                ]
            },
            "video": {
                "models": [
                    {
                        "name": "Seedance 1.5 Pro",
                        "enabled": True,
                        "experimental": False,
                        "verification_status": "unit_tested",
                        "point_cost_image": [
                            {"duration": 5, "resolution": "480", "point": 11},
                            {"duration": 10, "resolution": "1080", "point": 80},
                        ],
                        "point_cost_reference": [{"duration": 5, "point": 25}],
                        "point_cost_motion": [{"duration": 5, "point": 30}],
                    }
                ],
                "scenes": [
                    {
                        "scene_id": "text_or_image",
                        "name": "文/图生视频",
                        "enabled": True,
                        "experimental": False,
                        "verification_status": "live_verified",
                    },
                    {
                        "scene_id": "frame_based",
                        "name": "首尾帧",
                        "enabled": True,
                        "experimental": True,
                        "verification_status": "live_verified",
                    },
                    {
                        "scene_id": "reference",
                        "name": "参考",
                        "enabled": False,
                        "experimental": True,
                        "verification_status": "unverified",
                    },
                ],
            },
        }

    def test_availability_status_thresholds(self):
        self.assertEqual(availability_status(3), "available")
        self.assertEqual(availability_status(1), "tight")
        self.assertEqual(availability_status(0), "unavailable")

    def test_expand_skips_disabled_scenes_by_default(self):
        rows = expand_capability_cost_rows(self.sample_caps())
        scenes = {row["scene_id"] for row in rows if row["kind"] == "video"}
        self.assertIn("text_or_image", scenes)
        self.assertIn("frame_based", scenes)
        self.assertNotIn("reference", scenes)
        self.assertTrue(any(row["kind"] == "image" and row["resolution"] == "4K" for row in rows))

    def test_expand_can_include_disabled(self):
        rows = expand_capability_cost_rows(self.sample_caps(), include_disabled=True)
        self.assertTrue(any(row["scene_id"] == "reference" for row in rows))

    def test_single_account_enough_for_cheap_but_not_expensive(self):
        rows = expand_capability_cost_rows(self.sample_caps())
        cheap = next(row for row in rows if row["point_cost"] == 11)
        expensive = next(row for row in rows if row["point_cost"] == 80)
        # Ten accounts with 50 points: total 500, but none can take 80.
        spendable = {11: [50] * 10, 80: [50] * 10, 8: [50] * 10, 12: [50] * 10}
        scored = {item["point_cost"]: item for item in attach_spendable_availability([cheap, expensive], spendable)}
        self.assertEqual(scored[11]["status"], "available")
        self.assertEqual(scored[11]["ready_accounts"], 10)
        self.assertEqual(scored[80]["status"], "unavailable")
        self.assertEqual(scored[80]["ready_accounts"], 0)

    def test_public_items_strip_account_fields(self):
        rows = attach_spendable_availability(
            expand_capability_cost_rows(self.sample_caps()),
            {8: [20], 11: [20], 12: [20], 80: [20]},
        )
        public = public_availability_items(rows)
        self.assertTrue(public)
        for item in public:
            self.assertNotIn("ready_accounts", item)
            self.assertNotIn("task_capacity", item)
            self.assertIn(item["status"], {"available", "tight", "unavailable"})

    def test_sort_puts_available_first(self):
        items = sort_availability_items(
            [
                {"status": "unavailable", "kind": "video", "model_name": "B", "point_cost": 1},
                {"status": "available", "kind": "image", "model_name": "A", "point_cost": 9},
                {"status": "tight", "kind": "video", "model_name": "C", "point_cost": 2},
            ]
        )
        self.assertEqual([item["status"] for item in items], ["available", "tight", "unavailable"])


if __name__ == "__main__":
    unittest.main()
