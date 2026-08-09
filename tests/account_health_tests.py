"""Unit tests for pure account pool health helpers (no DB / server)."""

from __future__ import annotations

import time
import unittest

from gateway import account_health as ah


class AccountHealthTableTests(unittest.TestCase):
    """Table-driven coverage of readiness dimensions and composite status."""

    def test_auth_ready_matrix(self):
        cases = [
            ({"status": "verified", "ouid": "u", "ouss": "s"}, True),
            ({"status": "active", "ouid": "u", "ouss": "s"}, True),
            ({"status": "verified", "ouid": "u", "ouss": ""}, False),
            ({"status": "verified", "ouid": "", "ouss": "s"}, False),
            ({"status": "verified", "ouid": "  ", "ouss": "s"}, False),
            ({"status": "verified"}, False),  # missing ouid/ouss
            ({"status": "invalid", "ouid": "u", "ouss": "s"}, False),
            ({"status": "disabled", "ouid": "u", "ouss": "s"}, False),
            ({"status": "new", "ouid": "u", "ouss": "s"}, False),
            ({"status": "pending_validation", "ouid": "u", "ouss": "s"}, False),
        ]
        for row, expected in cases:
            with self.subTest(row=row, expected=expected):
                self.assertEqual(ah.account_auth_ready(row), expected)

    def test_points_ready_matrix(self):
        cases = [
            ({"rest_point": 100}, True),
            ({"rest_point": 10}, True),
            ({"rest_point": 9}, False),  # low
            ({"rest_point": 0}, False),  # empty
            ({"rest_point": 5}, False),
            ({}, True),  # unknown -> ready for display
            ({"rest_point": None}, True),
            ({"daily_point": 0, "bonus_point": 0}, False),  # known empty
            ({"daily_point": 3, "bonus_point": 2}, False),  # low 5
            ({"daily_point": 8, "bonus_point": 5}, True),  # 13 ok
        ]
        for row, expected in cases:
            with self.subTest(row=row, expected=expected):
                self.assertEqual(ah.account_points_ready(row), expected)

    def test_health_status_priority(self):
        now = 1_700_000_000.0
        cases = [
            ({"status": "disabled"}, "disabled"),
            ({"status": "invalid"}, "invalid"),
            ({"status": "new"}, "pending"),
            ({"status": "pending_validation"}, "pending"),
            ({"status": "verified", "cooldown_until": now + 60}, "cooling"),
            ({"status": "verified", "rest_point": 0}, "low_balance"),
            ({"status": "verified", "rest_point": 5}, "low_balance"),
            ({"status": "verified", "rest_point": 50}, "healthy"),
            ({"status": "active", "rest_point": 50}, "healthy"),
            ({"status": "verified"}, "healthy"),  # unknown balance still healthy
        ]
        for row, expected in cases:
            with self.subTest(row=row, expected=expected):
                self.assertEqual(ah.account_health_status(row, now=now), expected)

    def test_generate_ready_matrix(self):
        now = 1_700_000_000.0
        base = {"status": "verified", "ouid": "ouid", "ouss": "ouss", "rest_point": 100}
        cases = [
            # missing ouid -> not auth_ready
            ({"status": "verified", "ouid": "", "ouss": "s", "rest_point": 100}, True, False),
            # low balance
            ({**base, "rest_point": 5}, True, False),
            # cooling
            ({**base, "cooldown_until": now + 120}, True, False),
            # no caps
            (base, False, False),
            # healthy + caps
            (base, True, True),
            # invalid status
            ({**base, "status": "invalid"}, True, False),
            # disabled
            ({**base, "status": "disabled"}, True, False),
            # active + caps
            ({**base, "status": "active"}, True, True),
        ]
        for row, has_caps, expected in cases:
            with self.subTest(row=row, has_caps=has_caps, expected=expected):
                self.assertEqual(
                    ah.account_generate_ready(row, has_schedulable_capability=has_caps, now=now),
                    expected,
                )

    def test_account_health_fields_includes_three_ready_bools(self):
        row = {"status": "verified", "ouid": "u", "ouss": "s", "rest_point": 50}
        fields = ah.account_health_fields(row, has_schedulable_capability=True)
        self.assertTrue(fields["auth_ready"])
        self.assertTrue(fields["points_ready"])
        self.assertTrue(fields["generate_ready"])
        self.assertEqual(fields["health_status"], "healthy")
        self.assertIn("cooling", fields)
        self.assertIn("balance_status", fields)
        self.assertIn("risk_status", fields)

    def test_pool_summary_counts_three_ready_dimensions(self):
        now = time.time()
        rows = [
            # generate_ready
            {"status": "verified", "ouid": "a", "ouss": "b", "rest_point": 100, "_caps": True},
            # auth+points but cooling -> not generate
            {
                "status": "verified",
                "ouid": "a",
                "ouss": "b",
                "rest_point": 100,
                "cooldown_until": now + 300,
                "_caps": True,
            },
            # low balance
            {"status": "verified", "ouid": "a", "ouss": "b", "rest_point": 3, "_caps": True},
            # missing session secrets
            {"status": "verified", "ouid": "", "ouss": "", "rest_point": 100, "_caps": True},
            # healthy but no caps
            {"status": "verified", "ouid": "a", "ouss": "b", "rest_point": 100, "_caps": False},
            # invalid
            {"status": "invalid", "ouid": "a", "ouss": "b", "rest_point": 100, "_caps": True},
        ]

        def caps_fn(row):
            return bool(row.get("_caps"))

        summary = ah.account_pool_summary(rows, now=now, has_schedulable_capability=caps_fn)
        self.assertEqual(summary["total"], 6)
        self.assertEqual(summary["verified"], 5)  # all but invalid
        self.assertEqual(summary["auth_ready"], 4)  # missing secrets + invalid out
        self.assertEqual(summary["points_ready"], 5)  # only low_balance row fails
        self.assertEqual(summary["generate_ready"], 1)
        # Legacy healthy bucket: verified|active + health healthy + caps
        # (includes missing-ouid row; generate_ready is stricter via auth_ready).
        self.assertEqual(summary["healthy"], 2)
        self.assertEqual(summary["cooling"], 1)
        self.assertEqual(summary["low_balance"], 1)
        self.assertEqual(summary["invalid"], 1)

    def test_pool_summary_without_caps_fn_never_generate_ready(self):
        rows = [{"status": "verified", "ouid": "a", "ouss": "b", "rest_point": 100}]
        summary = ah.account_pool_summary(rows)
        self.assertEqual(summary["generate_ready"], 0)
        self.assertEqual(summary["healthy"], 0)
        self.assertEqual(summary["auth_ready"], 1)
        self.assertEqual(summary["points_ready"], 1)


if __name__ == "__main__":
    unittest.main()
