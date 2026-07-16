import datetime
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bin"))

from app.models.datasource import (  # noqa: E402
    DataSource,
    STATUS_CANCELLED,
    STATUS_ERROR,
    STATUS_FAIL,
    STATUS_NOT_RUN,
    STATUS_PASS,
    STATUS_QUEUED,
    STATUS_RUNNING,
)
from app.models.platform import Platform  # noqa: E402
from app.status_calculator import (  # noqa: E402
    DISABLED,
    PLATFORM_AMBER,
    PLATFORM_GREEN,
    PLATFORM_GREY,
    PLATFORM_RED,
    PLATFORM_RUNNING,
    compute_datasource_display_status,
    compute_platform_status,
)

NOW = datetime.datetime(2026, 7, 16, 12, 0, 0, tzinfo=datetime.timezone.utc)


def ds(**overrides):
    defaults = dict(
        datasource_id="ds1", platform_id="p1", name="DS1", spl_query="search index=main",
        enabled=True, current_status=STATUS_NOT_RUN, last_result_time=None,
        stale_threshold_sec=None,
    )
    defaults.update(overrides)
    return DataSource(**defaults)


def platform(**overrides):
    defaults = dict(platform_id="p1", name="P1", enabled=True)
    defaults.update(overrides)
    return Platform(**defaults)


class DataSourceStatusTests(unittest.TestCase):
    def test_disabled_datasource_is_disabled_regardless_of_current_status(self):
        d = compute_datasource_display_status(ds(enabled=False, current_status=STATUS_PASS), now=NOW)
        self.assertEqual(d.status, DISABLED)
        self.assertFalse(d.stale)

    def test_pass_without_threshold_is_never_stale(self):
        d = compute_datasource_display_status(
            ds(current_status=STATUS_PASS, last_result_time="2020-01-01T00:00:00.000Z"),
            now=NOW,
        )
        self.assertEqual(d.status, STATUS_PASS)
        self.assertFalse(d.stale)

    def test_fresh_pass_is_not_stale(self):
        recent = (NOW - datetime.timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        d = compute_datasource_display_status(
            ds(current_status=STATUS_PASS, last_result_time=recent, stale_threshold_sec=3600),
            now=NOW,
        )
        self.assertFalse(d.stale)
        self.assertNotIn("Stale", d.label)

    def test_old_pass_beyond_threshold_is_stale(self):
        old = (NOW - datetime.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        d = compute_datasource_display_status(
            ds(current_status=STATUS_PASS, last_result_time=old, stale_threshold_sec=3600),
            now=NOW,
        )
        self.assertTrue(d.stale)
        self.assertIn("Stale", d.label)

    def test_per_datasource_threshold_overrides_default(self):
        old = (NOW - datetime.timedelta(seconds=100)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        d = compute_datasource_display_status(
            ds(current_status=STATUS_PASS, last_result_time=old, stale_threshold_sec=50),
            now=NOW, default_stale_threshold_sec=999999,
        )
        self.assertTrue(d.stale)

    def test_fail_is_never_stale_even_if_old(self):
        old = (NOW - datetime.timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        d = compute_datasource_display_status(
            ds(current_status=STATUS_FAIL, last_result_time=old, stale_threshold_sec=60),
            now=NOW,
        )
        self.assertEqual(d.status, STATUS_FAIL)
        self.assertFalse(d.stale)

    def test_error_is_distinct_from_fail(self):
        d_fail = compute_datasource_display_status(ds(current_status=STATUS_FAIL), now=NOW)
        d_error = compute_datasource_display_status(ds(current_status=STATUS_ERROR), now=NOW)
        self.assertNotEqual(d_fail.status, d_error.status)
        self.assertNotEqual(d_fail.label, d_error.label)

    def test_every_status_has_label_icon_and_description(self):
        for status in (STATUS_PASS, STATUS_FAIL, STATUS_ERROR, STATUS_RUNNING,
                       STATUS_QUEUED, STATUS_NOT_RUN, STATUS_CANCELLED):
            d = compute_datasource_display_status(ds(current_status=status), now=NOW)
            with self.subTest(status=status):
                self.assertTrue(d.label)
                self.assertTrue(d.icon)
                self.assertTrue(d.description)


class PlatformStatusTests(unittest.TestCase):
    def test_disabled_platform_is_grey(self):
        p = compute_platform_status(platform(enabled=False), [
            compute_datasource_display_status(ds(current_status=STATUS_PASS), now=NOW),
        ])
        self.assertEqual(p.status, PLATFORM_GREY)

    def test_zero_enabled_children_is_grey_not_green(self):
        p = compute_platform_status(platform(enabled=True), [
            compute_datasource_display_status(ds(enabled=False), now=NOW),
        ])
        self.assertEqual(p.status, PLATFORM_GREY)

    def test_zero_children_at_all_is_grey(self):
        p = compute_platform_status(platform(enabled=True), [])
        self.assertEqual(p.status, PLATFORM_GREY)

    def test_all_fresh_pass_is_green(self):
        recent = (NOW - datetime.timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        children = [
            compute_datasource_display_status(
                ds(datasource_id="a", current_status=STATUS_PASS, last_result_time=recent, stale_threshold_sec=3600),
                now=NOW,
            ),
            compute_datasource_display_status(
                ds(datasource_id="b", current_status=STATUS_PASS, last_result_time=recent, stale_threshold_sec=3600),
                now=NOW,
            ),
        ]
        p = compute_platform_status(platform(), children)
        self.assertEqual(p.status, PLATFORM_GREEN)

    def test_any_fail_makes_platform_red(self):
        children = [
            compute_datasource_display_status(ds(datasource_id="a", current_status=STATUS_PASS), now=NOW),
            compute_datasource_display_status(ds(datasource_id="b", current_status=STATUS_FAIL), now=NOW),
        ]
        p = compute_platform_status(platform(), children)
        self.assertEqual(p.status, PLATFORM_RED)

    def test_any_error_makes_platform_red(self):
        children = [
            compute_datasource_display_status(ds(datasource_id="a", current_status=STATUS_ERROR), now=NOW),
        ]
        p = compute_platform_status(platform(), children)
        self.assertEqual(p.status, PLATFORM_RED)

    def test_any_running_makes_platform_running(self):
        children = [
            compute_datasource_display_status(ds(datasource_id="a", current_status=STATUS_PASS), now=NOW),
            compute_datasource_display_status(ds(datasource_id="b", current_status=STATUS_RUNNING), now=NOW),
        ]
        p = compute_platform_status(platform(), children)
        self.assertEqual(p.status, PLATFORM_RUNNING)

    def test_any_queued_makes_platform_running(self):
        children = [
            compute_datasource_display_status(ds(datasource_id="a", current_status=STATUS_QUEUED), now=NOW),
        ]
        p = compute_platform_status(platform(), children)
        self.assertEqual(p.status, PLATFORM_RUNNING)

    def test_all_pass_but_one_stale_is_amber(self):
        old = (NOW - datetime.timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        recent = (NOW - datetime.timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        children = [
            compute_datasource_display_status(
                ds(datasource_id="a", current_status=STATUS_PASS, last_result_time=recent, stale_threshold_sec=3600),
                now=NOW,
            ),
            compute_datasource_display_status(
                ds(datasource_id="b", current_status=STATUS_PASS, last_result_time=old, stale_threshold_sec=3600),
                now=NOW,
            ),
        ]
        p = compute_platform_status(platform(), children)
        self.assertEqual(p.status, PLATFORM_AMBER)

    def test_not_run_child_is_amber(self):
        children = [
            compute_datasource_display_status(ds(datasource_id="a", current_status=STATUS_NOT_RUN), now=NOW),
        ]
        p = compute_platform_status(platform(), children)
        self.assertEqual(p.status, PLATFORM_AMBER)

    def test_cancelled_child_is_amber(self):
        children = [
            compute_datasource_display_status(ds(datasource_id="a", current_status=STATUS_PASS), now=NOW),
            compute_datasource_display_status(ds(datasource_id="b", current_status=STATUS_CANCELLED), now=NOW),
        ]
        p = compute_platform_status(platform(), children)
        self.assertEqual(p.status, PLATFORM_AMBER)

    def test_disabled_children_never_affect_platform_status(self):
        children = [
            compute_datasource_display_status(
                ds(datasource_id="a", enabled=False, current_status=STATUS_FAIL), now=NOW,
            ),
        ]
        p = compute_platform_status(platform(), children)
        # Only a disabled child exists -> zero enabled children -> grey, not red.
        self.assertEqual(p.status, PLATFORM_GREY)

    def test_every_platform_status_has_label_icon_and_description(self):
        for children, expected in (
            ([], PLATFORM_GREY),
            ([compute_datasource_display_status(ds(current_status=STATUS_FAIL), now=NOW)], PLATFORM_RED),
            ([compute_datasource_display_status(ds(current_status=STATUS_RUNNING), now=NOW)], PLATFORM_RUNNING),
            ([compute_datasource_display_status(ds(current_status=STATUS_NOT_RUN), now=NOW)], PLATFORM_AMBER),
        ):
            p = compute_platform_status(platform(), children)
            with self.subTest(expected=expected):
                self.assertEqual(p.status, expected)
                self.assertTrue(p.label)
                self.assertTrue(p.icon)
                self.assertTrue(p.description)


if __name__ == "__main__":
    unittest.main()
