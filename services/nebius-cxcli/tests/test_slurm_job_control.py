from __future__ import annotations

import asyncio
import threading
from collections.abc import Mapping
from typing import Any

import pytest
from rich.console import Console
from rich.text import Text
from textual.widgets import DataTable, Footer, Static

from nebius_cxcli.slurm_action_journal import (
    SLURM_ACTION_APPLIED,
    SlurmJobActionBinding,
    enqueue_slurm_action,
    new_slurm_action_journal,
    record_slurm_controller_observation,
    record_slurm_login_observation,
    transition_slurm_action,
)
from nebius_cxcli.slurm_job_control import (
    SLURM_JOB_CONTROL_ACTION_PROMPT,
    SLURM_JOB_CONTROL_EXIT,
    SLURM_JOB_CONTROL_HELP,
    SLURM_JOB_CONTROL_JOBS_CHANGED,
    SLURM_JOB_CONTROL_JOBS_CLEARED,
    SLURM_JOB_CONTROL_KEYS,
    SLURM_JOB_CONTROL_WAIT_COMPLETED,
    SlurmJobControlRefreshError,
    build_slurm_jobs_table,
    create_slurm_job_control_app,
    normalize_slurm_job_control_action,
    prompt_slurm_job_control,
    slurm_job_control_action_result,
    slurm_job_control_controller_status,
    slurm_job_control_pending_selected_ids,
    slurm_job_control_selected_ids_for_action,
    slurm_table_value,
)
from nebius_cxcli.slurm_jobs import (
    SLURM_PARTITION_CUSTOMER_OWNED_FIELDS,
    SLURM_PARTITION_DERIVED_TOPOLOGY_FIELDS,
    AffectedSlurmJob,
    parse_scontrol_show_partition_states,
    parse_squeue_jobs,
    slurm_job_is_active,
    slurm_job_is_terminating,
    slurm_partition_owned_fields_match,
    slurm_partition_pause_records,
    slurm_partitions_overlapping_nodes,
    slurm_remaining_seconds,
)


def _job(
    job_id: str,
    *,
    state: str = "RUNNING",
    allocated_nodes: str = "worker-0",
    reason: str = "",
    remaining: str = "30:00",
    impact_scope: str = "allocated-node",
) -> AffectedSlurmJob:
    return AffectedSlurmJob(
        job_id=job_id,
        user="root",
        state=state,
        partition="main",
        allocated_nodes=allocated_nodes,
        requested_nodes="",
        scheduled_nodes="",
        reason=reason,
        elapsed="00:05",
        limit="35:00",
        remaining=remaining,
        name="sop-gpu-job-test-01",
        impact_scope=impact_scope,
    )


def _binding(job_id: str) -> SlurmJobActionBinding:
    return SlurmJobActionBinding(
        job_id=job_id,
        user_id="root(0)",
        submit_time="2026-07-12T10:00:00",
        restart_baseline=0,
        identity_fingerprint=f"identity-{job_id}",
        lineage_fingerprint=f"lineage-{job_id}",
    )


def _journal_provider(journal: Mapping[str, Any]):
    return lambda: journal


def test_slurm_job_control_action_keys_reserve_a_for_select_all() -> None:
    assert normalize_slurm_job_control_action("c") == "cancel"
    assert normalize_slurm_job_control_action("q") == "requeue"
    assert normalize_slurm_job_control_action("h") == "hold"
    assert normalize_slurm_job_control_action("H") == "requeue-hold"
    assert normalize_slurm_job_control_action("u") == "release"
    assert normalize_slurm_job_control_action("w") == "wait"
    assert normalize_slurm_job_control_action("r") == "refresh"
    for removed_binding in ("C", "Q", "b", "x", "a", "i"):
        assert normalize_slurm_job_control_action(removed_binding) == ""


def test_slurm_partition_pause_records_only_up_partitions() -> None:
    states = parse_scontrol_show_partition_states(
        "\n".join(
            (
                "PartitionName=main State=UP Nodes=worker-[0-1]",
                "PartitionName=debug State=DOWN* Nodes=debug-0",
                "PartitionName=login State=DRAIN Nodes=login-0",
            )
        )
    )

    records = slurm_partition_pause_records(
        partitions=("debug", "main", "login"),
        states=states,
    )

    assert len(records) == 1
    record = records[0]
    assert record.partition == "main"
    assert record.previous_state == "UP"
    assert record.applied_state == "DOWN"
    assert record.previous_record == "Nodes=worker-[0-1] PartitionName=main State=UP"
    assert len(record.previous_record_fingerprint) == 64
    assert record.applied_record == ""
    assert record.applied_record_fingerprint == ""


def test_slurm_partition_pause_records_fail_closed_for_unknown_partition() -> None:
    states = parse_scontrol_show_partition_states("PartitionName=main State=UP Nodes=worker-0")

    with pytest.raises(RuntimeError, match="Could not inspect Slurm partition `gpu`"):
        slurm_partition_pause_records(partitions=("gpu",), states=states)


def test_slurm_partition_pause_ownership_ignores_only_known_derived_topology() -> None:
    source = (
        "PartitionName=gpu State=DOWN Nodes=worker-[0-1] MaxTime=01:00:00 "
        "TotalNodes=2 TotalCPUs=224 TRES=cpu=224,gres/gpu=16,node=2 NodeIndices=0-1"
    )
    target = (
        "PartitionName=gpu State=DOWN Nodes=worker-[0-1] MaxTime=01:00:00 "
        "TotalNodes=4 TotalCPUs=448 TRES=cpu=448,gres/gpu=32,node=4 NodeIndices=8-11"
    )

    assert {
        "NodeIndices",
        "TotalCPUs",
        "TotalNodes",
        "TRES",
    } == SLURM_PARTITION_DERIVED_TOPOLOGY_FIELDS
    assert {"MaxTime", "Nodes", "PartitionName", "State"}.issubset(
        SLURM_PARTITION_CUSTOMER_OWNED_FIELDS
    )
    assert slurm_partition_owned_fields_match(source, target)
    assert not slurm_partition_owned_fields_match(
        source,
        target.replace("MaxTime=01:00:00", "MaxTime=02:00:00"),
    )
    assert not slurm_partition_owned_fields_match(source, target + " FutureField=target")
    assert not slurm_partition_owned_fields_match(
        source,
        target.replace("State=DOWN", "State=UP"),
    )
    assert slurm_partition_owned_fields_match(
        source,
        target.replace("State=DOWN", "State=UP"),
        include_state=False,
    )


def test_slurm_partition_pause_observation_rejects_unknown_field_drift() -> None:
    before = parse_scontrol_show_partition_states(
        "PartitionName=gpu State=UP Nodes=worker-0 FutureField=source"
    )
    record = slurm_partition_pause_records(partitions=("gpu",), states=before)[0]
    applied = parse_scontrol_show_partition_states(
        "PartitionName=gpu State=DOWN Nodes=worker-0 FutureField=target"
    )[0]

    with pytest.raises(ValueError, match="customer-owned or unknown field"):
        record.with_applied_observation(applied)


def test_slurm_partitions_overlapping_nodes_include_hidden_and_background() -> None:
    states = parse_scontrol_show_partition_states(
        "\n".join(
            (
                "PartitionName=main State=UP Nodes=worker-[0-1]",
                "PartitionName=hidden State=UP Nodes=worker-[0-1] Hidden=YES",
                "PartitionName=background State=UP Nodes=worker-[0-1] Hidden=YES",
                "PartitionName=login State=UP Nodes=login-0",
            )
        )
    )

    assert slurm_partitions_overlapping_nodes(
        states=states,
        node_names=("worker-0",),
        fallback_partitions=("main",),
    ) == ("main", "hidden", "background")


def test_slurm_partitions_overlapping_nodes_preserve_fallback_for_missing_nodes_metadata() -> None:
    states = parse_scontrol_show_partition_states(
        "\n".join(
            (
                "PartitionName=main State=UP Nodes=worker-0",
                "PartitionName=debug State=UP Nodes=(null)",
            )
        )
    )

    assert slurm_partitions_overlapping_nodes(
        states=states,
        node_names=("worker-1",),
        fallback_partitions=("debug",),
    ) == ("debug",)


def test_slurm_job_control_actions_preserve_the_exact_displayed_selection() -> None:
    active = _job("41")
    pending = _job(
        "44",
        state="PENDING",
        allocated_nodes="",
        reason="Priority",
        impact_scope="pending-partition",
    )
    jobs = (active, pending)

    assert slurm_job_control_selected_ids_for_action("cancel", jobs, ("missing", "44", "41")) == (
        "44",
        "41",
    )
    assert slurm_job_control_selected_ids_for_action("wait", jobs, ()) == ("41", "44")
    assert slurm_job_control_selected_ids_for_action("refresh", jobs, ()) == ("41", "44")
    assert slurm_job_control_pending_selected_ids(jobs, ("41", "44")) == ("44",)


def test_slurm_job_control_treats_completing_as_terminating_active_job() -> None:
    job = _job("8", state="COMPLETING")

    assert slurm_job_is_active(job)
    assert slurm_job_is_terminating(job)


def test_slurm_jobs_table_renders_aligned_columns_and_selection_marks() -> None:
    jobs = (
        _job("41"),
        _job("44", state="PENDING", allocated_nodes="", reason="Priority"),
    )
    table = build_slurm_jobs_table(
        jobs,
        title="Affected Slurm jobs",
        selected_job_ids=("44",),
        include_selection=True,
    )

    assert [column.header for column in table.columns][:5] == [
        "Sel",
        "Job",
        "User",
        "State",
        "Partition",
    ]
    console = Console(record=True, width=180, color_system=None)
    console.print(table)
    rendered = console.export_text()
    assert "Affected Slurm jobs" in rendered
    assert "[x]" in rendered
    assert "Priority" in rendered


@pytest.mark.parametrize(
    "sentinel",
    ("", "-", "(null)", "(NULL)", "N/A", "none", "not_set", "unlimited"),
)
def test_slurm_table_value_normalizes_empty_slurm_sentinels(sentinel: str) -> None:
    assert slurm_table_value(sentinel) == "-"


def test_slurm_job_control_normalizes_live_squeue_sentinels() -> None:
    jobs = parse_squeue_jobs(
        "10|root|RUNNING|main|worker-1|N/A|(null)|NONE|00:05|35:00|30:00|job-10",
        impact_scope="allocated-node",
    )

    assert jobs[0].allocated_nodes == "worker-1"
    assert jobs[0].requested_nodes == ""
    assert jobs[0].scheduled_nodes == ""
    assert jobs[0].reason == ""

    async def _run() -> tuple[str, tuple[str, ...]]:
        app = create_slurm_job_control_app(jobs, title="Affected Slurm jobs")
        async with app.run_test(size=(160, 40)):
            table = app.query_one("#jobs", DataTable)
            title = app.query_one("#title", Static)
            row = table.get_row("10")
            return str(title.content), tuple(str(row[index]) for index in range(5, 9))

    assert asyncio.run(_run()) == (
        "Affected Slurm jobs",
        ("worker-1", "-", "-", "-"),
    )


def test_slurm_job_control_textual_app_marks_completing_jobs() -> None:
    async def _run():
        app = create_slurm_job_control_app(
            (_job("8", state="COMPLETING"),),
            title="Affected Slurm jobs",
        )
        async with app.run_test(size=(160, 40)):
            table = app.query_one("#jobs", DataTable)
            status = app.query_one("#status", Static)
            headers = [column.label.plain for column in table.columns.values()]
            return table.get_row("8")[3], str(status.content), headers

    state_cell, status_text, headers = asyncio.run(_run())

    assert headers == [
        "Sel",
        "Job",
        "User",
        "State",
        "Partition",
        "Allocated",
        "Requested",
        "Scheduled",
        "Reason",
        "Elapsed",
        "Limit",
        "Remaining",
        "Scope",
        "Name",
        "Action result",
    ]
    assert isinstance(state_cell, Text)
    assert state_cell.plain == "COMPLETING"
    assert "yellow" in str(state_cell.style)
    assert "1 completing" in status_text
    assert "still freeing nodes" in status_text


def test_slurm_job_control_text_fallback_uses_visible_action_keys() -> None:
    jobs = (
        _job("41"),
        _job("44", state="PENDING", allocated_nodes="", reason="Priority"),
    )
    prompts: list[tuple[str, str, bool]] = []
    answers = iter(["44", "c"])

    def _prompt(message: str, default: str, show_default: bool) -> str:
        prompts.append((message, default, show_default))
        return next(answers)

    action, selected = prompt_slurm_job_control(
        jobs,
        console=Console(record=True, width=180, color_system=None),
        table_title="Affected Slurm jobs",
        is_tty=False,
        text_prompt=_prompt,
    )

    assert action == "cancel"
    assert selected == ("44",)
    assert prompts[1][0] == SLURM_JOB_CONTROL_ACTION_PROMPT
    assert prompts[1][1:] == ("r", True)


def test_slurm_job_control_tui_failure_falls_back_to_text_prompt() -> None:
    jobs = (_job("41"),)
    answers = iter(["41", "q"])

    def _prompt(_message: str, _default: str, _show_default: bool) -> str:
        return next(answers)

    def _broken_tui(_jobs, title: str):
        assert title == "Affected Slurm jobs"
        raise RuntimeError("no terminal")

    console = Console(record=True, width=180, color_system=None)
    action, selected = prompt_slurm_job_control(
        jobs,
        console=console,
        table_title="Affected Slurm jobs",
        is_tty=True,
        text_prompt=_prompt,
        tui_runner=_broken_tui,
    )

    assert action == "requeue"
    assert selected == ("41",)
    assert "Interactive TUI unavailable" in console.export_text()


def test_slurm_job_control_tui_close_does_not_fall_back_to_stale_text_snapshot() -> None:
    prompts: list[str] = []

    def _prompt(message: str, _default: str, _show_default: bool) -> str:
        prompts.append(message)
        return ""

    action, selected = prompt_slurm_job_control(
        (_job("41"),),
        console=Console(record=True, width=180, color_system=None),
        table_title="Affected Slurm jobs",
        is_tty=True,
        text_prompt=_prompt,
        tui_runner=lambda _jobs, _title: None,
    )

    assert action == SLURM_JOB_CONTROL_EXIT
    assert selected == ()
    assert prompts == []


def test_slurm_job_control_refresh_failure_does_not_fall_back_to_stale_prompt() -> None:
    prompts: list[str] = []

    def _prompt(message: str, _default: str, _show_default: bool) -> str:
        prompts.append(message)
        return ""

    def _broken_refresh(_jobs, _title: str):
        raise SlurmJobControlRefreshError("squeue failed")

    with pytest.raises(SlurmJobControlRefreshError, match="squeue failed"):
        prompt_slurm_job_control(
            (_job("41"),),
            console=Console(record=True, width=180, color_system=None),
            table_title="Affected Slurm jobs",
            is_tty=True,
            text_prompt=_prompt,
            tui_runner=_broken_refresh,
        )

    assert prompts == []


def test_slurm_job_control_textual_app_returns_selected_action() -> None:
    async def _run():
        app = create_slurm_job_control_app(
            (_job("41"), _job("44", state="PENDING")),
            title="Affected Slurm jobs",
        )
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.press("space")
            await pilot.press("c")
        return app.return_value

    result = asyncio.run(_run())

    assert result.action == "cancel"
    assert result.selected_job_ids == ("41",)


def test_slurm_job_control_textual_app_uses_visible_selection_mark() -> None:
    async def _run():
        app = create_slurm_job_control_app(
            (_job("41"),),
            title="Affected Slurm jobs",
        )
        async with app.run_test(size=(160, 40)) as pilot:
            table = app.query_one("#jobs", DataTable)
            unselected = table.get_row("41")[0]
            await pilot.press("space")
            selected = table.get_row("41")[0]
            return unselected, selected

    unselected, selected = asyncio.run(_run())

    assert isinstance(unselected, Text)
    assert isinstance(selected, Text)
    assert unselected.plain == "---"
    assert selected.plain == "YES"
    assert "white" in str(selected.style)
    assert "green" in str(selected.style)


def test_slurm_job_control_textual_app_uses_single_concise_legend() -> None:
    async def _run():
        app = create_slurm_job_control_app(
            (_job("41"),),
            title="Affected Slurm jobs",
        )
        async with app.run_test(size=(160, 40)):
            keys_widget = app.query_one("#keys", Static)
            return str(keys_widget.content), len(list(app.query(Footer)))

    keys_text, footer_count = asyncio.run(_run())

    assert footer_count == 0
    assert "Keys:" in keys_text
    assert "c cancel" in keys_text
    assert "q requeue" in keys_text
    assert "h hold" in keys_text
    assert "H requeue-and-hold" in keys_text
    assert "u release" in keys_text
    assert "? help" in keys_text
    assert keys_text == SLURM_JOB_CONTROL_KEYS


def test_slurm_job_control_textual_help_opens_modal_overlay() -> None:
    async def _run():
        app = create_slurm_job_control_app(
            tuple(_job(str(index)) for index in range(20)),
            title="Affected Slurm jobs",
        )
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.press("?")
            await pilot.pause(0.1)
            help_text = app.screen.query_one("#help-text", Static)
            help_dialog_count = len(list(app.screen.query("#help-dialog")))
            table = app.query_one("#jobs", DataTable)
            await pilot.press("escape")
            await pilot.pause(0.1)
            return (
                str(help_text.content),
                help_dialog_count,
                table.row_count,
                len(list(app.screen.query("#help-dialog"))),
            )

    help_text, help_dialog_count, row_count, closed_count = asyncio.run(_run())

    assert help_dialog_count == 1
    assert row_count == 20
    assert closed_count == 0
    assert "Requeue stops the current execution" in help_text
    assert "Slurm may show a job as COMPLETING" in help_text
    assert "u: release selected held jobs" in help_text
    assert "durably queues actions" in help_text
    assert help_text == SLURM_JOB_CONTROL_HELP


def test_slurm_job_control_textual_app_ticks_remaining_time() -> None:
    async def _run():
        app = create_slurm_job_control_app(
            (_job("41", remaining="0:05"),),
            title="Affected Slurm jobs",
        )
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause(1.25)
            table = app.query_one("#jobs", DataTable)
            return table.get_row("41")[11]

    remaining = asyncio.run(_run())

    assert isinstance(remaining, str)
    seconds = slurm_remaining_seconds(remaining)
    assert seconds is not None
    assert seconds < 5


def test_slurm_job_control_textual_wait_stays_in_app_until_jobs_clear() -> None:
    snapshots = [
        (_job("41", remaining="0:03"),),
        (),
    ]

    def _jobs_provider() -> tuple[AffectedSlurmJob, ...]:
        return snapshots.pop(0) if snapshots else ()

    async def _run():
        app = create_slurm_job_control_app(
            (_job("41", remaining="0:04"),),
            title="Affected Slurm jobs",
            jobs_provider=_jobs_provider,
            wait_timeout_seconds=5,
            poll_interval_seconds=1,
        )
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.press("w")
            assert app.return_value is None
            await pilot.pause(2.25)
        return app.return_value

    result = asyncio.run(_run())

    assert result is not None
    assert result.action == SLURM_JOB_CONTROL_WAIT_COMPLETED
    assert result.selected_job_ids == ()


def test_slurm_job_control_formats_controller_and_latest_action_status() -> None:
    journal = new_slurm_action_journal()
    record_slurm_controller_observation(
        journal,
        authority="cxcli-bridge-0",
        connectivity="unavailable",
        authority_epoch="bridge-source-v1",
        snapshot_age_seconds=12,
    )
    record_slurm_login_observation(
        journal,
        state="pending-voluntary-exit",
        protected_pod_count=1,
        active_session_count=1,
        target_ready=True,
    )
    enqueue_slurm_action(
        journal,
        kind="cancel",
        binding=_binding("41"),
        intended_postcondition={"absent": True},
    )

    assert slurm_job_control_controller_status(journal) == (
        "Authority cxcli-bridge-0 | Connectivity unavailable | Snapshot age 12s | "
        "Broker accept-only | Login pending-voluntary-exit (1 protected Pod(s), "
        "1 active session(s), target ready)"
    )
    assert slurm_job_control_action_result(journal, "41") == "cancel: Queued"
    assert slurm_job_control_action_result(journal, "missing") == "-"


def test_slurm_job_control_action_result_uses_current_snapshot_lineage() -> None:
    journal = new_slurm_action_journal()
    old_binding = _binding("41")
    old_action = enqueue_slurm_action(
        journal,
        kind="cancel",
        binding=old_binding,
        intended_postcondition={"absent": True},
    )
    transition_slurm_action(
        journal,
        action_id=old_action["action_id"],
        state="Rejected",
        result="old job completed",
    )
    new_binding = SlurmJobActionBinding(
        job_id="41",
        user_id="other(1001)",
        submit_time="2026-07-12T11:00:00",
        restart_baseline=0,
        identity_fingerprint="identity-41-reused",
        lineage_fingerprint="lineage-41-reused",
    )
    journal["job_snapshots"] = {
        "41": {
            "binding": new_binding.as_payload(),
        }
    }

    assert slurm_job_control_action_result(journal, "41") == "-"

    enqueue_slurm_action(
        journal,
        kind="hold",
        binding=new_binding,
        intended_postcondition={"held": True},
    )

    assert slurm_job_control_action_result(journal, "41") == "hold: Queued"


def test_slurm_job_control_shows_exact_pending_ssh_exit_fingerprint() -> None:
    journal = new_slurm_action_journal()
    fingerprint = "a" * 64
    record_slurm_login_observation(
        journal,
        state="indeterminate",
        protected_pod_count=1,
        active_session_count=1,
        target_ready=True,
        exit_confirmation_requests=(
            {
                "socket_fingerprint": fingerprint,
                "absence_observed_at": "2026-07-12T10:02:00Z",
            },
        ),
    )

    assert (
        f"Explicit SSH-exit confirmation required: {fingerprint}"
        in slurm_job_control_controller_status(journal)
    )


def test_slurm_job_control_textual_refreshes_durable_action_result() -> None:
    journal = new_slurm_action_journal()
    action = enqueue_slurm_action(
        journal,
        kind="cancel",
        binding=_binding("41"),
        intended_postcondition={"absent": True},
    )

    async def _run() -> str:
        app = create_slurm_job_control_app(
            (_job("41"),),
            title="Affected Slurm jobs",
            action_journal_provider=_journal_provider(journal),
        )
        async with app.run_test(size=(160, 40)) as pilot:
            transition_slurm_action(
                journal,
                action_id=action["action_id"],
                state="Dispatching",
                authority_epoch="bridge-source-v1",
            )
            transition_slurm_action(
                journal,
                action_id=action["action_id"],
                state=SLURM_ACTION_APPLIED,
                result="cancelled",
            )
            await pilot.pause(1.1)
            return str(app.query_one("#jobs", DataTable).get_row("41")[-1])

    assert asyncio.run(_run()) == "cancel: Applied - cancelled"


def test_slurm_job_control_textual_idle_poll_refreshes_rows_without_keypress() -> None:
    snapshots = [
        (_job("42", remaining="0:10"),),
    ]

    def _jobs_provider() -> tuple[AffectedSlurmJob, ...]:
        return snapshots.pop(0) if snapshots else (_job("42", remaining="0:10"),)

    async def _run():
        app = create_slurm_job_control_app(
            (_job("41", remaining="0:20"),),
            title="Affected Slurm jobs",
            jobs_provider=_jobs_provider,
            poll_interval_seconds=1,
        )
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause(1.5)
            table = app.query_one("#jobs", DataTable)
            return table.get_row("42")[1], app.return_value

    job_id, result = asyncio.run(_run())

    assert job_id == "42"
    assert result is None


def test_slurm_job_control_textual_idle_poll_exits_when_jobs_clear() -> None:
    def _jobs_provider() -> tuple[AffectedSlurmJob, ...]:
        return ()

    async def _run():
        app = create_slurm_job_control_app(
            (_job("41"),),
            title="Affected Slurm jobs",
            jobs_provider=_jobs_provider,
            poll_interval_seconds=1,
        )
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause(1.5)
        return app.return_value

    result = asyncio.run(_run())

    assert result is not None
    assert result.action == SLURM_JOB_CONTROL_JOBS_CLEARED


def test_slurm_job_control_textual_fast_scheduler_exits_when_jobs_change() -> None:
    def _jobs_provider() -> tuple[AffectedSlurmJob, ...]:
        return (_job("42", allocated_nodes="worker-1"),)

    async def _run():
        app = create_slurm_job_control_app(
            (_job("41", allocated_nodes="worker-0"), _job("42", allocated_nodes="worker-1")),
            title="Affected Slurm jobs",
            jobs_provider=_jobs_provider,
            poll_interval_seconds=1,
            exit_after_action=True,
        )
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause(1.5)
        return app.return_value

    result = asyncio.run(_run())

    assert result is not None
    assert result.action == SLURM_JOB_CONTROL_JOBS_CHANGED
    assert result.selected_job_ids == ()


def test_slurm_job_control_textual_controller_outage_keeps_tui_open() -> None:
    journal = new_slurm_action_journal()
    record_slurm_controller_observation(
        journal,
        authority="cxcli-bridge-0",
        connectivity="unavailable",
        authority_epoch="bridge-source-v1",
        snapshot_age_seconds=17,
    )

    def _jobs_provider() -> tuple[AffectedSlurmJob, ...]:
        raise RuntimeError("squeue failed")

    async def _run():
        app = create_slurm_job_control_app(
            (_job("41"),),
            title="Affected Slurm jobs",
            jobs_provider=_jobs_provider,
            action_journal_provider=_journal_provider(journal),
            wait_timeout_seconds=5,
            poll_interval_seconds=1,
        )
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.press("r")
            await pilot.pause(0.2)
            return (
                app.return_value,
                str(app.query_one("#status", Static).content),
                str(app.query_one("#controller-status", Static).content),
            )

    result, status, controller_status = asyncio.run(_run())

    assert result is None
    assert "Controller unavailable; durable actions remain queued" in status
    assert "Connectivity unavailable" in controller_status
    assert "Snapshot age 17s" in controller_status


def test_slurm_job_control_textual_callback_runs_action_and_refreshes_in_place() -> None:
    actions: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []

    def _action_handler(
        action: str,
        selected: tuple[str, ...],
        displayed_jobs: tuple[AffectedSlurmJob, ...],
    ) -> tuple[AffectedSlurmJob, ...]:
        actions.append((action, selected, tuple(job.job_id for job in displayed_jobs)))
        return (_job("44", state="PENDING", allocated_nodes="", reason="Priority"),)

    async def _run():
        app = create_slurm_job_control_app(
            (_job("41"), _job("44", state="PENDING", allocated_nodes="", reason="Priority")),
            title="Affected Slurm jobs",
            action_handler=_action_handler,
            poll_interval_seconds=30,
        )
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.press("space")
            await pilot.press("c")
            await pilot.pause(0.2)
            table = app.query_one("#jobs", DataTable)
            return table.row_count, table.get_row("44")[1], app.return_value

    row_count, job_id, result = asyncio.run(_run())

    assert actions == [("cancel", ("41",), ("41", "44"))]
    assert row_count == 1
    assert job_id == "44"
    assert result is None


def test_slurm_job_control_textual_durably_queues_action_during_refresh() -> None:
    refresh_started = threading.Event()
    release_refresh = threading.Event()
    actions: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []
    journal = new_slurm_action_journal()
    record_slurm_controller_observation(
        journal,
        authority="cxcli-bridge-0",
        connectivity="unavailable",
        authority_epoch="bridge-source-v1",
        snapshot_age_seconds=9,
    )

    def _jobs_provider() -> tuple[AffectedSlurmJob, ...]:
        refresh_started.set()
        assert release_refresh.wait(timeout=5)
        return (_job("41"), _job("44", state="PENDING", allocated_nodes=""))

    def _action_handler(
        action: str,
        selected: tuple[str, ...],
        displayed_jobs: tuple[AffectedSlurmJob, ...],
    ) -> tuple[AffectedSlurmJob, ...]:
        actions.append((action, selected, tuple(job.job_id for job in displayed_jobs)))
        enqueue_slurm_action(
            journal,
            kind=action,
            binding=_binding(selected[0]),
            intended_postcondition={"absent": True},
            accepted_authority_epoch="bridge-source-v1",
        )
        return displayed_jobs

    async def _run():
        app = create_slurm_job_control_app(
            (_job("41"), _job("44", state="PENDING", allocated_nodes="")),
            title="Affected Slurm jobs",
            jobs_provider=_jobs_provider,
            action_handler=_action_handler,
            action_journal_provider=_journal_provider(journal),
            poll_interval_seconds=30,
        )
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.press("space")
            app._schedule_poll(reason="test")  # noqa: SLF001
            assert await asyncio.to_thread(refresh_started.wait, 2)
            await pilot.press("c")
            await pilot.pause(0.2)
            status = app.query_one("#status", Static)
            table = app.query_one("#jobs", DataTable)
            queued_status = str(status.content)
            action_result = str(table.get_row("41")[-1])
            release_refresh.set()
            await pilot.pause(0.5)
            return queued_status, action_result, app.return_value

    queued_status, action_result, result = asyncio.run(_run())

    assert "Action intent recorded" in queued_status
    assert action_result == "cancel: Queued"
    assert actions == [("cancel", ("41",), ("41", "44"))]
    assert result is None


def test_slurm_job_control_textual_action_uses_pre_refresh_display_snapshot() -> None:
    refresh_started = threading.Event()
    release_refresh = threading.Event()
    actions: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []

    def _jobs_provider() -> tuple[AffectedSlurmJob, ...]:
        refresh_started.set()
        assert release_refresh.wait(timeout=5)
        return (_job("41"), _job("45"))

    def _action_handler(
        action: str,
        selected: tuple[str, ...],
        displayed_jobs: tuple[AffectedSlurmJob, ...],
    ) -> tuple[AffectedSlurmJob, ...]:
        actions.append((action, selected, tuple(job.job_id for job in displayed_jobs)))
        return (_job("45"),)

    async def _run():
        app = create_slurm_job_control_app(
            (_job("41"),),
            title="Affected Slurm jobs",
            jobs_provider=_jobs_provider,
            action_handler=_action_handler,
            poll_interval_seconds=30,
        )
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.press("space")
            app._schedule_poll(reason="test")  # noqa: SLF001
            assert await asyncio.to_thread(refresh_started.wait, 2)
            await pilot.press("c")
            await pilot.pause(0.2)
            release_refresh.set()
            await pilot.pause(0.5)

    asyncio.run(_run())

    assert actions == [("cancel", ("41",), ("41",))]


def test_slurm_job_control_textual_fast_scheduler_stays_open_for_queued_action() -> None:
    actions: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []
    journal = new_slurm_action_journal()

    def _action_handler(
        action: str,
        selected: tuple[str, ...],
        displayed_jobs: tuple[AffectedSlurmJob, ...],
    ) -> tuple[AffectedSlurmJob, ...]:
        actions.append((action, selected, tuple(job.job_id for job in displayed_jobs)))
        enqueue_slurm_action(
            journal,
            kind=action,
            binding=_binding(selected[0]),
            intended_postcondition={"absent": True},
        )
        return displayed_jobs

    async def _run():
        app = create_slurm_job_control_app(
            (_job("41"), _job("44", state="PENDING", allocated_nodes="", reason="Priority")),
            title="Affected Slurm jobs",
            action_handler=_action_handler,
            action_journal_provider=_journal_provider(journal),
            poll_interval_seconds=30,
            exit_after_action=True,
        )
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.press("space")
            await pilot.press("c")
            await pilot.pause(0.5)
        return app.return_value

    result = asyncio.run(_run())

    assert actions == [("cancel", ("41",), ("41", "44"))]
    assert result is None


def test_slurm_job_control_textual_callback_exits_when_action_clears_jobs() -> None:
    actions: list[tuple[str, tuple[str, ...]]] = []

    def _action_handler(
        action: str,
        selected: tuple[str, ...],
        _displayed_jobs: tuple[AffectedSlurmJob, ...],
    ) -> tuple[AffectedSlurmJob, ...]:
        actions.append((action, selected))
        return ()

    async def _run():
        app = create_slurm_job_control_app(
            (_job("41"), _job("42")),
            title="Affected Slurm jobs",
            action_handler=_action_handler,
            poll_interval_seconds=30,
        )
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.press("a")
            await pilot.press("c")
            await pilot.pause(0.5)
        return app.return_value

    result = asyncio.run(_run())

    assert actions == [("cancel", ("41", "42"))]
    assert result is not None
    assert result.action == SLURM_JOB_CONTROL_JOBS_CLEARED


def test_slurm_job_control_textual_upper_h_requeues_and_holds_selected_active_job() -> None:
    actions: list[tuple[str, tuple[str, ...]]] = []

    def _action_handler(
        action: str,
        selected: tuple[str, ...],
        _displayed_jobs: tuple[AffectedSlurmJob, ...],
    ) -> tuple[AffectedSlurmJob, ...]:
        actions.append((action, selected))
        return ()

    async def _run():
        app = create_slurm_job_control_app(
            (
                _job("41"),
                _job("44", state="PENDING", allocated_nodes="", reason="Priority"),
            ),
            title="Affected Slurm jobs",
            action_handler=_action_handler,
            poll_interval_seconds=30,
        )
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.press("space")
            await pilot.press("upper_h")
            await pilot.pause(0.5)
        return app.return_value

    result = asyncio.run(_run())

    assert actions == [("requeue-hold", ("41",))]
    assert result is not None
    assert result.action == SLURM_JOB_CONTROL_JOBS_CLEARED


def test_slurm_job_control_textual_rejects_pending_requeue_selection() -> None:
    actions: list[tuple[str, tuple[str, ...]]] = []

    def _action_handler(
        action: str,
        selected: tuple[str, ...],
        _displayed_jobs: tuple[AffectedSlurmJob, ...],
    ) -> tuple[AffectedSlurmJob, ...]:
        actions.append((action, selected))
        return ()

    async def _run():
        app = create_slurm_job_control_app(
            (
                _job("41"),
                _job("44", state="PENDING", allocated_nodes="", reason="Priority"),
            ),
            title="Affected Slurm jobs",
            action_handler=_action_handler,
            poll_interval_seconds=30,
        )
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.press("a")
            await pilot.press("q")
            await pilot.pause(0.1)
            status = app.query_one("#status", Static)
            return str(status.content), app.return_value

    status_text, result = asyncio.run(_run())

    assert actions == []
    assert result is None
    assert "Pending jobs cannot be requeued: 44" in status_text


@pytest.mark.parametrize(
    ("key", "expected_action"),
    (
        ("c", "cancel"),
        ("q", "requeue"),
        ("h", "hold"),
        ("upper_h", "requeue-hold"),
        ("u", "release"),
    ),
)
def test_slurm_job_control_textual_mutation_bindings_are_canonical(
    key: str,
    expected_action: str,
) -> None:
    actions: list[tuple[str, tuple[str, ...]]] = []

    def _action_handler(
        action: str,
        selected: tuple[str, ...],
        displayed_jobs: tuple[AffectedSlurmJob, ...],
    ) -> tuple[AffectedSlurmJob, ...]:
        actions.append((action, selected))
        return displayed_jobs

    async def _run() -> None:
        app = create_slurm_job_control_app(
            (_job("41"),),
            title="Affected Slurm jobs",
            action_handler=_action_handler,
        )
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.press("space")
            await pilot.press(key)
            await pilot.pause(0.2)

    asyncio.run(_run())

    assert actions == [(expected_action, ("41",))]


def test_slurm_job_control_textual_refresh_polls_provider_without_journaling_action() -> None:
    actions: list[tuple[str, tuple[str, ...]]] = []
    provider_calls: list[bool] = []
    displayed_ids: list[tuple[str, ...]] = []

    def _action_handler(
        action: str,
        selected: tuple[str, ...],
        displayed_jobs: tuple[AffectedSlurmJob, ...],
    ) -> tuple[AffectedSlurmJob, ...]:
        actions.append((action, selected))
        return displayed_jobs

    def _jobs_provider() -> tuple[AffectedSlurmJob, ...]:
        provider_calls.append(True)
        return (_job("41"), _job("42"), _job("43", state="PENDING"))

    async def _run() -> None:
        jobs = (_job("41"), _job("42"))
        app = create_slurm_job_control_app(
            jobs,
            title="Affected Slurm jobs",
            jobs_provider=_jobs_provider,
            action_handler=_action_handler,
        )
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.press("r")
            await pilot.pause(0.2)
            displayed_ids.append(tuple(job.job_id for job in app.affected_jobs))

    asyncio.run(_run())

    assert provider_calls == [True]
    assert actions == []
    assert displayed_ids == [("41", "42", "43")]


def test_slurm_job_control_textual_wait_uses_all_displayed_jobs() -> None:
    actions: list[tuple[str, tuple[str, ...]]] = []

    def _action_handler(
        action: str,
        selected: tuple[str, ...],
        displayed_jobs: tuple[AffectedSlurmJob, ...],
    ) -> tuple[AffectedSlurmJob, ...]:
        actions.append((action, selected))
        return displayed_jobs

    async def _run() -> None:
        jobs = (_job("41"), _job("42"))
        app = create_slurm_job_control_app(
            jobs,
            title="Affected Slurm jobs",
            jobs_provider=lambda: jobs,
            action_handler=_action_handler,
        )
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.press("w")
            await pilot.pause(0.2)

    asyncio.run(_run())

    assert actions == [("wait", ("41", "42"))]


def test_slurm_job_control_textual_removed_x_binding_does_nothing() -> None:
    actions: list[tuple[str, tuple[str, ...]]] = []

    def _action_handler(
        action: str,
        selected: tuple[str, ...],
        _displayed_jobs: tuple[AffectedSlurmJob, ...],
    ) -> tuple[AffectedSlurmJob, ...]:
        actions.append((action, selected))
        return ()

    async def _run():
        app = create_slurm_job_control_app(
            (_job("41"),),
            title="Affected Slurm jobs",
            action_handler=_action_handler,
            poll_interval_seconds=30,
        )
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.press("x")
            await pilot.pause(0.1)
        return app.return_value

    result = asyncio.run(_run())

    assert actions == []
    assert result is None


def test_slurm_job_control_textual_removed_x_binding_does_not_abort_wait() -> None:
    started = threading.Event()
    release = threading.Event()

    def _jobs_provider() -> tuple[AffectedSlurmJob, ...]:
        started.set()
        release.wait(1)
        return (_job("41"),)

    async def _run():
        app = create_slurm_job_control_app(
            (_job("41"),),
            title="Affected Slurm jobs",
            jobs_provider=_jobs_provider,
            wait_timeout_seconds=5,
            poll_interval_seconds=1,
        )
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.press("w")
            assert await asyncio.to_thread(started.wait, 1)
            await pilot.press("x")
            release.set()
            await pilot.pause(0.1)
        return app.return_value

    result = asyncio.run(_run())

    assert result is None


def test_slurm_job_control_textual_app_preserves_display_order_for_selected_jobs() -> None:
    async def _run():
        app = create_slurm_job_control_app(
            (_job("2"), _job("10")),
            title="Affected Slurm jobs",
        )
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.press("a")
            await pilot.press("c")
        return app.return_value

    result = asyncio.run(_run())

    assert result.action == "cancel"
    assert result.selected_job_ids == ("2", "10")


def test_slurm_job_control_textual_app_keeps_a_and_i_as_selection_keys() -> None:
    async def _run():
        app = create_slurm_job_control_app(
            (_job("41"), _job("44", state="PENDING")),
            title="Affected Slurm jobs",
        )
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.press("a")
            await pilot.press("i")
            await pilot.press("a")
            await pilot.press("c")
        return app.return_value

    result = asyncio.run(_run())

    assert result.action == "cancel"
    assert result.selected_job_ids == ("41", "44")
