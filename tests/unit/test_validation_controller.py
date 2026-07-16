import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bin"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import FakeKVCollection, FakeSearchExecutor  # noqa: E402

from app.models.base import NotFoundError  # noqa: E402
from app.models.datasource import DataSourceStore, STATUS_CANCELLED, STATUS_ERROR, STATUS_FAIL, STATUS_PASS, STATUS_QUEUED, STATUS_RUNNING  # noqa: E402
from app.models.platform import PlatformStore  # noqa: E402
from app.models.validation_queue import QUEUE_STATUS_CANCELLED, QUEUE_STATUS_DONE, QUEUE_STATUS_ERROR, QUEUE_STATUS_QUEUED, QUEUE_STATUS_RUNNING, ValidationQueueStore  # noqa: E402
from app.models.validation_result import ValidationResultStore  # noqa: E402
from app.models.validation_run import RUN_STATUS_CANCELLED, RUN_STATUS_COMPLETED, RUN_STATUS_RUNNING, ValidationRunStore  # noqa: E402
from app.models.worker_lease import WorkerLeaseStore  # noqa: E402

import validation_controller as vc  # noqa: E402


def make_stores():
    return vc.Stores(
        platform_store=PlatformStore(FakeKVCollection()),
        datasource_store=DataSourceStore(FakeKVCollection()),
        run_store=ValidationRunStore(FakeKVCollection()),
        queue_store=ValidationQueueStore(FakeKVCollection()),
        result_store=ValidationResultStore(FakeKVCollection()),
        lease_store=WorkerLeaseStore(FakeKVCollection()),
    )


def seed_basic(stores):
    stores.platform_store.create("p1", "Firewall", sort_order=1)
    stores.datasource_store.create("d1", "p1", "Auth", "search index=auth", sort_order=1)
    stores.datasource_store.create("d2", "p1", "Proxy", "search index=proxy", sort_order=2)


class CreateRunTests(unittest.TestCase):
    def test_includes_only_enabled_under_enabled_platform(self):
        stores = make_stores()
        stores.platform_store.create("p1", "Enabled", sort_order=1)
        stores.platform_store.create("p2", "Disabled", sort_order=2, enabled=False)
        stores.datasource_store.create("d1", "p1", "A", "search index=a")
        stores.datasource_store.create("d2", "p1", "B (disabled)", "search index=b", enabled=False)
        stores.datasource_store.create("d3", "p2", "C", "search index=c")

        run = vc.create_run(stores, created_by="admin", trigger_type="manual")
        items = stores.queue_store.list_for_run(run.run_id)
        self.assertEqual([i.datasource_id for i in items], ["d1"])
        self.assertEqual(run.total_items, 1)

    def test_orders_by_platform_then_datasource_sort_then_name(self):
        stores = make_stores()
        stores.platform_store.create("p2", "Second Platform", sort_order=2)
        stores.platform_store.create("p1", "First Platform", sort_order=1)
        stores.datasource_store.create("d_p2_a", "p2", "Zeta", "search index=z", sort_order=1)
        stores.datasource_store.create("d_p1_b", "p1", "Bravo", "search index=b", sort_order=2)
        stores.datasource_store.create("d_p1_a", "p1", "Alpha", "search index=a", sort_order=1)

        run = vc.create_run(stores, created_by="admin", trigger_type="manual")
        items = stores.queue_store.list_for_run(run.run_id)
        self.assertEqual([i.datasource_id for i in items], ["d_p1_a", "d_p1_b", "d_p2_a"])

    def test_raises_when_nothing_selected(self):
        stores = make_stores()
        with self.assertRaises(ValueError):
            vc.create_run(stores, created_by="admin", trigger_type="manual")

    def test_sets_datasource_status_queued(self):
        stores = make_stores()
        seed_basic(stores)
        vc.create_run(stores, created_by="admin", trigger_type="manual")
        self.assertEqual(stores.datasource_store.get("d1").current_status, STATUS_QUEUED)
        self.assertEqual(stores.datasource_store.get("d2").current_status, STATUS_QUEUED)

    def test_explicit_datasource_ids_subset(self):
        stores = make_stores()
        seed_basic(stores)
        run = vc.create_run(stores, created_by="admin", trigger_type="manual", datasource_ids={"d2"})
        items = stores.queue_store.list_for_run(run.run_id)
        self.assertEqual([i.datasource_id for i in items], ["d2"])


class CancelRunTests(unittest.TestCase):
    def test_missing_run_raises(self):
        stores = make_stores()
        with self.assertRaises(NotFoundError):
            vc.cancel_run(stores, "nope")

    def test_cancels_queued_items_and_datasources(self):
        stores = make_stores()
        seed_basic(stores)
        run = vc.create_run(stores, created_by="admin", trigger_type="manual")

        vc.cancel_run(stores, run.run_id)

        items = stores.queue_store.list_for_run(run.run_id)
        self.assertTrue(all(i.status == QUEUE_STATUS_CANCELLED for i in items))
        self.assertEqual(stores.datasource_store.get("d1").current_status, STATUS_CANCELLED)
        self.assertEqual(stores.run_store.get(run.run_id).status, RUN_STATUS_CANCELLED)

    def test_leaves_running_item_and_run_active_until_it_finishes(self):
        stores = make_stores()
        seed_basic(stores)
        run = vc.create_run(stores, created_by="admin", trigger_type="manual")
        items = stores.queue_store.list_for_run(run.run_id)
        stores.queue_store.mark_running(items[0].queue_id, sid="1.1")

        vc.cancel_run(stores, run.run_id)

        self.assertEqual(stores.queue_store.get(items[0].queue_id).status, QUEUE_STATUS_RUNNING)
        self.assertEqual(stores.queue_store.get(items[1].queue_id).status, QUEUE_STATUS_CANCELLED)
        # Run itself stays RUNNING since one item is still in flight.
        self.assertEqual(stores.run_store.get(run.run_id).status, RUN_STATUS_RUNNING)


class RunTickTests(unittest.TestCase):
    def test_lease_held_by_other_owner_does_nothing(self):
        stores = make_stores()
        seed_basic(stores)
        vc.create_run(stores, created_by="admin", trigger_type="manual")
        stores.lease_store.try_acquire("member-a", ttl_seconds=60)

        executor = FakeSearchExecutor()
        vc.run_tick(stores, executor, lease_owner="member-b")

        self.assertEqual(executor.dispatch_calls, [])

    def test_dispatches_next_queued_when_idle(self):
        stores = make_stores()
        seed_basic(stores)
        run = vc.create_run(stores, created_by="admin", trigger_type="manual")

        executor = FakeSearchExecutor()
        vc.run_tick(stores, executor, lease_owner="member-a")

        self.assertEqual(executor.dispatch_calls, ["search index=auth"])
        items = stores.queue_store.list_for_run(run.run_id)
        self.assertEqual(items[0].status, QUEUE_STATUS_RUNNING)
        self.assertEqual(items[1].status, QUEUE_STATUS_QUEUED)
        self.assertEqual(stores.datasource_store.get("d1").current_status, STATUS_RUNNING)

    def test_does_not_dispatch_second_job_while_one_running(self):
        stores = make_stores()
        seed_basic(stores)
        vc.create_run(stores, created_by="admin", trigger_type="manual")

        executor = FakeSearchExecutor()
        vc.run_tick(stores, executor, lease_owner="member-a")
        vc.run_tick(stores, executor, lease_owner="member-a")

        self.assertEqual(len(executor.dispatch_calls), 1)

    def test_still_running_poll_leaves_state_unchanged(self):
        stores = make_stores()
        seed_basic(stores)
        vc.create_run(stores, created_by="admin", trigger_type="manual")
        executor = FakeSearchExecutor()
        vc.run_tick(stores, executor, lease_owner="member-a")

        vc.run_tick(stores, executor, lease_owner="member-a")

        self.assertEqual(stores.datasource_store.get("d1").current_status, STATUS_RUNNING)

    def test_finalizes_pass_and_advances_to_next_item(self):
        stores = make_stores()
        seed_basic(stores)
        run = vc.create_run(stores, created_by="admin", trigger_type="manual")
        executor = FakeSearchExecutor()
        vc.run_tick(stores, executor, lease_owner="member-a")  # dispatch d1
        sid = executor.dispatch_calls and list(stores.queue_store.list_for_run(run.run_id))[0].sid
        from fakes import FakeValidationOutcome
        executor.poll_results[sid] = [FakeValidationOutcome(status=STATUS_PASS, result_count=3)]

        vc.run_tick(stores, executor, lease_owner="member-a")  # finalize d1

        d1 = stores.datasource_store.get("d1")
        self.assertEqual(d1.current_status, STATUS_PASS)
        self.assertIsNotNone(d1.last_result_time)
        results = stores.result_store.list_for_datasource("d1")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].result_count, 3)

        vc.run_tick(stores, executor, lease_owner="member-a")  # dispatch d2
        self.assertEqual(len(executor.dispatch_calls), 2)

    def test_finalizes_fail(self):
        stores = make_stores()
        seed_basic(stores)
        vc.create_run(stores, created_by="admin", trigger_type="manual")
        executor = FakeSearchExecutor()
        vc.run_tick(stores, executor, lease_owner="member-a")
        sid = stores.queue_store.list_for_run(_only_run(stores))[0].sid
        from fakes import FakeValidationOutcome
        executor.poll_results[sid] = [FakeValidationOutcome(status=STATUS_FAIL, result_count=0)]

        vc.run_tick(stores, executor, lease_owner="member-a")

        self.assertEqual(stores.datasource_store.get("d1").current_status, STATUS_FAIL)

    def test_stuck_job_is_cancelled_and_marked_error(self):
        stores = make_stores()
        seed_basic(stores)
        vc.create_run(stores, created_by="admin", trigger_type="manual")
        executor = FakeSearchExecutor()
        vc.run_tick(stores, executor, lease_owner="member-a")
        run_id = _only_run(stores)
        item = stores.queue_store.list_for_run(run_id)[0]
        # Force dispatched_time far enough in the past to exceed a tiny timeout.
        raw = stores.queue_store._collection.get(item.queue_id)
        raw["dispatched_time"] = "2000-01-01T00:00:00.000Z"
        stores.queue_store._collection.update(item.queue_id, raw)

        vc.run_tick(stores, executor, lease_owner="member-a", stuck_job_timeout_sec=1)

        self.assertIn(item.sid, executor.cancelled)
        self.assertEqual(stores.datasource_store.get("d1").current_status, STATUS_ERROR)

    def test_rejected_query_at_dispatch_records_error_and_advances(self):
        stores = make_stores()
        seed_basic(stores)
        vc.create_run(stores, created_by="admin", trigger_type="manual")
        executor = FakeSearchExecutor()
        from fakes import FakeDispatchResult
        executor.dispatch_queue.append(FakeDispatchResult(allowed=False, reason="denylisted command"))

        vc.run_tick(stores, executor, lease_owner="member-a")

        self.assertEqual(stores.datasource_store.get("d1").current_status, STATUS_ERROR)
        results = stores.result_store.list_for_datasource("d1")
        self.assertEqual(results[0].error_message, "denylisted command")

    def test_run_completes_once_all_items_finalized(self):
        stores = make_stores()
        stores.platform_store.create("p1", "P", sort_order=1)
        stores.datasource_store.create("d1", "p1", "Only", "search index=a")
        run = vc.create_run(stores, created_by="admin", trigger_type="manual")
        executor = FakeSearchExecutor()

        vc.run_tick(stores, executor, lease_owner="member-a")  # dispatch
        sid = stores.queue_store.list_for_run(run.run_id)[0].sid
        from fakes import FakeValidationOutcome
        executor.poll_results[sid] = [FakeValidationOutcome(status=STATUS_PASS, result_count=1)]
        vc.run_tick(stores, executor, lease_owner="member-a")  # finalize

        self.assertEqual(stores.run_store.get(run.run_id).status, RUN_STATUS_COMPLETED)

    def test_disabled_datasource_is_skipped_at_dispatch_time(self):
        stores = make_stores()
        seed_basic(stores)
        vc.create_run(stores, created_by="admin", trigger_type="manual")
        stores.datasource_store.update_metadata("d1", enabled=False)

        executor = FakeSearchExecutor()
        vc.run_tick(stores, executor, lease_owner="member-a")

        self.assertEqual(executor.dispatch_calls, [])
        run_id = _only_run(stores)
        items = stores.queue_store.list_for_run(run_id)
        self.assertEqual(items[0].status, QUEUE_STATUS_CANCELLED)


def _only_run(stores):
    runs = stores.run_store.list()
    assert len(runs) == 1
    return runs[0].run_id


if __name__ == "__main__":
    unittest.main()
