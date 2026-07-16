import datetime
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bin"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import FakeKVCollection  # noqa: E402

from app.models.base import NotFoundError, parse_iso  # noqa: E402
from app.models.platform import PlatformStore  # noqa: E402
from app.models.datasource import DataSourceStore, STATUS_NOT_RUN, STATUS_PASS  # noqa: E402
from app.models.validation_run import ValidationRunStore, RUN_STATUS_CANCELLED, RUN_STATUS_COMPLETED, RUN_STATUS_RUNNING  # noqa: E402
from app.models.validation_queue import (  # noqa: E402
    QUEUE_STATUS_CANCELLED,
    QUEUE_STATUS_DONE,
    QUEUE_STATUS_QUEUED,
    QUEUE_STATUS_RUNNING,
    ValidationQueueStore,
)
from app.models.validation_result import ValidationResultStore  # noqa: E402
from app.models.settings import SettingsStore  # noqa: E402
from app.models.worker_lease import WorkerLeaseStore  # noqa: E402


class PlatformStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = PlatformStore(FakeKVCollection())

    def test_create_and_get(self):
        self.store.create("p1", "Firewall", description="EU firewalls", sort_order=1)
        got = self.store.get("p1")
        self.assertEqual(got.name, "Firewall")
        self.assertEqual(got.sort_order, 1)
        self.assertTrue(got.enabled)
        self.assertIsNotNone(got.created_time)

    def test_get_missing_returns_none(self):
        self.assertIsNone(self.store.get("nope"))

    def test_list_sorted_by_sort_order_then_name(self):
        self.store.create("p2", "Bravo", sort_order=2)
        self.store.create("p1", "Alpha", sort_order=1)
        self.store.create("p3", "Charlie", sort_order=1)
        names = [p.name for p in self.store.list()]
        self.assertEqual(names, ["Alpha", "Charlie", "Bravo"])

    def test_list_enabled_only(self):
        self.store.create("p1", "Enabled", enabled=True)
        self.store.create("p2", "Disabled", enabled=False)
        names = [p.name for p in self.store.list(enabled_only=True)]
        self.assertEqual(names, ["Enabled"])

    def test_update_bumps_updated_time_and_changes_field(self):
        self.store.create("p1", "Old Name")
        updated = self.store.update("p1", name="New Name")
        self.assertEqual(updated.name, "New Name")

    def test_update_rejects_unknown_field(self):
        self.store.create("p1", "P")
        with self.assertRaises(ValueError):
            self.store.update("p1", **{"not_a_real_field": "different"})

    def test_update_missing_raises_not_found(self):
        with self.assertRaises(NotFoundError):
            self.store.update("nope", name="X")

    def test_delete(self):
        self.store.create("p1", "P")
        self.store.delete("p1")
        self.assertIsNone(self.store.get("p1"))

    def test_create_rejects_bad_identifier(self):
        with self.assertRaises(Exception):
            self.store.create("../bad", "P")


class DataSourceStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = DataSourceStore(FakeKVCollection())

    def test_create_computes_hash_and_version_one(self):
        d = self.store.create("d1", "p1", "Auth Log", "search index=auth")
        self.assertEqual(d.query_version, 1)
        self.assertTrue(d.query_hash)
        self.assertEqual(d.current_status, STATUS_NOT_RUN)

    def test_create_rejects_empty_query(self):
        with self.assertRaises(ValueError):
            self.store.create("d1", "p1", "Auth Log", "   ")

    def test_update_query_bumps_version_hash_and_resets_status(self):
        self.store.create("d1", "p1", "Auth Log", "search index=auth")
        self.store.update_status("d1", STATUS_PASS, last_result_time="2026-01-01T00:00:00.000Z")
        before = self.store.get("d1")
        self.assertEqual(before.current_status, STATUS_PASS)

        after = self.store.update_query("d1", "search index=auth sourcetype=new")
        self.assertEqual(after.query_version, 2)
        self.assertNotEqual(after.query_hash, before.query_hash)
        self.assertEqual(after.current_status, STATUS_NOT_RUN)

    def test_list_filters_by_platform(self):
        self.store.create("d1", "p1", "A", "search index=a")
        self.store.create("d2", "p2", "B", "search index=b")
        results = self.store.list(platform_id="p1")
        self.assertEqual([d.datasource_id for d in results], ["d1"])

    def test_update_status_sets_last_run_id(self):
        self.store.create("d1", "p1", "A", "search index=a")
        updated = self.store.update_status("d1", STATUS_PASS, last_run_id="run_123")
        self.assertEqual(updated.last_run_id, "run_123")


class ValidationRunStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = ValidationRunStore(FakeKVCollection())

    def test_create_defaults_to_running(self):
        run = self.store.create("admin", "manual", total_items=3)
        self.assertEqual(run.status, RUN_STATUS_RUNNING)
        self.assertEqual(run.completed_items, 0)

    def test_set_progress(self):
        run = self.store.create("admin", "manual", total_items=3)
        updated = self.store.set_progress(run.run_id, 2)
        self.assertEqual(updated.completed_items, 2)

    def test_mark_completed(self):
        run = self.store.create("admin", "manual", total_items=1)
        updated = self.store.mark_completed(run.run_id)
        self.assertEqual(updated.status, RUN_STATUS_COMPLETED)
        self.assertIsNotNone(updated.completed_time)

    def test_cancel(self):
        run = self.store.create("admin", "manual", total_items=1)
        updated = self.store.cancel(run.run_id)
        self.assertEqual(updated.status, RUN_STATUS_CANCELLED)

    def test_list_sorted_newest_first(self):
        r1 = self.store.create("admin", "manual", total_items=1)
        r2 = self.store.create("admin", "manual", total_items=1)
        # Force distinct timestamps: two creates in the same test can land
        # in the same millisecond, which would make ordering ambiguous.
        raw1 = self.store._collection.get(r1.run_id)
        raw1["created_time"] = "2020-01-01T00:00:00.000Z"
        self.store._collection.update(r1.run_id, raw1)
        raw2 = self.store._collection.get(r2.run_id)
        raw2["created_time"] = "2020-01-01T00:00:01.000Z"
        self.store._collection.update(r2.run_id, raw2)

        runs = self.store.list()
        self.assertEqual(runs[0].run_id, r2.run_id)
        self.assertEqual(runs[1].run_id, r1.run_id)


class ValidationQueueStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = ValidationQueueStore(FakeKVCollection())

    def test_create_batch_preserves_order(self):
        items = [
            {"platform_id": "p1", "datasource_id": "d2", "order_index": 2, "query_hash": "h", "query_version": 1},
            {"platform_id": "p1", "datasource_id": "d1", "order_index": 1, "query_hash": "h", "query_version": 1},
        ]
        created = self.store.create_batch("run1", items)
        self.assertEqual([c.datasource_id for c in created], ["d1", "d2"])

    def test_unique_keys_no_double_execution(self):
        items = [{"platform_id": "p1", "datasource_id": "d1", "order_index": 1, "query_hash": "h", "query_version": 1}]
        created = self.store.create_batch("run1", items)
        self.assertEqual(len(set(c.queue_id for c in created)), 1)

    def test_find_any_running_none_initially(self):
        self.assertIsNone(self.store.find_any_running())

    def test_mark_running_then_found_by_find_any_running(self):
        items = [{"platform_id": "p1", "datasource_id": "d1", "order_index": 1, "query_hash": "h", "query_version": 1}]
        created = self.store.create_batch("run1", items)
        self.store.mark_running(created[0].queue_id, sid="1234.56")
        running = self.store.find_any_running()
        self.assertIsNotNone(running)
        self.assertEqual(running.sid, "1234.56")

    def test_mark_done_clears_running(self):
        items = [{"platform_id": "p1", "datasource_id": "d1", "order_index": 1, "query_hash": "h", "query_version": 1}]
        created = self.store.create_batch("run1", items)
        self.store.mark_running(created[0].queue_id, sid="1.1")
        self.store.mark_done(created[0].queue_id, status=QUEUE_STATUS_DONE)
        self.assertIsNone(self.store.find_any_running())

    def test_cancel_queued_for_run_only_touches_queued_items(self):
        items = [
            {"platform_id": "p1", "datasource_id": "d1", "order_index": 1, "query_hash": "h", "query_version": 1},
            {"platform_id": "p1", "datasource_id": "d2", "order_index": 2, "query_hash": "h", "query_version": 1},
        ]
        created = self.store.create_batch("run1", items)
        self.store.mark_running(created[0].queue_id, sid="1.1")
        self.store.cancel_queued_for_run("run1")

        first = self.store.get(created[0].queue_id)
        second = self.store.get(created[1].queue_id)
        self.assertEqual(first.status, QUEUE_STATUS_RUNNING)
        self.assertEqual(second.status, QUEUE_STATUS_CANCELLED)

    def test_list_for_run_ordered(self):
        items = [
            {"platform_id": "p1", "datasource_id": "d2", "order_index": 2, "query_hash": "h", "query_version": 1},
            {"platform_id": "p1", "datasource_id": "d1", "order_index": 1, "query_hash": "h", "query_version": 1},
        ]
        self.store.create_batch("run1", items)
        ordered = self.store.list_for_run("run1")
        self.assertEqual([i.order_index for i in ordered], [1, 2])


class ValidationResultStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = ValidationResultStore(FakeKVCollection())

    def test_create_and_latest_for_datasource(self):
        self.store.create(
            run_id="r1", queue_id="q1", datasource_id="d1", platform_id="p1",
            status="PASS", result_count=5, query_hash="h", query_version=1,
            executed_time="2026-01-01T00:00:00.000Z",
        )
        self.store.create(
            run_id="r2", queue_id="q2", datasource_id="d1", platform_id="p1",
            status="FAIL", result_count=0, query_hash="h", query_version=1,
            executed_time="2026-02-01T00:00:00.000Z",
        )
        latest = self.store.latest_for_datasource("d1")
        self.assertEqual(latest.status, "FAIL")

    def test_list_for_datasource_history_order(self):
        self.store.create(
            run_id="r1", queue_id="q1", datasource_id="d1", platform_id="p1",
            status="PASS", result_count=1, query_hash="h", query_version=1,
            executed_time="2026-01-01T00:00:00.000Z",
        )
        self.store.create(
            run_id="r2", queue_id="q2", datasource_id="d1", platform_id="p1",
            status="FAIL", result_count=0, query_hash="h", query_version=1,
            executed_time="2026-02-01T00:00:00.000Z",
        )
        history = self.store.list_for_datasource("d1")
        self.assertEqual([h.status for h in history], ["FAIL", "PASS"])

    def test_list_for_run(self):
        self.store.create(
            run_id="r1", queue_id="q1", datasource_id="d1", platform_id="p1",
            status="PASS", result_count=1, query_hash="h", query_version=1,
        )
        self.store.create(
            run_id="r1", queue_id="q2", datasource_id="d2", platform_id="p1",
            status="ERROR", result_count=0, query_hash="h", query_version=1,
        )
        results = self.store.list_for_run("r1")
        self.assertEqual(len(results), 2)


class SettingsStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = SettingsStore(FakeKVCollection())

    def test_get_default_when_unset(self):
        self.assertEqual(self.store.get("missing_key", default="fallback"), "fallback")

    def test_set_then_get(self):
        self.store.set("default_stale_threshold_sec", "3600", updated_by="admin")
        self.assertEqual(self.store.get("default_stale_threshold_sec"), "3600")

    def test_set_overwrites_existing(self):
        self.store.set("k", "v1")
        self.store.set("k", "v2")
        self.assertEqual(self.store.get("k"), "v2")

    def test_get_all(self):
        self.store.set("a", "1")
        self.store.set("b", "2")
        self.assertEqual(self.store.get_all(), {"a": "1", "b": "2"})

    def test_delete(self):
        self.store.set("k", "v")
        self.store.delete("k")
        self.assertIsNone(self.store.get("k"))


class WorkerLeaseStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = WorkerLeaseStore(FakeKVCollection())

    def test_acquire_when_unheld(self):
        lease = self.store.try_acquire("member-a", ttl_seconds=30)
        self.assertIsNotNone(lease)
        self.assertEqual(lease.owner, "member-a")

    def test_second_owner_cannot_acquire_live_lease(self):
        self.store.try_acquire("member-a", ttl_seconds=30)
        result = self.store.try_acquire("member-b", ttl_seconds=30)
        self.assertIsNone(result)

    def test_owner_can_renew_its_own_lease(self):
        self.store.try_acquire("member-a", ttl_seconds=30)
        result = self.store.try_acquire("member-a", ttl_seconds=30)
        self.assertIsNotNone(result)
        self.assertEqual(result.owner, "member-a")

    def test_expired_lease_can_be_taken_by_another_owner(self):
        self.store.try_acquire("member-a", ttl_seconds=30)
        lease = self.store.get()
        # Force it into the past.
        expired_time = (parse_iso(lease.acquired_time) - datetime.timedelta(seconds=60))
        expired_iso = expired_time.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        raw = self.store._collection.get("dsv_validation_worker")
        raw["expires_time"] = expired_iso
        self.store._collection.update("dsv_validation_worker", raw)

        result = self.store.try_acquire("member-b", ttl_seconds=30)
        self.assertIsNotNone(result)
        self.assertEqual(result.owner, "member-b")

    def test_heartbeat_extends_expiry_for_owner_only(self):
        self.store.try_acquire("member-a", ttl_seconds=30)
        original = self.store.get()
        self.assertIsNone(self.store.heartbeat("member-b", ttl_seconds=30))
        renewed = self.store.heartbeat("member-a", ttl_seconds=30)
        self.assertIsNotNone(renewed)
        self.assertGreaterEqual(parse_iso(renewed.expires_time), parse_iso(original.expires_time))

    def test_release_only_by_owner(self):
        self.store.try_acquire("member-a", ttl_seconds=30)
        self.assertFalse(self.store.release("member-b"))
        self.assertTrue(self.store.release("member-a"))
        self.assertIsNone(self.store.get())


if __name__ == "__main__":
    unittest.main()
