#cloud-config
# IMPORTANT (for the person creating the VM):
# - Replace the example under `ssh_authorized_keys:` with YOUR PUBLIC SSH key (e.g., the contents of
#   ~/.ssh/id_ed25519.pub or ~/.ssh/id_rsa.pub). This grants you initial access to the jump host.
# - Never paste your private key. To print your public key on macOS/Linux:
#     cat ~/.ssh/id_ed25519.pub
# - After the VM is up, you can add more users/keys later by placing their public keys into
#   /home/<username>/.ssh/authorized_keys on the jump host.
#
users:
  - name: nebius-user
    gecos: Nebius User
    groups: [sudo]
    shell: /bin/bash
    lock_passwd: true
    ssh_authorized_keys:
      - ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINotARealKeyExamplePlaceholder== user@example.com

package_update: true
package_upgrade: true
packages:
  - openssh-server
  - ufw
  - fail2ban
  - unattended-upgrades
  - auditd
  - apt-transport-https
  - ca-certificates
  - vim
  - git
  - net-tools
  - iproute2
  - curl

write_files:
  - path: /etc/bastion_allowed_cidrs
    owner: root:root
    permissions: '0644'
    content: |
      # One CIDR per line to allow SSH (port 22) from those sources.
      # Examples:
      # 203.0.113.4/32
      # 198.51.100.0/24
      #
      # TIP: Put YOUR current public IP here before creating the VM to avoid lockout.
      # You can get it from your laptop with:
      #   echo "$(curl -4 -s https://ifconfig.me)/32"
      #
      # YOUR_PUBLIC_IP/32

  - path: /etc/ssh/sshd_config.d/50-bastion.conf
    owner: root:root
    permissions: '0644'
    content: |
      Port 22
      AddressFamily any
      ListenAddress 0.0.0.0
      PermitRootLogin no
      PasswordAuthentication no
      ChallengeResponseAuthentication no
      UsePAM yes
      X11Forwarding no
      AllowTcpForwarding yes
      GatewayPorts no
      PermitTunnel no
      MaxAuthTries 3
      LogLevel VERBOSE
      AcceptEnv LANG LC_*
      Subsystem sftp /usr/lib/openssh/sftp-server

  - path: /etc/fail2ban/jail.d/sshd.conf
    owner: root:root
    permissions: '0644'
    content: |
      [sshd]
      enabled = true
      port = ssh
      logpath = /var/log/auth.log
      maxretry = 3
      bantime = 3600

  - path: /etc/audit/rules.d/50-bastion.rules
    owner: root:root
    permissions: '0640'
    content: |
      -w /bin -p x -k exec_bin
      -w /usr/bin -p x -k exec_usrbin
      -a always,exit -F arch=b64 -S execve -k execve_log
      -a always,exit -F arch=b32 -S execve -k execve_log

  - path: /etc/apt/apt.conf.d/50unattended-upgrades
    owner: root:root
    permissions: '0644'
    content: |
      Unattended-Upgrade::Allowed-Origins {
          "${distro_id}:${distro_codename}-security";
      };
      Unattended-Upgrade::Automatic-Reboot "false";
      Unattended-Upgrade::Automatic-Reboot-Time "02:00";

  - path: /usr/local/bin/log-restart-required.sh
    owner: root:root
    permissions: '0755'
    content: |
      #!/bin/bash
      if [ -f /var/run/reboot-required ]; then
        logger -t bastion "Security updates installed, restart required on $(hostname)"
      fi

  - path: /etc/systemd/system/log-restart-required.service
    owner: root:root
    permissions: '0644'
    content: |
      [Unit]
      Description=Log when a restart is required after upgrades

      [Service]
      Type=oneshot
      ExecStart=/usr/local/bin/log-restart-required.sh

  - path: /etc/systemd/system/log-restart-required.timer
    owner: root:root
    permissions: '0644'
    content: |
      [Unit]
      Description=Run restart-required logger hourly

      [Timer]
      OnBootSec=5min
      OnUnitActiveSec=1h
      Persistent=true

      [Install]
      WantedBy=timers.target

  - path: /etc/sudoers.d/90-allusers
    owner: root:root
    permissions: '0440'
    content: |
      %sudo ALL=(ALL) NOPASSWD:ALL

  - path: /usr/local/bin/setup-bastion.sh
    owner: root:root
    permissions: '0755'
    content: |
      #!/bin/bash
      set -euo pipefail

      ALLOWED_CIDR_FILE=/etc/bastion_allowed_cidrs
      # Build a list of allowed CIDRs from file (ignore comments/blank lines)
      mapfile -t ALLOWED_CIDRS < <(grep -vE '^[[:space:]]*(#|$)' "$ALLOWED_CIDR_FILE" 2>/dev/null || true)

      export DEBIAN_FRONTEND=noninteractive
      apt-get update -y
      apt-get install -y --no-install-recommends \
        openssh-server ufw fail2ban unattended-upgrades auditd \
        apt-transport-https ca-certificates vim git net-tools iproute2 curl || true

      chmod 0644 /etc/ssh/sshd_config.d/50-bastion.conf || true
      chmod 0644 /etc/fail2ban/jail.d/sshd.conf || true
      chmod 0640 /etc/audit/rules.d/50-bastion.rules || true
      chmod 0644 /etc/apt/apt.conf.d/50unattended-upgrades || true

      ufw --force reset
      ufw default deny incoming
      ufw default allow outgoing
      ufw allow from 127.0.0.1 to any port 22 proto tcp
      if [ "${#ALLOWED_CIDRS[@]}" -gt 0 ]; then
        for cidr in "${ALLOWED_CIDRS[@]}"; do
          ufw allow from "$cidr" to any port 22 proto tcp || true
        done
        logger -t bastion "Applied SSH allow rules for CIDRs: ${ALLOWED_CIDRS[*]}"
      else
        logger -t bastion "No client CIDRs configured in $ALLOWED_CIDR_FILE; SSH will be blocked by UFW"
      fi
      ufw --force enable || true

      augenrules --load || true
      systemctl daemon-reload || true
      systemctl enable --now auditd fail2ban ssh log-restart-required.timer || true
      systemctl reload ssh || true
      logger -t bastion "setup-bastion completed"
runcmd:
  - [ bash, -lc, "/usr/local/bin/setup-bastion.sh > /var/log/bastion-setup.log 2>&1 || true" ]

final_message: "Bastion cloud-init applied. SSH key auth only. Add your client IPs/CIDRs to /etc/bastion_allowed_cidrs (one per line) before provisioning to allow SSH."
