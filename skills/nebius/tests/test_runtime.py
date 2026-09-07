"""Offline regression tests; stdlib only. No credentials or cloud access."""

import contextlib
import io
import json
import math
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "assets"))
from sdk import runtime
from sdk.inspection import Report


def resource(rid="resource-a", parent="project-a", name="example"):
    return NS(metadata=NS(id=rid, parent_id=parent, name=name, resource_version="v1"))


def response(value):
    return NS(wait=lambda: value)


def pages(*values):
    return Mock(side_effect=[response(value) for value in values])


class PaginationTests(unittest.TestCase):
    def test_all_pages_and_remaining_request_budget(self):
        method = pages(
            NS(items=[resource("a")], next_page_token="two"),
            NS(items=[resource("b")], next_page_token=""),
        )
        with patch.object(runtime.time, "monotonic", side_effect=[0, 1, 8]):
            items = runtime.collect_pages(method, lambda token: token, timeout=10)
        self.assertEqual([runtime.identity(x) for x in items], ["a", "b"])
        self.assertEqual([c.args[0] for c in method.call_args_list], ["", "two"])
        self.assertEqual([c.kwargs["timeout"] for c in method.call_args_list], [9, 2])

    def test_cycle_fails_instead_of_returning_partial_inventory(self):
        method = pages(
            NS(items=[resource("a")], next_page_token="same"),
            NS(items=[resource("b")], next_page_token="same"),
        )
        with self.assertRaisesRegex(runtime.CloudError, "PAGINATION_CYCLE"):
            runtime.collect_pages(method, str)

    def test_malformed_duplicate_missing_and_bounds(self):
        cases = [
            (NS(items=[], next_page_token=None), {}, "MALFORMED_PAGE"),
            (
                NS(items=[resource("a"), resource("a")], next_page_token=""),
                {},
                "DUPLICATE",
            ),
            (NS(items=[NS()], next_page_token=""), {}, "RESOURCE_ID_MISSING"),
            (
                NS(items=[resource("a"), resource("b")], next_page_token=""),
                {"max_items": 1},
                "INVENTORY_LIMIT",
            ),
            (NS(items=[], next_page_token="another"), {"max_pages": 1}, "PAGE_LIMIT"),
        ]
        for page, kwargs, code in cases:
            with (
                self.subTest(code=code),
                self.assertRaisesRegex(runtime.CloudError, code),
            ):
                runtime.collect_pages(pages(page), str, **kwargs)

    def test_failure_on_later_page_is_sanitized(self):
        method = Mock(
            side_effect=[
                response(NS(items=[resource()], next_page_token="two")),
                RuntimeError("secret-sentinel"),
            ]
        )
        report = Report({"project_id": "project-a"})
        report.collect("inventory", lambda: runtime.collect_pages(method, str))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(report.emit(as_json=True), 1)
        payload = json.loads(output.getvalue())
        self.assertFalse(payload["complete"])
        self.assertEqual(payload["data"], [])
        self.assertNotIn("secret-sentinel", output.getvalue())


class RuntimeTests(unittest.TestCase):
    def test_invalid_readback_factory_never_submits_a_create(self):
        import sdk.provision as helper

        request = NS(metadata=NS(parent_id="project-a", name="example"))
        with patch.object(helper, "submit") as submit, self.assertRaises(TypeError):
            helper.create_and_verify(Mock(), request, None, ready=lambda _: True)
        submit.assert_not_called()

    def test_invalid_readiness_settings_never_submit_a_create(self):
        import sdk.provision as helper

        for settings in [
            {"ready_timeout": 0, "ready": lambda _: True},
            {"ready_timeout": math.nan, "ready": lambda _: True},
            {"ready_timeout": 90, "ready": None},
        ]:
            client = Mock()
            client.get.return_value = response(resource())
            request = NS(metadata=NS(parent_id="project-a", name="example"))
            with (
                self.subTest(settings=settings),
                patch.object(
                    helper,
                    "submit",
                    return_value=runtime.MutationResult("operation-a", "resource-a"),
                ) as submit,
                self.assertRaises(
                    TypeError if settings["ready"] is None else ValueError
                ),
            ):
                helper.create_and_verify(
                    client, request, lambda **kw: NS(**kw), **settings
                )
            submit.assert_not_called()

    def test_invalid_timeouts_fail_before_request(self):
        for timeout in [0, -1, math.inf, math.nan]:
            method = Mock()
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                runtime.rpc(method, None, timeout=timeout)
            method.assert_not_called()

    def test_exact_identity_not_name_only(self):
        for kwargs in [
            {"parent_id": "wrong"},
            {"parent_id": "project-a", "resource_id": "wrong"},
            {"parent_id": "project-a", "name": "wrong"},
        ]:
            with self.subTest(kwargs=kwargs), self.assertRaises(runtime.CloudError):
                runtime.verify_identity(resource(), **kwargs)

    def test_mutation_is_single_submission_and_failure_retains_identity(self):
        op = NS(
            id="operation-a",
            resource_id="resource-a",
            sync_wait=Mock(side_effect=TimeoutError("secret-sentinel")),
        )
        method = Mock(return_value=response(op))
        with self.assertRaises(runtime.CloudError) as caught:
            runtime.submit(method, NS(), timeout=30)
        method.assert_called_once()
        self.assertEqual(method.call_args.kwargs["retries"], 1)
        self.assertEqual(caught.exception.operation_id, "operation-a")
        self.assertEqual(caught.exception.resource_id, "resource-a")
        self.assertEqual(caught.exception.outcome, "reconcile-required")
        self.assertNotIn("secret-sentinel", str(caught.exception))

    def test_operation_success_is_distinct_from_ready(self):
        op = NS(
            id="operation-a",
            resource_id="resource-a",
            sync_wait=Mock(),
            done=lambda: True,
            successful=lambda: True,
        )
        result = runtime.submit(Mock(return_value=response(op)), NS())
        self.assertEqual(result.resource_id, "resource-a")
        read = Mock(return_value=resource())
        with (
            patch.object(runtime.time, "monotonic", side_effect=[0, 0, 1, 5]),
            patch.object(runtime.time, "sleep"),
            self.assertRaisesRegex(runtime.CloudError, "READINESS_TIMEOUT"),
        ):
            runtime.wait_ready(read, lambda item: False, timeout=2)
        read.assert_called_once_with(2)

    def test_timeout_retains_identity_learned_during_polling(self):
        op = NS(id="operation-a", resource_id="")

        def fail_after_progress(**kwargs):
            op.resource_id = "learned-resource"
            raise TimeoutError("secret-sentinel")

        op.sync_wait = fail_after_progress
        with self.assertRaises(runtime.CloudError) as caught:
            runtime.submit(Mock(return_value=response(op)), NS())
        self.assertEqual(caught.exception.resource_id, "learned-resource")

    def test_cleanup_failure_preserves_primary_mutation_error(self):
        client = Mock()
        client.sync_close.side_effect = TimeoutError("secret-sentinel")
        primary = runtime.CloudError(
            "READINESS_TIMEOUT",
            resource_id="created-resource",
            operation_id="operation-a",
            outcome="reconcile-required",
        )
        with (
            patch.object(runtime, "init_nebius_sdk", return_value=client),
            self.assertRaises(runtime.CloudError) as caught,
            runtime.owned_sdk(parent_id="project-a"),
        ):
            raise primary
        self.assertIs(caught.exception, primary)
        self.assertEqual(caught.exception.resource_id, "created-resource")
        self.assertEqual(caught.exception.cleanup_error_code, "DEADLINE_EXCEEDED")

    def test_cleanup_failure_without_primary_is_sanitized(self):
        client = Mock()
        client.sync_close.side_effect = TimeoutError("secret-sentinel")
        with (
            patch.object(runtime, "init_nebius_sdk", return_value=client),
            self.assertRaisesRegex(runtime.CloudError, "SDK_CLOSE_FAILED"),
            runtime.owned_sdk(parent_id="project-a"),
        ):
            pass

    def test_context_closes_owned_client_after_failure(self):
        client = Mock()
        with (
            patch.object(runtime, "init_nebius_sdk", return_value=client),
            self.assertRaisesRegex(RuntimeError, "example"),
            runtime.owned_sdk(parent_id="project-a"),
        ):
            raise RuntimeError("example")
        client.sync_close.assert_called_once_with(timeout=10.0)

    def test_explicit_auth_selection_disables_ambient_overrides(self):
        config, sdk = Mock(), Mock()
        modules = {
            "nebius.aio.cli_config": NS(Config=config),
            "nebius.sdk": NS(SDK=sdk),
        }
        with patch.dict(sys.modules, modules):
            runtime.init_nebius_sdk(parent_id="project-a", profile="selected")
            self.assertTrue(config.call_args.kwargs["no_env"])
            self.assertTrue(config.call_args.kwargs["no_parent_id"])
            self.assertEqual(sdk.call_args.kwargs["parent_id"], "project-a")
            self.assertTrue(
                sdk.call_args.kwargs["federation_invitation_no_browser_open"]
            )
            runtime.init_nebius_sdk(parent_id="project-a")
            self.assertFalse(config.call_args.kwargs["no_env"])
            with self.assertRaises(ValueError):
                runtime.init_nebius_sdk(
                    parent_id="project-a", profile="a", credentials_file=Path("b")
                )

    def test_json_is_lossless_while_screen_is_bounded(self):
        report = Report(
            {"project_id": "project-a"}, data=[{"id": str(i)} for i in range(50)]
        )
        for as_json in (True, False):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(report.emit(as_json=as_json), 0)
            if as_json:
                self.assertEqual(len(json.loads(output.getvalue())["data"]), 50)
            else:
                self.assertEqual(len(output.getvalue().splitlines()), 22)
                self.assertIn("Showing 20", output.getvalue())


if __name__ == "__main__":
    unittest.main()
