"""Standard-library-only export policy, also streamed to the gateway probe.

Unknown fields and free text are intentionally not exported. Identifiers are
private operational information; this is not an anonymizer or a raw log backup.
"""

from __future__ import annotations

import ipaddress
import json
import math
import re
from typing import Any

from .redaction import redact_text

MAX_BYTES = 2 * 1024 * 1024
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,191}\Z")
HASH = re.compile(r"[a-fA-F0-9]{12,128}\Z")
PACKAGE_WORKER_UNIT = re.compile(r"nebius-vpngw-(?:repair-)?package-[a-f0-9]{64}-[0-9]+\.service")
TIME = re.compile(r"[0-9TtZz:+. -]{1,40}\Z")
SECRET = re.compile(
    r"-----BEGIN [A-Z ]*(?:PRIVATE KEY|CERTIFICATE)-----|"
    r"(?i:bearer\s+[a-z0-9._~+/-]+|(?:password|passwd|psk|secret|token)\s*[=:])|"
    r"(?:AKIA|ASIA)[A-Z0-9]{16}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"
)
ENUMS = frozenset(
    [
        "active",
        "passive",
        "standby",
        "unknown",
        "idle",
        "pending",
        "accepted",
        "running",
        "stopped",
        "starting",
        "stopping",
        "failed",
        "error",
        "blocked",
        "healthy",
        "unhealthy",
        "ready",
        "not-ready",
        "transitioning",
        "stable",
        "prepared",
        "committed",
        "complete",
        "completed",
        "succeeded",
        "success",
        "failed",
        "inactive",
        "activating",
        "deactivating",
        "dead",
        "exited",
        "listening",
        "enabled",
        "disabled",
        "static",
        "generated",
        "masked",
        "loaded",
        "not-found",
        "auto",
        "manual",
        "yes",
        "no",
        "true",
        "false",
        "bgp",
        "static-route",
        "Established",
        "ESTABLISHED",
        "CONNECTING",
        "INSTALLED",
        "REKEYING",
        "DELETING",
        "IKEv1",
        "IKEv2",
        "transport",
        "tunnel",
        "UP",
        "DOWN",
        "UNKNOWN",
        "LOWER_UP",
        "BROADCAST",
        "MULTICAST",
        "LOOPBACK",
        "NOARP",
        "POINTOPOINT",
        "global",
        "link",
        "host",
        "universe",
        "unicast",
        "local",
        "broadcast",
        "unreachable",
        "prohibit",
        "blackhole",
        "AES_CBC",
        "AES_GCM_16",
        "AES_GCM_12",
        "AES_GCM_8",
        "CHACHA20_POLY1305",
        "HMAC_SHA2_256_128",
        "HMAC_SHA2_384_192",
        "HMAC_SHA2_512_256",
        "PRF_HMAC_SHA2_256",
        "PRF_HMAC_SHA2_384",
        "PRF_HMAC_SHA2_512",
        "MODP_2048",
        "MODP_3072",
        "MODP_4096",
        "ECP_256",
        "ECP_384",
        "ECP_521",
        "eq",
        "ne",
        "in",
        "==",
        "!=",
        "daddr",
        "saddr",
        "dport",
        "sport",
        "l4proto",
        "nfproto",
        "iifname",
        "oifname",
        "kernel",
        "boot",
        "static",
        "dhcp",
        "ra",
        "proto",
        "connected",
        "reject",
        "drop",
        "accept",
        "return",
        "jump",
        "goto",
        "INPUT",
        "OUTPUT",
        "FORWARD",
        "input",
        "output",
        "forward",
        "prerouting",
        "postrouting",
        "filter",
        "nat",
        "mangle",
        "inet",
        "ip",
        "ip6",
        "tcp",
        "udp",
        "esp",
        "icmp",
        "icmpv6",
        "all",
        "IPv4",
        "IPv6",
        "ipv4",
        "ipv6",
        "RUNNING",
        "STOPPED",
        "STARTING",
        "STOPPING",
        "CREATING",
        "UPDATING",
        "DELETING",
        "ERROR",
        "UNSPECIFIED",
        "initializing",
        "settling",
        "applying",
        "repairing",
        "destroying",
        "provisioned",
        "provision",
        "apply",
        "repair",
        "destroy",
        "install",
        "upgrade",
        "provision",
        "resume",
        "resume-transaction",
        "status-verification",
        "verify-fenced",
        "status-observation-unavailable",
        "required-state-not-observed",
        "operation-timeout",
        "artifact-changed",
        "apply-lock-held",
        "apply-lock-unproven",
        "managed-mtls-transaction-conflict",
        "managed-mtls-apply-interrupted",
        "standby-auto-healing-peer-policy-unavailable",
        "peer-heartbeat-endpoint-unreachable",
        "peer-heartbeat-tls-failure",
        "peer-heartbeat-stale",
        "independent-heartbeat-v6",
        "managed-mtls-v1",
        "fenced",
        "healthy-owner",
        "ssh-agent",
        "explicit-private-key",
        "default-private-key",
        "explicit-override-v1",
        "local-prebuilt-wheel",
        "VPNGW_AGENT_WHEEL",
        "installed-metadata",
        "missing",
        "ambiguous",
        "ok",
        "partial",
        "unavailable",
        "timeout",
        "changed",
        "not-applicable",
        "invalid-data",
        "permission",
        "io",
        "authentication",
        "ssh-transport",
        "source-limit",
        "privacy-omitted",
        "deadline",
    ]
)
IDENTITY_KEYS = frozenset(
    [
        "name",
        "hostname",
        "node_id",
        "peer_node_id",
        "owner_node_id",
        "observed_owner_node_id",
        "cluster_id",
        "instance_id",
        "project_id",
        "tenant_id",
        "region",
        "region_id",
        "subnet_id",
        "network_id",
        "allocation_id",
        "shared_allocation_id",
        "service_account_id",
        "route_table_id",
        "id",
        "parent_id",
        "resource_id",
        "operation_id",
        "apply_operation_id",
        "transfer_inhibition_operation_id",
        "generation_id",
        "boot_id",
        "guard_boot_id",
        "local_boot_id",
        "peer_boot_id",
        "interface",
        "ifname",
        "dev",
        "master",
        "username",
        "ssh_username",
        "tunnel",
        "connection",
        "gateway",
        "package_version",
        "version",
        "python_version",
        "kernel",
        "platform",
        "machine",
        "unit",
        "service",
        "protocol",
        "family",
        "type",
        "scope",
        "table",
        "proto",
        "state_machine",
        "worker_id",
        "route_runtime_id",
        "schema",
        "source_kind",
        "kind",
        "action",
        "phase",
        "member_id",
        "target_node_id",
        "candidate_node_id",
        "active_node_id",
        "previous_owner_node_id",
        "cloud_operation_id",
        "request_id",
        "execution_id",
        "gateway_name",
        "instance_name",
        "parentId",
        "allocationId",
        "subnetId",
        "networkId",
        "deviceId",
        "resourceVersion",
        "path",
        "source_file",
        "function",
        "event",
        "component",
        "package",
        "package_name",
    ]
)
HASH_KEYS = frozenset(
    [
        "sha256",
        "artifact_sha256",
        "wheel_sha256",
        "expected_artifact_sha256",
        "config_sha256",
        "configuration_digest",
        "static_routes_digest",
        "bgp_policy_digest",
        "configuration",
        "static_routes",
        "bgp_policy",
        "certificate_fingerprint",
        "fingerprint",
        "peer_fingerprint",
        "generation",
        "digest",
        "ha_config_digest",
        "selected_artifact_sha256",
        "package_sha256",
    ]
)
TIME_KEYS = frozenset(
    [
        "timestamp",
        "created_at",
        "updated_at",
        "observed_at",
        "started_at",
        "finished_at",
        "not_before",
        "not_after",
        "last_seen",
        "expires_at",
        "createdAt",
        "updatedAt",
        "ActiveEnterTimestamp",
        "InactiveEnterTimestamp",
        "ExecMainStartTimestamp",
        "ExecMainExitTimestamp",
    ]
)
VALUE_KEYS = frozenset(
    [
        "schema_version",
        "epoch",
        "sequence",
        "peer_sequence",
        "local_sequence",
        "age_seconds",
        "peer_age_seconds",
        "fresh",
        "peer_fresh",
        "peer_v6_fresh",
        "echo_valid",
        "challenge_valid",
        "challenge_echo_valid",
        "promotion_ready",
        "apply_locked",
        "transfer_inhibition_quiescent",
        "current_boot",
        "data_plane_mode",
        "state",
        "status",
        "phase",
        "role",
        "mode",
        "health",
        "result",
        "readiness",
        "ready",
        "reasons",
        "reason",
        "predicate",
        "latest_predicate",
        "first_predicate",
        "capabilities",
        "controller_capabilities",
        "boot_id_valid",
        "generation_matches",
        "sequence_valid",
        "certificate_valid",
        "self_fenced",
        "ownership_epoch",
        "ownership_incarnation",
        "priority",
        "enabled",
        "disabled",
        "failover_enabled",
        "auto_healing_enabled",
        "applied",
        "retained",
        "changed",
        "configuration_generation",
        "instance_count",
        "instance_index",
        "gateway_instance_index",
        "gateway_group",
        "name",
        "region",
        "vm_ha",
        "members",
        "nodes",
        "connections",
        "tunnels",
        "ha_role",
        "local_prefixes",
        "remote_prefixes",
        "external_ips",
        "private_ips",
        "management_ip",
        "public_ip",
        "address",
        "local",
        "remote",
        "peer_ip",
        "destination",
        "destination_prefix",
        "next_hop",
        "cidr",
        "prefix",
        "gateway",
        "via",
        "src",
        "dst",
        "prefsrc",
        "family",
        "table",
        "priority",
        "protocol",
        "metric",
        "mtu",
        "ifindex",
        "link_index",
        "link_type",
        "flags",
        "operstate",
        "addr_info",
        "prefixlen",
        "valid_life_time",
        "preferred_life_time",
        "rx_bytes",
        "tx_bytes",
        "rx_packets",
        "tx_packets",
        "rx_errors",
        "tx_errors",
        "rx_dropped",
        "tx_dropped",
        "bytes",
        "packets",
        "errors",
        "drops",
        "received",
        "sent",
        "if_id",
        "spi",
        "reqid",
        "replay_window",
        "replay_sequence",
        "items",
        "nftables",
        "expr",
        "match",
        "left",
        "right",
        "op",
        "key",
        "payload",
        "meta",
        "counter",
        "accept",
        "drop",
        "jump",
        "ip_address",
        "aliases",
        "security_groups",
        "ipv4_public_subnet_id",
        "ipv4_private_subnet_id",
        "cidrs",
        "address_range",
        "port_ranges",
        "ports",
        "start",
        "end",
        "access",
        "index",
        "default",
        "attached_to",
        "local_epoch",
        "peer_epoch",
        "target_epoch",
        "accepted_epochs",
        "phase_started_at",
        "local_identity",
        "peer_identity",
        "trusted_peer",
        "trust",
        "fingerprints",
        "operation",
        "receipts",
        "pending",
        "progress",
        "steps",
        "intent",
        "inputs",
        "predecessor",
        "successors",
        "artifact",
        "applied_generation",
        "desired_generation",
        "heartbeat_age_seconds",
        "sequence_number",
        "uptime",
        "elapsed_seconds",
        "memory_total",
        "memory_available",
        "disk_total",
        "disk_free",
        "disk_used",
        "free",
        "total",
        "used",
        "available",
        "loadavg",
        "ntp_synchronized",
        "timezone",
        "offset_seconds",
        "ActiveState",
        "SubState",
        "LoadState",
        "Result",
        "MainPID",
        "NRestarts",
        "ExecMainStatus",
        "ExecMainCode",
        "UnitFileState",
        "ActiveEnterTimestampMonotonic",
        "InactiveEnterTimestampMonotonic",
        "NTPSynchronized",
        "TimeUSec",
        "RTCTimeUSec",
        "as",
        "local_as",
        "remote_as",
        "localAs",
        "remoteAs",
        "peerAs",
        "vrfId",
        "vrfName",
        "bgpState",
        "pfxRcd",
        "pfxSnt",
        "state",
        "remoteRouterId",
        "routerId",
        "peerUptimeEstablishedEpoch",
        "peerUptimeMsec",
        "connectionsEstablished",
        "connectionsDropped",
        "msgRcvd",
        "msgSent",
        "network",
        "mask",
        "nexthops",
        "bestpath",
        "selected",
        "valid",
        "weight",
        "metric",
        "locPrf",
        "pathFrom",
        "local-host",
        "remote-host",
        "local-port",
        "remote-port",
        "uniqueid",
        "version",
        "initiator",
        "encr-alg",
        "encr-keysize",
        "integ-alg",
        "prf-alg",
        "dh-group",
        "established",
        "rekey-time",
        "reauth-time",
        "child-sas",
        "spi-in",
        "spi-out",
        "encap",
        "bytes-in",
        "bytes-out",
        "packets-in",
        "packets-out",
        "local-ts",
        "remote-ts",
        "local-id",
        "remote-id",
        "life-time",
        "install-time",
        "replay-window",
        "policy",
        "fwd",
        "ingress",
        "egress",
        "from_port",
        "to_port",
        "port",
        "direction",
        "security_group_ids",
        "explicit_ids",
        "effective_ids",
        "source_cidrs",
        "destination_cidrs",
        "ip_protocol",
        "chain",
        "hook",
        "verdict",
        "counters",
        "handle",
        "rule",
        "rules",
        "routes",
        "peers",
        "ipv4Unicast",
        "heartbeat",
        "mtls",
        "peer",
        "local",
        "identity",
        "runtime_identity",
        "auto_healing",
        "apply_convergence",
        "route_reconciliation",
        "guard",
        "materialization",
        "checkpoint",
        "transaction",
        "plan",
        "effects",
        "bindings",
        "runtime",
        "targets",
        "request",
        "outcome",
        "first",
        "latest",
        "failure",
        "last_failure",
        "pending_operation",
        "pending_requests",
        "locks",
        "receipts",
        "transfer_progress",
        "transfer_lineage",
        "rearm",
        "standby_ready",
        "promotion",
        "network_interfaces",
        "networkInterfaces",
        "publicIpAddress",
        "privateIpAddress",
        "securityGroups",
        "metadata",
        "spec",
        "status",
        "diskAttachments",
        "bootDisk",
    ]
)
DROP_KEYS = frozenset(
    [
        "password",
        "passwd",
        "psk",
        "secret",
        "token",
        "authorization",
        "private_key",
        "certificate",
        "pem",
        "cloud_init_user_data",
        "cloudInitUserData",
        "user_data",
        "userdata",
        "environment",
        "env",
        "command",
        "cmdline",
        "argv",
        "message",
        "error",
        "details",
        "traceback",
        "stderr",
        "stdout",
        "url",
        "ssh_public_key",
        "ssh_private_key_path",
        "remote-id",
        "local-id",
    ]
)


ENUMS |= frozenset(
    {
        "service-failed",
        "service-transition",
        "ipsec-authentication-failed",
        "ipsec-proposal-mismatch",
        "ipsec-selector-mismatch",
        "ipsec-established",
        "ipsec-retransmission",
        "bgp-peer-down",
        "bgp-peer-up",
        "kernel-oom",
        "network-link-down",
        "network-link-up",
        "python-exception",
        "agent-or-bootstrap-failed",
    }
)


ENUMS |= frozenset(
    [
        "active-node-lacks-exact-allocation-ownership",
        "active-route-reconciliation-context-stale",
        "apply-lock-held",
        "authoritative-cloud-observation-not-yet-recorded",
        "authoritative-cloud-observation-unavailable",
        "authoritative-owner-active",
        "authoritative-owner-peer-is-healthy",
        "authoritative-ownership-epoch-missing",
        "automatic-retry-exhausted",
        "bgp-import-policy-mismatch",
        "bgp-not-ready",
        "bgp-policy-digest-mismatch",
        "candidate-attachment-observation-inconsistent",
        "candidate-attachment-requires-reproof",
        "candidate-dataplane-requires-owner-only-preparation",
        "candidate-owner-observation-with-former-attachment-present",
        "candidate-owner-observation-without-exact-attachment",
        "candidate-ownership-awaiting-authoritative-observation",
        "candidate-ownership-re-read-required",
        "candidate-requires-exact-shared-allocation",
        "checkpointed-action-prerequisites-changed",
        "checkpointed-action-requires-current-boot-guard",
        "checkpointed-action-requires-passive-dataplane",
        "cloud-operation-finalization-failed",
        "cloud-operation-journal-invalid",
        "cloud-operation-journal-unbound",
        "cloud-ownership-unavailable",
        "cold-start-guard-not-installed",
        "cold-start-guard-stale",
        "compute-start-failed",
        "compute-start-permanent-failure",
        "compute-start-retry-scheduled",
        "configuration-digest-mismatch",
        "configured-bgp-sessions-not-established",
        "controller-effect-pending",
        "controller-step-failed",
        "current-boot-guard-not-active",
        "current-boot-guard-not-installed",
        "current-boot-status-stale",
        "data-plane-not-passive",
        "exact-owner-ready-to-enable-forwarding",
        "explicit-retry-required",
        "former-allocation-attachment-must-be-absent",
        "former-attachment-observation-inconsistent",
        "former-owner-compute-state-ambiguous",
        "former-owner-identity-mismatch",
        "former-owner-must-be-stopped",
        "generation-mismatch",
        "local-node-is-not-exact-non-owner",
        "local-ownership-lacks-establishment-proof",
        "local-repair-budget-exhausted",
        "local-repair-exhausted-forwarding-fenced",
        "local-repair-verification-active",
        "local-service-unhealthy",
        "local-shared-alias-present",
        "manual-failback-invalid-for-passive-role",
        "manual-failback-required",
        "manual-failover-invalid-for-active-role",
        "mtls-rotation-active",
        "no-configured-bgp-sessions",
        "non-owner-must-remain-passive",
        "origination-inhibited",
        "origination-policy-unproven",
        "origination-preparation-unready",
        "owner-local-prefix-origination-required",
        "owner-must-materialize-passive-dataplane",
        "owner-routes-require-reconciliation",
        "owner-shared-alias-not-exact",
        "ownership-confirmation-without-exact-attachment",
        "ownership-or-alias-drift",
        "peer-configured-role-mismatch",
        "peer-generation-unavailable",
        "peer-heartbeat-endpoint-unreachable",
        "peer-heartbeat-stale",
        "peer-heartbeat-tls-failure",
        "peer-heartbeat-unhealthy",
        "peer-identity-mismatch",
        "promotion-receipt-invalid",
        "promotion-receipt-unavailable",
        "rearm-checkpoint-invalid",
        "rearm-cloud-observation-unavailable",
        "rearm-request-changed",
        "rearm-request-invalid",
        "rearm-status-invalid",
        "rearm-status-unavailable",
        "rearm-writer-busy",
        "redundant-bgp-session-unavailable",
        "redundant-path-degraded",
        "replaying-checkpointed-action",
        "required-bgp-prefixes-lack-usable-xfrm-next-hop",
        "required-bgp-prefixes-not-learned",
        "route-ledger-identity-not-exact",
        "route-runtime-identity-missing",
        "routing-hygiene-not-ready",
        "runtime-identity-blocked",
        "shared-allocation-identity-missing",
        "standby-auto-healing-peer-policy-unavailable",
        "standby-auto-healing-policy-disabled",
        "standby-auto-healing-policy-disagreement",
        "standby-auto-healing-policy-invalid",
        "standby-auto-healing-policy-transition",
        "standby-auto-healing-policy-unavailable",
        "standby-auto-healing-recovery-authority-stale-or-foreign",
        "standby-auto-healing-recovery-completion-failed",
        "standby-auto-healing-recovery-consume-failed",
        "standby-auto-healing-recovery-invalid",
        "standby-auto-healing-recovery-policy-changed",
        "standby-auto-healing-recovery-policy-unavailable",
        "standby-readiness-timeout",
        "standby-ready-evidence-invalid",
        "standby-restoration-authority-stale-or-foreign",
        "standby-restoration-authorization-invalid",
        "standby-restoration-blocked",
        "standby-restoration-not-committed",
        "standby-restoration-policy-changed",
        "standby-restoration-policy-unavailable",
        "standby-restoration-start-identity-changed",
        "standby-transfer-readiness-unavailable",
        "static-route-digest-mismatch",
        "static-routes-not-ready",
        "suspicion-window-active",
        "unexpected-allocation-owner",
        "vm-ha-removal-or-disable-active",
        "xfrm-not-ready",
    ]
)
VALUE_KEYS |= frozenset(
    {
        "done",
        "failure_code",
        "waiting",
        "terminal",
        "requests",
        "advertisedRoutes",
        "totalPrefixCounter",
        "filteredPrefixCounter",
        "localRouterId",
        "peerRouterId",
        "network",
        "prefixLen",
    }
)
HASH_KEYS |= frozenset({"spki_fingerprint"})
IDENTITY_KEYS |= frozenset({"compute_id", "boot"})
IDENTITY_KEYS |= frozenset({"accepted_cloud_operation_id", "provider_operation_id"})


ENUMS |= frozenset({"ALLOW", "DENY", "ANY", "TCP", "UDP", "ICMP", "STATEFUL", "STATELESS", "READY"})
VALUE_KEYS |= frozenset({"destination_ports", "source_ports"})
IDENTITY_KEYS |= frozenset({"source_security_group_id", "destination_security_group_id"})


VALUE_KEYS |= frozenset(
    {
        "peer_target_epoch",
        "preserve_local",
        "old_local",
        "old_peers",
        "pending_local",
        "pending_peer",
        "new_identity_observed",
        "observation_ids",
        "operation_kind",
        "last_sequence",
        "highest_sequence",
        "accepted_sequence",
        "replay",
        "staged",
        "previous",
        "current",
        "revision",
    }
)
ENUMS |= frozenset(
    {
        "peer-staged",
        "trust-expanded",
        "local-active",
        "verified",
        "pruned",
        "rolled-back",
        "bootstrap",
        "replacement",
        "recovery",
        "rotation",
    }
)


IDENTITY_KEYS |= frozenset(
    {
        "primary_allocation_id",
        "public_allocation_id",
        "network_interface_name",
        "network_interface_subnet_id",
        "disk_id",
        "allocation_name",
        "workload_subnet_id",
    }
)
VALUE_KEYS |= frozenset({"route_targets", "compute_revision", "alias_allocation_ids"})


VALUE_KEYS |= frozenset(
    {
        "resource_version",
        "reconciling",
        "public_ip_address",
        "ipv4_private",
        "ipv4_public",
        "allocation_details",
        "allocated_cidr",
        "assignment",
        "network_interface",
        "reserved",
        "route_table",
    }
)
IDENTITY_KEYS |= frozenset({"pool_id"})
ENUMS |= frozenset(
    {"ALLOCATED", "RESERVED", "FREE", "UNASSIGNED", "ASSIGNED", "IPV4_PRIVATE", "IPV4_PUBLIC"}
)


IDENTITY_KEYS |= frozenset({"observed_route_table_id"})
IDENTITY_KEYS |= frozenset({"os_id", "os_version_id"})
VALUE_KEYS |= frozenset({"allocation", "default_egress_gateway"})
ENUMS |= frozenset(
    {
        "data-plane-mode-mismatch",
        "heartbeat-capability-missing",
        "heartbeat-schema-mismatch",
        "peer-heartbeat-not-fresh",
        "promotion-readiness-mismatch",
        "owner-identity-mismatch",
        "transfer-inhibition-operation-mismatch",
        "transfer-inhibition-not-quiescent",
        "managed-mtls-evidence-mismatch",
    }
)


# Diagnostic strings are a closed vocabulary, not identifier-shaped free text.
EXCEPTIONS = frozenset(
    {
        "ImportError",
        "ModuleNotFoundError",
        "PermissionError",
        "FileNotFoundError",
        "OSError",
        "TimeoutError",
        "ConnectionError",
        "ConnectionRefusedError",
        "ConnectionResetError",
        "SSLError",
        "SSLCertVerificationError",
        "JSONDecodeError",
        "ValueError",
        "TypeError",
        "KeyError",
        "RuntimeError",
        "MemoryError",
    }
)
FRAMES = frozenset(
    {
        "agent/main.py",
        "agent/vm_ha_controller.py",
        "agent/vm_ha_rearm.py",
        "agent/local_commands.py",
        "agent/ordinary.py",
        "vm_ha_tls.py",
        "vm_ha_managed_credentials.py",
        "vm_ha_command.py",
    }
)
DIAGNOSTIC_STRINGS = {
    "exception_class": EXCEPTIONS,
    "frame": FRAMES,
    "unit": frozenset(
        {
            "nebius-vpngw-agent",
            "nebius-vpngw-vm-ha",
            "nebius-vpngw-vm-ha-guard",
            "nebius-vpngw-vm-ha-rearm",
            "nebius-vpngw-ingress",
            "nebius-vpngw-fix-routes",
            "nebius-vpngw-ordinary-fix-routes",
            "nebius-vpngw-health-monitor",
            "nebius-vpngw-ordinary-agent",
            "nebius-vpngw-ordinary-health-monitor",
            "nebius-vpngw-esp4-preflight",
            "strongswan",
            "strongswan-starter",
            "strongswan-swanctl",
            "charon-systemd",
            "frr",
            "systemd-networkd",
            "cloud-init-local",
            "cloud-init",
            "cloud-config",
            "cloud-final",
            "nebius-vpngw-fix-routes.timer",
            "package-worker",
            "unknown",
        }
    ),
    "stage": frozenset({"init-local", "init", "modules-config", "modules-final", "unknown"}),
    "location": frozenset({"root", "logs", "state", "temporary"}),
}
ENUMS |= (
    EXCEPTIONS
    | FRAMES
    | frozenset().union(*DIAGNOSTIC_STRINGS.values())
    | frozenset(
        {
            "exact",
            "lower-bound",
            "oldest",
            "newest",
            "none",
            "unsupported",
            "degraded",
            "done",
            "not-run",
            "not-started",
            "error-disabled",
            "python-error",
            "errno-error",
            "bootstrap-error",
            "package-error",
            "certificate-error",
            "wildcard",
            "journal",
            "current-snapshot",
            "current-samples",
            "historical-events",
            "cpu",
            "memory",
            "io",
            "some",
            "full",
            "tcp",
            "udp",
            "tcp6",
            "udp6",
        }
    )
)
VALUE_KEYS |= frozenset(
    {
        "ExecMainStartTimestampMonotonic",
        "ExecMainExitTimestampMonotonic",
        "OOMKilled",
        "coverage",
        "events",
        "scanned",
        "invalid",
        "unrecognized",
        "recognized",
        "exported",
        "discarded",
        "counts",
        "retention",
        "truncation",
        "source_limited",
        "exception_class",
        "errno",
        "frames",
        "frame",
        "line",
        "unit",
        "priority",
        "pid",
        "monotonic_usec",
        "event_id",
        "invocation_id",
        "victim_pid",
        "oom_score",
        "rss_bytes",
        "oom_kill",
        "oom",
        "high",
        "max",
        "low",
        "degraded",
        "stages",
        "stage",
        "error_count",
        "recoverable_error_count",
        "errors",
        "start",
        "finished",
        "unknown_errors",
        "samples",
        "interval_seconds",
        "sample_count",
        "expected_samples",
        "counter_reset",
        "cpu_user",
        "cpu_system",
        "cpu_idle",
        "cpu_iowait",
        "cpu_steal",
        "runnable",
        "blocked",
        "load_1",
        "load_5",
        "load_15",
        "swap_total",
        "swap_free",
        "swap_in",
        "swap_out",
        "page_in",
        "page_out",
        "pressure",
        "resource",
        "some",
        "full",
        "avg10",
        "avg60",
        "avg300",
        "total_usec",
        "storage",
        "location",
        "inodes_total",
        "inodes_free",
        "service_cgroups",
        "memory_current",
        "memory_peak",
        "memory_events",
        "available",
        "listeners",
        "local_port",
        "clock_realtime_usec",
        "clock_monotonic_usec",
        "collection_started_at",
        "collection_finished_at",
        "evidence_class",
        "observed_start_usec",
        "observed_end_usec",
    }
)
TIME_KEYS |= frozenset({"requested_since", "requested_until"})
HASH_KEYS |= frozenset({"event_id", "invocation_id"})
VALUE_KEYS |= frozenset({"pids", "pid_membership_available"})
VALUE_KEYS |= frozenset(
    {
        "peer_health_fresh",
        "peer_process_id",
        "peer_send_last_success_age_seconds",
        "peer_send_last_error_age_seconds",
        "peer_send_consecutive_failures",
        "peer_send_error_kind",
    }
)
DIAGNOSTIC_STRINGS["peer_send_error_kind"] = frozenset(
    {"timeout", "refused", "unreachable", "tls", "protocol"}
)


def retention_priorities(events: list[dict[str, Any]]) -> list[int]:
    """Shared retention rule: first/latest failure per boot, other failures, routine."""
    priorities = [0] * len(events)
    failures: dict[str, list[int]] = {}
    for index, event in enumerate(events):
        priority = event.get("priority")
        reasons = event.get("reasons", [])
        if not isinstance(reasons, (list, tuple)):
            reasons = []
        if (type(priority) is int and 0 <= priority <= 3) or any(
            any(word in reason for word in ("failed", "error", "timeout", "mismatch", "oom"))
            for reason in reasons
            if isinstance(reason, str)
        ):
            priorities[index] = 1
            failures.setdefault(str(event.get("boot_id", "")), []).append(index)
    for indices in failures.values():
        priorities[indices[0]] = priorities[indices[-1]] = 2
    return priorities


def project_journal(
    value: dict[str, Any], omitted: list[int], limited: list[int], *, byte_limit: int | None = None
) -> dict[str, Any]:
    """Keep accounting outside per-event traversal and serialized byte budgets."""
    coverage = project(value.get("coverage", {}), depth=1, omitted=omitted, limited=limited)
    if not isinstance(coverage, dict):
        coverage = {}
    for key in ("scanned", "invalid", "unrecognized", "recognized", "exported", "discarded"):
        if type(coverage.get(key)) is not int or not 0 <= coverage[key] <= 10**20:
            coverage[key] = 0
            limited[0] += 1
            coverage["counts"] = "lower-bound"
    events = value.get("events")
    if not isinstance(events, list):
        limited[0] += 1
        events = []
    cap = MAX_BYTES if byte_limit is None else min(MAX_BYTES, max(0, byte_limit))
    retained: dict[int, dict[str, Any]] = {}
    used = len(json.dumps({"coverage": coverage, "events": []}, indent=2).encode()) + 512
    discarded = 0
    candidates = []
    for index, event in enumerate(events[:5000]):
        clean = project(
            {key: child for key, child in event.items() if key != "message"}
            if isinstance(event, dict)
            else event,
            omitted=omitted,
            limited=limited,
            _budget=[512],
            depth=1,
        )
        if not isinstance(clean, dict) or not clean:
            discarded += 1
            continue
        if isinstance(event, dict) and isinstance(event.get("message"), str):
            message = event["message"]
            clean["message"] = redact_text(message[:65536], complete=len(message) <= 65536)[
                "content"
            ]
            if len(message) > 65536:
                limited[0] += 1
        candidates.append((index, clean))
    priorities = retention_priorities([event for _, event in candidates])
    order = sorted(range(len(candidates)), key=lambda index: (-priorities[index], index))
    for position in order:
        index, clean = candidates[position]
        encoded = json.dumps(clean, indent=2).encode()
        # Objects nest two levels deeper in the final pretty-printed envelope.
        size = len(encoded) + 4 * (encoded.count(b"\n") + 1) + 4
        if used + size > cap:
            discarded += 1
            continue
        retained[index] = clean
        used += size
    discarded += max(0, len(events) - 5000)
    if discarded:
        limited[0] += discarded
        coverage["discarded"] = int(coverage.get("discarded", 0)) + discarded
        coverage["truncation"] = "oldest"
    coverage["exported"] = len(retained)
    return {"coverage": coverage, "events": [retained[index] for index in sorted(retained)]}


def safe_identifier(value: Any) -> bool:
    return isinstance(value, str) and bool(IDENTIFIER.fullmatch(value)) and not SECRET.search(value)


def project(
    value: Any,
    key: str = "",
    *,
    depth: int = 0,
    omitted: list[int] | None = None,
    limited: list[int] | None = None,
    _seen: set[int] | None = None,
    _budget: list[int] | None = None,
) -> Any:
    """Project JSON-like input; no value-dependent key/value is copied unchecked."""
    if omitted is None:
        omitted = [0]
    if limited is None:
        limited = [0]
    if depth == 0 and isinstance(value, dict) and "events" in value and "coverage" in value:
        return project_journal(value, omitted, limited)
    if _seen is None:
        _seen = set()
    if _budget is None:
        _budget = [4096]

    def drop() -> None:
        omitted[0] += 1
        return None

    _budget[0] -= 1
    if depth > 14 or _budget[0] < 0:
        limited[0] += 1
        return drop()
    if key in DROP_KEYS:
        return drop()
    if isinstance(value, (dict, list, tuple)):
        if id(value) in _seen:
            limited[0] += 1
            return drop()
        _seen.add(id(value))
    if isinstance(value, dict):
        result = {}
        for child, item in list(value.items())[:4096]:
            if not isinstance(child, str):
                drop()
                continue
            allowed = child in VALUE_KEYS | IDENTITY_KEYS | HASH_KEYS | TIME_KEYS
            if key == "requests" and re.fullmatch(r"[a-f0-9]{64}", child):
                # Package-worker request IDs are exact digests, never free-text keys.
                allowed = True
            if not allowed:
                try:
                    ipaddress.ip_network(child, strict=False)
                    allowed = True
                except ValueError:
                    # Named IPsec children and BGP peers are projected as lists
                    # by their owner; arbitrary map keys never cross this boundary.
                    pass
            if not allowed or child in DROP_KEYS:
                drop()
                continue
            clean = project(
                item,
                child,
                depth=depth + 1,
                omitted=omitted,
                limited=limited,
                _seen=_seen,
                _budget=_budget,
            )
            if clean is not None:
                result[child] = clean
        if len(value) > 4096:
            omitted[0] += len(value) - 4096
            limited[0] += len(value) - 4096
        return result
    if isinstance(value, (tuple, list)):
        sequence = []
        for item in value[:4096]:
            clean = project(
                item,
                key,
                depth=depth + 1,
                omitted=omitted,
                limited=limited,
                _seen=_seen,
                _budget=_budget,
            )
            if clean is not None:
                sequence.append(clean)
        omitted[0] += max(0, len(value) - 4096)
        limited[0] += max(0, len(value) - 4096)
        return sequence
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and abs(value) <= 10**20 and math.isfinite(value):
        return value
    if not isinstance(value, str) or len(value) > 256 or SECRET.search(value):
        return drop()
    if key == "identity":
        # Scalar transport identities are exact digests; native identity objects
        # have already passed through the recursive field projection above.
        return value if re.fullmatch(r"[a-f0-9]{64}", value) else drop()
    if key == "unit" and PACKAGE_WORKER_UNIT.fullmatch(value):
        return value
    if key in DIAGNOSTIC_STRINGS:
        return value if value in DIAGNOSTIC_STRINGS[key] else drop()
    if key in HASH_KEYS:
        return value if HASH.fullmatch(value) else drop()
    if key in TIME_KEYS:
        return value if TIME.fullmatch(value) else drop()
    if key in IDENTITY_KEYS and safe_identifier(value):
        return value
    if value in ENUMS or value.isdecimal():
        return value
    try:
        ipaddress.ip_network(value, strict=False)
        return value
    except ValueError:
        return drop()


VALUE_KEYS |= frozenset({"ssh_identity_source", "ssh_trust_source"})
DIAGNOSTIC_STRINGS["ssh_identity_source"] = frozenset(
    {
        "operator-private-key",
        "explicit-private-key",
        "ssh-agent",
        "default-private-key",
        "library-default",
    }
)
DIAGNOSTIC_STRINGS["ssh_trust_source"] = frozenset(
    {"explicit-known-hosts", "managed-pins", "system-known-hosts"}
)
