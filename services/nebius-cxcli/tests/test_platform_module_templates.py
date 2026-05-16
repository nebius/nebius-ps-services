import ast
import subprocess
from pathlib import Path

import yaml

_VALID_ED25519_PUBLIC_KEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAABAgMEBQYHCAkKCwwNDg8QERITFBUWFxgZGhscHR4f demo@example"
)


def test_ssh_jumphost_passes_bootstrap_allowed_cidrs_json_to_cloud_init() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    main_tf = (
        repo_root
        / "platform-infra"
        / "modules"
        / "ssh-jumphost"
        / "main.tf"
    ).read_text(encoding="utf-8")

    assert "bootstrap_allowed_cidrs_json = jsonencode(var.allowed_cidrs)" in main_tf


def test_ssh_jumphost_template_installs_vm_local_day2_allowed_cidr_helper() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    template_path = (
        repo_root
        / "platform-infra"
        / "modules"
        / "ssh-jumphost"
        / "ssh-jumphost-cloud-init.tftpl"
    )
    expression = (
        f'templatefile("{template_path}", {{ '
        'ssh_user_name = "ubuntu", '
        f'ssh_public_key = "{_VALID_ED25519_PUBLIC_KEY}", '
        'bootstrap_allowed_cidrs_json = jsonencode(["203.0.113.10/32"]) '
        "})\n"
    )
    rendered = subprocess.run(
        ["terraform", "console", "-no-color"],
        input=expression,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert rendered.startswith("<<EOT\n")
    cloud_config = rendered.removeprefix("<<EOT\n").removesuffix("\nEOT\n")
    payload = yaml.safe_load(cloud_config)
    setup_script = next(
        item["content"]
        for item in payload["write_files"]
        if item["path"] == "/usr/local/bin/setup-bastion.sh"
    )
    helper_script = next(
        item["content"]
        for item in payload["write_files"]
        if item["path"] == "/usr/local/sbin/nebius-ssh-jumphost"
    )

    heredoc_terminators = [
        line for line in setup_script.splitlines() if line.strip() == "EOF"
    ]
    assert heredoc_terminators
    assert all(line == "EOF" for line in heredoc_terminators)
    assert "/etc/nebius-ssh-jumphost/bootstrap-allowed-cidrs.json" in setup_script
    assert "nebius-ssh-jumphost bootstrap" in setup_script
    assert "/etc/bastion_allowed_cidrs" not in cloud_config
    assert "add-allowed-cidrs" in helper_script
    assert "remove-allowed-cidrs" in helper_script
    assert "refusing to remove every SSH source CIDR" in helper_script
    assert 'STATE_DIR = Path("/var/lib/nebius-ssh-jumphost")' in helper_script


def test_wireguard_gw_passes_runtime_config_json_to_cloud_init() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    main_tf = (
        repo_root
        / "platform-infra"
        / "modules"
        / "wireguard-gw"
        / "main.tf"
    ).read_text(encoding="utf-8")

    assert "wireguard_config_json = jsonencode({" in main_tf
    assert "endpoint_host                       = var.endpoint_host" in main_tf
    assert "local_subnets                       = var.local_subnets" in main_tf
    assert (
        "client_default_persistent_keepalive = var.client_default_persistent_keepalive"
        in main_tf
    )
    assert "bootstrap_clients_json = jsonencode(var.clients)" in main_tf


def test_wireguard_gw_defaults_are_operator_friendly() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    variables_tf = (
        repo_root
        / "platform-infra"
        / "modules"
        / "wireguard-gw"
        / "variables.tf"
    ).read_text(encoding="utf-8")

    assert 'default     = "10.8.0.1/22"' in variables_tf
    assert 'default     = ["1.1.1.1", "1.0.0.1"]' in variables_tf


def test_nebius_sdk_disk_api_exposes_encryption_and_deletion_protection() -> None:
    from nebius.api.nebius.compute.v1 import DiskEncryption, DiskSpec

    assert hasattr(DiskSpec, "disk_encryption")
    assert hasattr(DiskSpec, "forbid_deletion")
    assert (
        DiskEncryption.DiskEncryptionType.DISK_ENCRYPTION_MANAGED.name
        == "DISK_ENCRYPTION_MANAGED"
    )


def test_vm_module_maps_disk_security_controls_to_nebius_disk_resource() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    module_dir = repo_root / "platform-infra" / "modules" / "vm"
    main_tf = (module_dir / "main.tf").read_text(encoding="utf-8")
    variables_tf = (module_dir / "variables.tf").read_text(encoding="utf-8")
    locals_tf = (module_dir / "locals.tf").read_text(encoding="utf-8")

    assert "boot_disk_encryption_enabled" in variables_tf
    assert "boot_disk_deletion_protection" in variables_tf
    assert "encryption_enabled  = optional(bool, false)" in variables_tf
    assert "deletion_protection = optional(bool, false)" in variables_tf
    assert "disk_encryption = var.boot_disk_encryption_enabled ? {" in main_tf
    assert 'type = "DISK_ENCRYPTION_MANAGED"' in main_tf
    assert "forbid_deletion = var.boot_disk_deletion_protection" in main_tf
    assert "disk_encryption = each.value.encryption_enabled ? {" in main_tf
    assert "forbid_deletion = each.value.deletion_protection" in main_tf
    assert "boot_disk_encryption_enabled and boot_disk_deletion_protection apply only" in main_tf
    assert "encryption_enabled  = try(disk.encryption_enabled, false)" in locals_tf
    assert "deletion_protection = try(disk.deletion_protection, false)" in locals_tf


def test_jump_host_wrappers_use_vm_module_without_legacy_state_moves() -> None:
    repo_root = Path(__file__).resolve().parents[3]

    for module_name in ("ssh-jumphost", "wireguard-gw"):
        main_tf = (
            repo_root / "platform-infra" / "modules" / module_name / "main.tf"
        ).read_text(encoding="utf-8")

        assert "moved {" not in main_tf
        assert 'source = "../vm"' in main_tf
        assert "platform            = var.platform" in main_tf
        assert "preset              = var.preset" in main_tf
        assert "source_image_family = var.source_image_family" in main_tf
        assert "boot_disk_encryption_enabled  = var.boot_disk_encryption_enabled" in main_tf
        assert "boot_disk_deletion_protection = var.boot_disk_deletion_protection" in main_tf
        assert 'public_ip_mode          = "allocation"' in main_tf
        assert (
            "public_ip_allocation_id = local.effective_public_ip_allocation_id"
            in main_tf
        )
        assert "labels                  = local.effective_labels" in main_tf
        locals_tf = (
            repo_root / "platform-infra" / "modules" / module_name / "locals.tf"
        ).read_text(encoding="utf-8")
        assert "effective_labels = merge(" in locals_tf
        assert f'component = "{module_name}"' in locals_tf
        assert "name      = var.name" in locals_tf


def test_wireguard_gw_template_renders_shell_heredoc_terminators_at_column_zero() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    template_path = (
        repo_root
        / "platform-infra"
        / "modules"
        / "wireguard-gw"
        / "wireguard-cloud-init.tftpl"
    )
    expression = (
        f'templatefile("{template_path}", {{ '
        'ssh_user_name = "ubuntu", '
        f'ssh_public_key = "{_VALID_ED25519_PUBLIC_KEY}", '
        "wireguard_config_json = jsonencode({ "
        'wireguard_tunnel_cidr = "10.8.0.1/22", '
        "wireguard_listen_port = 51820, "
        "nat_mode = true, "
        "endpoint_host = null, "
        'local_subnets = ["10.0.0.0/8"], '
        'client_default_dns = ["1.1.1.1", "1.0.0.1"], '
        "client_default_persistent_keepalive = 25 "
        "}), "
        "bootstrap_clients_json = jsonencode([{ "
        'name = "laptop", '
        'client_wg_tunnel_address = "10.8.0.2/32", '
        'local_subnets = ["10.0.0.0/8"], '
        'dns = ["1.1.1.1"], '
        "persistent_keepalive = 25 "
        "}]) "
        "})\n"
    )
    rendered = subprocess.run(
        ["terraform", "console", "-no-color"],
        input=expression,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert rendered.startswith("<<EOT\n")
    cloud_config = rendered.removeprefix("<<EOT\n").removesuffix("\nEOT\n")
    payload = yaml.safe_load(cloud_config)
    expected_packages = {
        "wireguard",
        "wireguard-tools",
        "fail2ban",
        "auditd",
        "unattended-upgrades",
    }
    assert expected_packages <= set(payload["packages"])
    setup_script = next(
        item["content"]
        for item in payload["write_files"]
        if item["path"] == "/usr/local/bin/setup-wireguard.sh"
    )
    generator_script = next(
        item["content"]
        for item in payload["write_files"]
        if item["path"] == "/usr/local/sbin/nebius-wireguard-client"
    )
    wireguard_sshd_config = next(
        item["content"]
        for item in payload["write_files"]
        if item["path"] == "/etc/ssh/sshd_config.d/50-wireguard-gw.conf"
    )
    write_file_paths = {item["path"] for item in payload["write_files"]}
    ast.parse(generator_script)

    heredoc_terminators = [
        line for line in setup_script.splitlines() if line.strip() == "EOF"
    ]
    assert heredoc_terminators
    assert all(line == "EOF" for line in heredoc_terminators)
    assert "/etc/fail2ban/jail.d/sshd.conf" in write_file_paths
    assert "/etc/audit/rules.d/50-wireguard-gw.rules" in write_file_paths
    assert "/etc/sudoers.d/90-ubuntu" in write_file_paths
    assert "AuthenticationMethods publickey" in wireguard_sshd_config
    assert "AllowAgentForwarding no" in wireguard_sshd_config
    assert "AllowTcpForwarding no" in wireguard_sshd_config
    assert "MaxAuthTries 3" in wireguard_sshd_config
    assert "\nEOF\nchmod 0600 /etc/nebius-wireguard/config.json" in setup_script
    assert "nebius-wireguard-client bootstrap" in setup_script
    assert "fail2ban-client augenrules" in setup_script
    assert "add-local-subnets" in generator_script
    assert "remove-local-subnets" in generator_script
    assert "RUNTIME_CONFIG_PATH = STATE_DIR / \"runtime.json\"" in generator_script
    assert (
        'Path("/run/sshd").mkdir(mode=0o755, parents=True, exist_ok=True)'
        in generator_script
    )
    assert "net.ipv4.conf.all.accept_redirects=0" in generator_script
    assert "net.ipv4.conf.all.send_redirects=0" in generator_script
    assert 'DEFAULT_FORWARD_POLICY="DROP"' in generator_script
    assert "config.get(\"local_subnets\")" in generator_script
    assert "client_default_local_subnets" not in generator_script
    assert (
        'CLIENT_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,13}[a-z0-9])?$")'
        in generator_script
    )
    assert 'return f"wg-{secrets.token_hex(6)}"' in generator_script
    assert 'return f"client-' not in generator_script
    assert "client_wg_tunnel_address" in generator_script
    assert "local_subnets" in generator_script
    assert "AllowedIPs = {client['client_wg_tunnel_address']}" in generator_script
    assert "AllowedIPs = {','.join(local_subnets)}" in generator_script
    assert (
        "PostUp = iptables -t nat -A POSTROUTING -s {wg_net} -o {wan_if} -j MASQUERADE"
        in generator_script
    )
    assert (
        "PostDown = iptables -t nat -D POSTROUTING -s {wg_net} -o {wan_if} -j MASQUERADE"
        in generator_script
    )
    assert (
        'run(["ufw", "route", "allow", "in", "on", wan_if, "out", "on", WG_IF])'
        not in generator_script
    )
    assert 'run(["systemctl", "enable", "--now", "fail2ban"])' in generator_script
    assert "def add_client(" in generator_script
    assert "def list_clients(" in generator_script
    assert 'key not in {"preshared_key", "private_key"}' in generator_script
    assert "clients_created" in generator_script
    assert "remaining_client_slots" in generator_script
    assert "grep -qx" not in setup_script
    assert "^ssh\\\\.service" not in setup_script
    assert "^sshd\\\\.service" not in setup_script
    assert "sysctl -w net.ipv4.ip_forward=1" in generator_script


def test_vm_and_ssh_jumphost_bootstraps_prepare_sshd_runtime_directory() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    vm_template = (
        repo_root / "platform-infra" / "modules" / "vm" / "vm-cloud-init.tftpl"
    ).read_text(encoding="utf-8")
    ssh_jumphost_template = (
        repo_root
        / "platform-infra"
        / "modules"
        / "ssh-jumphost"
        / "ssh-jumphost-cloud-init.tftpl"
    ).read_text(encoding="utf-8")

    assert "install -d -m 0755 /run/sshd && sshd -t" in vm_template
    assert "install -d -m 0755 /run/sshd" in ssh_jumphost_template
    assert "sshd -t" in ssh_jumphost_template


def test_vm_module_does_not_manage_observability_collector_identity() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    module_dir = repo_root / "platform-infra" / "modules" / "vm"
    main_tf = (module_dir / "main.tf").read_text(encoding="utf-8")
    locals_tf = (module_dir / "locals.tf").read_text(encoding="utf-8")
    variables_tf = (module_dir / "variables.tf").read_text(encoding="utf-8")
    template = (module_dir / "vm-cloud-init.tftpl").read_text(encoding="utf-8")

    combined = "\n".join((main_tf, locals_tf, variables_tf, template))
    assert "observability_collector" not in combined
    assert "cxcli-vm-collector" not in combined
    assert "nebius-o11y-agent" not in combined
    assert "service_account_id   = var.service_account_id" in main_tf
