"""Pure terminal presentation of scoped packet observations."""

from __future__ import annotations

from collections import Counter

from rich.console import Console
from rich.table import Table
from rich.text import Text

from .packet_health import CounterCheck, GatewayReport, TunnelReport


def result_text(result: str) -> Text:
    style = (
        "green" if result.startswith("PASS") else "dim" if result.startswith("N/A") else "yellow"
    )
    return Text(result, style=style)


def counter_text(check: CounterCheck, *, traffic: bool = False) -> Text:
    value = check.delta
    if not check.complete:
        label = f">={value}" if value else "—"
    else:
        label = str(value)
    style = "cyan" if traffic else "yellow" if check.increased or not check.complete else "green"
    return Text(label, style=style)


def _table(title: str, columns: tuple[str, ...]) -> Table:
    table = Table(title=Text(title), header_style="bold cyan", padding=(0, 1))
    for column in columns:
        table.add_column(
            column,
            overflow="fold",
            justify="left"
            if column in {"Tunnel", "Interface", "Result", "Gateway", "Check"}
            else "right",
        )
    return table


def render(
    console: Console,
    reports: dict[str, list[tuple[str, TunnelReport]]],
    gateways: dict[str, GatewayReport],
    *,
    details: bool,
) -> None:
    if not details:
        table = _table("Packet health — 3s observation", ("Tunnel", "RX window", "Receive health"))
        for hostname, rows in reports.items():
            for name, row in rows:
                label = f"{name}\n{hostname}" if len(reports) > 1 else name
                table.add_row(
                    Text(label),
                    Text(row.window),
                    Text(row.health, style=result_text(row.result).style),
                )
        console.print(table)
    else:
        console.print(Text("Packet receive checks — 3s observation", style="bold cyan"))
        console.print(
            "Error/drop threshold: 0 per sample; >0 = WARN. Received is traffic, not an error."
        )
        for hostname, rows in reports.items():
            interval = gateways[hostname].interval
            timing = (
                f"measured interval: {interval:.2f}s"
                if interval is not None
                else "interval unavailable"
            )
            console.print(Text(f"{hostname} · {timing}", style="bold"))
            narrow = console.width < 120
            columns: tuple[str, ...] = (
                ("Tunnel", "Win", "RX", "Late", "Dup", "Intg")
                if narrow
                else ("Tunnel", "Window", "Received", "Late", "Duplicate", "Integrity")
            )
            if not narrow:
                columns += ("IF errors", "IF drops")
            table = _table(
                "Tunnel checks (SA counters)" if narrow else "Tunnel checks", (*columns, "Result")
            )
            interface_table = _table(
                "Interface checks", ("Tunnel", "Interface", "RX errors", "RX drops", "Result")
            )
            uses = Counter(interface for _, row in rows for interface in row.interfaces)
            notes = []
            for name, row in rows:
                tunnel_label = Text(name)
                if not narrow and row.interfaces:
                    tunnel_label.append("\n" + ", ".join(row.interfaces), style="dim")
                cells = [
                    tunnel_label,
                    Text(row.window),
                    counter_text(row.counters["packets"], traffic=True),
                ]
                cells.extend(
                    counter_text(row.counters[key]) for key in ("late", "duplicate", "integrity")
                )
                if not narrow:
                    cells.extend(counter_text(row.counters[key]) for key in ("errors", "dropped"))
                table.add_row(*cells, result_text(row.result))
                checks = [row.counters[key] for key in ("errors", "dropped")]
                complete = all(check.complete for check in checks)
                increased = any(check.increased for check in checks)
                interface_result = "WARN" if increased else "PASS" if complete else "INCOMPLETE"
                if increased and not complete:
                    interface_result = "WARN · incomplete"
                if row.result.startswith("N/A"):
                    interface_result = row.result
                interface_table.add_row(
                    Text(name),
                    Text(", ".join(row.interfaces) or "—"),
                    *(counter_text(check) for check in checks),
                    result_text(interface_result),
                )
                if not row.result.startswith("PASS"):
                    notes.append(f"{name}: {row.health}")
                shared = [interface for interface in row.interfaces if uses[interface] > 1]
                if shared:
                    notes.append(
                        f"{name}: shared interface context ({', '.join(shared)}); not tunnel-attributed"
                    )
            console.print(table)
            if narrow:
                console.print(interface_table)
            for note in notes:
                console.print(Text(note))

    gateway_table = _table(
        "Gateway-wide checks", ("Gateway", "Check", "Delta", "Allowed", "Result")
    )
    for hostname, gateway in gateways.items():
        if details or gateway.result != "PASS":
            gateway_table.add_row(
                Text(hostname),
                Text("Sequence errors"),
                counter_text(gateway.sequence),
                "0",
                result_text(gateway.result),
            )
    if gateway_table.row_count:
        console.print(gateway_table)
        for hostname, gateway in gateways.items():
            if gateway.reason:
                console.print(Text(f"{hostname}: {gateway.reason}"))
    if details:
        console.print(
            Text(
                "Counters have different scopes and are not additive. "
                "Win = replay window; RX = received; Dup = duplicate; Intg = integrity. "
                "IF = interface context; "
                "sequence errors = gateway-wide XfrmInStateSeqError. Source reads are sequential. "
                "PASS covers this sample only. Late means outside the replay window; "
                "integrity failures do not identify an attacker.",
                style="dim",
            )
        )
