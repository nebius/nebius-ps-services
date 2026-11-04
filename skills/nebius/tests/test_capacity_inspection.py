"""SDK-backed offline capacity advice tests; no credentials or cloud access."""

import contextlib
import io
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import inspect_capacity as collector
import inspect_quotas
from nebius.api.nebius.capacity.v1 import (
    ComputeInstanceDetails,
    ListResourceAdviceResponse,
    ResourceAdvice,
    ResourceAdviceSpec,
    ResourceAdviceStatus,
)
from nebius.api.nebius.common.v1 import ResourceMetadata
from sdk.runtime import CloudError

A = ResourceAdviceStatus.Availability
WHEN = datetime(2026, 1, 1, tzinfo=timezone.utc)


def advice(
    *, region="region-a", fabric="", preset="example-preset", state=None, available=0
):
    return ResourceAdvice(
        spec=ResourceAdviceSpec(
            region=region,
            fabric=fabric,
            compute_instance=ComputeInstanceDetails(
                platform="example-gpu",
                preset=ComputeInstanceDetails.Preset(name=preset),
            ),
        ),
        status=ResourceAdviceStatus(
            on_demand=A(
                data_state=state if state is not None else A.DataState.DATA_STATE_FRESH,
                effective_at=WHEN,
                available=available,
                limit=10,
                availability_level=A.AvailabilityLevel.AVAILABILITY_LEVEL_HIGH,
            )
        ),
    )


class CapacityInspectionTests(unittest.TestCase):
    def test_tenant_only_quota_invocation_never_collects_a_project(self):
        with (
            patch.object(
                sys,
                "argv",
                ["inspect_quotas", "--tenant-id", "tenant-a", "--raw", "--json"],
            ),
            patch.object(inspect_quotas, "init_nebius_sdk") as init,
            patch.object(inspect_quotas, "_list_quotas", return_value={}) as collect,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            result = inspect_quotas.main()
        self.assertEqual(result, 0)
        self.assertEqual(init.call_args.kwargs["parent_id"], "tenant-a")
        collect.assert_called_once_with(
            init.return_value, parent_id="tenant-a", scope="tenant"
        )
        payload = json.loads(output.getvalue())
        self.assertIsNone(payload["scope"]["project_id"])
        self.assertEqual(payload["scope"]["regions"], [])
        self.assertEqual(payload["mode"], "raw")

    def test_fresh_zero_is_measured_but_absent_modes_are_unknown(self):
        rows = collector.advice_rows([advice()], tenant_id="tenant-a", regions=set())
        self.assertEqual(rows[0]["available_vm_count"], 0)
        self.assertEqual(rows[0]["measurement"], "fresh")
        self.assertEqual(rows[0]["effective_at"], WHEN.isoformat())
        for row in rows[1:]:
            self.assertIsNone(row["available_vm_count"])
            self.assertIsNone(row["limit_vm_count"])

    def test_stale_unknown_and_missing_timestamp_do_not_become_zero(self):
        for state in (
            A.DataState.DATA_STATE_STALE,
            A.DataState.DATA_STATE_UNKNOWN,
            A.DataState.DATA_STATE_UNSPECIFIED,
        ):
            with self.subTest(state=state):
                row = collector.advice_rows(
                    [advice(state=state)], tenant_id="tenant-a", regions=set()
                )[0]
                self.assertIsNone(row["available_vm_count"])
                self.assertEqual(row["measurement"], "unresolved")
        item = advice()
        item.status.on_demand.effective_at = None
        self.assertIsNone(
            collector.advice_rows([item], tenant_id="tenant-a", regions=set())[0][
                "available_vm_count"
            ]
        )

    def test_alternative_presets_and_allocations_are_never_summed(self):
        item = advice(available=5)
        item.status.reserved = A(
            data_state=A.DataState.DATA_STATE_FRESH, effective_at=WHEN, available=2
        )
        rows = collector.advice_rows(
            [item, advice(preset="other", available=5)],
            tenant_id="tenant-a",
            regions=set(),
        )
        self.assertEqual(len(rows), 6)
        self.assertEqual(
            [row["available_vm_count"] for row in rows], [5, None, 2, 5, None, None]
        )
        self.assertEqual({row["preset"] for row in rows}, {"example-preset", "other"})

    def test_exact_region_filter_and_no_matching_rows(self):
        items = [advice(), advice(region="region-b")]
        rows = collector.advice_rows(items, tenant_id="tenant-a", regions={"region-b"})
        self.assertEqual({row["region"] for row in rows}, {"region-b"})
        self.assertEqual(
            collector.advice_rows(items, tenant_id="tenant-a", regions={"region-c"}), []
        )

    def test_invalid_key_and_conflicting_parent_fail_closed(self):
        for item in (advice(region=""), advice(preset="")):
            with self.assertRaises(CloudError):
                collector.advice_key(item)
        item = advice()
        item.metadata = ResourceMetadata(parent_id="tenant-other")
        with self.assertRaisesRegex(CloudError, "RESOURCE_IDENTITY_MISMATCH"):
            collector.advice_rows([item], tenant_id="tenant-a", regions=set())

    def test_sdk_pagination_uses_composite_identity_and_explicit_tenant(self):
        pages = [
            ListResourceAdviceResponse(items=[advice()], next_page_token="next"),
            ListResourceAdviceResponse(items=[advice(region="region-b")]),
        ]
        client = Mock()
        client.list.side_effect = [Mock(wait=Mock(return_value=page)) for page in pages]
        with patch(
            "nebius.api.nebius.capacity.v1.ResourceAdviceServiceClient",
            return_value=client,
        ):
            rows = collector.inspect_capacity(
                object(), tenant_id="tenant-a", regions=set()
            )
        self.assertEqual(len(rows), 6)
        requests = [call.args[0] for call in client.list.call_args_list]
        self.assertEqual([r.parent_id for r in requests], ["tenant-a", "tenant-a"])
        self.assertEqual([r.page_token for r in requests], ["", "next"])
        for request in requests:
            self.assertIsInstance(request.SerializeToString(), bytes)
        self.assertNotIn("region", requests[0].to_json())

    def test_duplicate_advice_and_pagination_cycles_are_incomplete(self):
        for pages, code in (
            (
                [
                    ListResourceAdviceResponse(
                        items=[advice()], next_page_token="next"
                    ),
                    ListResourceAdviceResponse(items=[advice()]),
                ],
                "DUPLICATE_OR_MISSING_IDENTITY",
            ),
            (
                [
                    ListResourceAdviceResponse(
                        items=[advice()], next_page_token="next"
                    ),
                    ListResourceAdviceResponse(
                        items=[advice(region="region-b")], next_page_token="next"
                    ),
                ],
                "PAGINATION_CYCLE",
            ),
        ):
            with self.subTest(code=code):
                client = Mock()
                client.list.side_effect = [
                    Mock(wait=Mock(return_value=page)) for page in pages
                ]
                with (
                    patch(
                        "nebius.api.nebius.capacity.v1.ResourceAdviceServiceClient",
                        return_value=client,
                    ),
                    self.assertRaisesRegex(CloudError, code),
                ):
                    collector.inspect_capacity(
                        object(), tenant_id="tenant-a", regions=set()
                    )

    def test_failure_after_first_page_cannot_claim_complete_inventory(self):
        client = Mock()
        client.list.side_effect = [
            Mock(
                wait=Mock(
                    return_value=ListResourceAdviceResponse(
                        items=[advice()], next_page_token="next"
                    )
                )
            ),
            CloudError("PERMISSION_DENIED"),
        ]
        with (
            patch(
                "nebius.api.nebius.capacity.v1.ResourceAdviceServiceClient",
                return_value=client,
            ),
            self.assertRaisesRegex(CloudError, "PERMISSION_DENIED"),
        ):
            collector.inspect_capacity(object(), tenant_id="tenant-a", regions=set())

    def test_cli_scope_profile_and_sanitized_failure(self):
        with (
            patch.object(collector, "owned_sdk") as owned,
            patch.object(
                collector,
                "inspect_capacity",
                side_effect=RuntimeError("sensitive provider error"),
            ),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            result = collector.main(
                ["--tenant-id", "tenant-a", "--profile", "selected", "--json"]
            )
        self.assertEqual(result, 1)
        payload = json.loads(output.getvalue())
        self.assertFalse(payload["complete"])
        self.assertFalse(payload["scope"]["counts_additive"])
        self.assertEqual(payload["scope"]["regions"], [])
        self.assertNotIn("sensitive", output.getvalue())
        self.assertEqual(owned.call_args.kwargs["parent_id"], "tenant-a")
        self.assertEqual(owned.call_args.kwargs["profile"], "selected")

    def test_invalid_arguments_never_construct_sdk(self):
        for args in (
            ["--tenant-id", ""],
            ["--tenant-id", "tenant-a", "--region", "all"],
            ["--project-id", "project-a"],
        ):
            with (
                self.subTest(args=args),
                patch.object(collector, "owned_sdk") as owned,
                contextlib.redirect_stderr(io.StringIO()),
            ):
                with self.assertRaises(SystemExit) as caught:
                    collector.main(args)
                self.assertEqual(caught.exception.code, 2)
                owned.assert_not_called()


if __name__ == "__main__":
    unittest.main()
