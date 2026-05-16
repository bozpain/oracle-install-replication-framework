"""OS preparation phase manual.

Builds root-executed OS bootstrap scripts: package baseline, Oracle groups and
users, `/u01` layout, resolver, hosts file, firewall shutdown, SELinux
permissive mode, and chrony NTP configuration.
"""

from __future__ import annotations

import shlex

from oracle_auto.automation import AutomationStep, shell_script
from oracle_auto.config import AutomationConfig, NodeConfig
from oracle_auto.phase_builders.common import (
    INVENTORY_LOCATION,
    DB_HOME,
    GRID_BASE,
    GRID_BASE_DIR,
    ORACLE_BASE,
    STAGE,
    ensure_swap_lines,
    install_asmlib_lines,
    inventory_pointer_lines,
    make_step,
)


def prepare_os_steps(config: AutomationConfig) -> list[AutomationStep]:
    return [
        make_step(
            "prepare-os",
            "prepare_os",
            node,
            "Prepare Oracle Linux users, DNS, hosts, firewall, SELinux, and chrony",
            _prepare_os_script(config, node),
            timeout=900,
        )
        for node in config.all_nodes
    ]


def _prepare_os_script(config: AutomationConfig, node: NodeConfig) -> str:
    hosts_block = _hosts_block(config)
    resolv_conf = _resolv_conf(config)
    chrony_block = "\n".join(f"server {server} iburst" for server in config.os.ntp_servers)
    lines = [
        f"{config.os.package_manager} install -y {shlex.quote(config.os.preinstall_package)} chrony unzip tar libnsl",
        *install_asmlib_lines(config.os.package_manager),
        'for group in oinstall dba oper backupdba dgdba kmdba racdba asmadmin asmdba asmoper; do getent group "$group" >/dev/null || groupadd "$group"; done',
        'id grid >/dev/null 2>&1 || useradd -g oinstall -G asmadmin,asmdba,asmoper,dba grid',
        'id oracle >/dev/null 2>&1 || useradd -g oinstall -G dba,oper,backupdba,dgdba,kmdba,racdba,asmdba oracle',
        "sudo -iu grid true",
        "sudo -iu oracle true",
        f"mkdir -p {GRID_BASE_DIR} {GRID_BASE} {ORACLE_BASE} {DB_HOME} {config.installer.sources_path} {STAGE} {INVENTORY_LOCATION}",
        f"chown -R grid:oinstall {GRID_BASE_DIR} {GRID_BASE} {INVENTORY_LOCATION}",
        f"chown -R oracle:oinstall {ORACLE_BASE}",
        f"chmod -R 775 {GRID_BASE_DIR} {ORACLE_BASE} {INVENTORY_LOCATION}",
        *inventory_pointer_lines(),
        *_oracle_profile_lines(config, node),
        "cp -p /etc/resolv.conf /etc/resolv.conf.oracle-auto.bak.$(date +%Y%m%d%H%M%S) 2>/dev/null || true",
        f"cat > /etc/resolv.conf <<'EOF'\n{resolv_conf}\nEOF",
        "awk '/# BEGIN ORACLE-AUTO HOSTS/{skip=1} /# END ORACLE-AUTO HOSTS/{skip=0; next} !skip{print}' /etc/hosts > /etc/hosts.oracle-auto",
        "cat >> /etc/hosts.oracle-auto <<'EOF'\n# BEGIN ORACLE-AUTO HOSTS\n" + hosts_block + "\n# END ORACLE-AUTO HOSTS\nEOF",
        "cp /etc/hosts /etc/hosts.oracle-auto.bak.$(date +%Y%m%d%H%M%S)",
        "mv /etc/hosts.oracle-auto /etc/hosts",
        "systemctl disable --now firewalld 2>/dev/null || true",
        "systemctl disable --now iptables 2>/dev/null || true",
        "systemctl disable --now nftables 2>/dev/null || true",
        "setenforce 0 2>/dev/null || true",
        "test -f /etc/selinux/config && sed -i 's/^SELINUX=.*/SELINUX=permissive/' /etc/selinux/config || true",
        *ensure_swap_lines(),
        "swapon --show",
        "cp -p /etc/chrony.conf /etc/chrony.conf.oracle-auto.bak.$(date +%Y%m%d%H%M%S) 2>/dev/null || true",
        "sed -i '/^server /s/^/# oracle-auto disabled /; /^pool /s/^/# oracle-auto disabled /' /etc/chrony.conf 2>/dev/null || true",
        "awk '/# BEGIN ORACLE-AUTO CHRONY/{skip=1} /# END ORACLE-AUTO CHRONY/{skip=0; next} !skip{print}' /etc/chrony.conf > /etc/chrony.conf.oracle-auto",
        "cat >> /etc/chrony.conf.oracle-auto <<'EOF'\n# BEGIN ORACLE-AUTO CHRONY\n" + chrony_block + "\n# END ORACLE-AUTO CHRONY\nEOF",
        "mv /etc/chrony.conf.oracle-auto /etc/chrony.conf",
        "systemctl enable --now chronyd",
        "systemctl restart chronyd",
        "chronyc sources || true",
    ]
    return shell_script("Prepare OS baseline", lines)


def _oracle_profile_lines(config: AutomationConfig, node: NodeConfig) -> list[str]:
    site = config.site_for_node(node)
    oracle_sid = site.db_unique_name
    asm_sid = _asm_sid(config, node)
    grid_profile = "\n".join(
        [
            f"export ORACLE_BASE={GRID_BASE_DIR}",
            f"export ORACLE_HOME={GRID_BASE}",
            f"export GRID_HOME={GRID_BASE}",
            f"export DB_HOME={DB_HOME}",
            f"export ORACLE_SID={asm_sid}",
            "export TNS_ADMIN=$ORACLE_HOME/network/admin",
            "export PATH=$ORACLE_HOME/bin:$DB_HOME/bin:$PATH",
            "export LD_LIBRARY_PATH=$ORACLE_HOME/lib:${LD_LIBRARY_PATH:-}",
            "umask 022",
        ]
    )
    oracle_profile = "\n".join(
        [
            f"export ORACLE_BASE={ORACLE_BASE}",
            f"export ORACLE_HOME={DB_HOME}",
            f"export DB_HOME={DB_HOME}",
            f"export GRID_HOME={GRID_BASE}",
            f"export ORACLE_SID={oracle_sid}",
            "export TNS_ADMIN=$ORACLE_HOME/network/admin",
            "export PATH=$ORACLE_HOME/bin:$GRID_HOME/bin:$PATH",
            "export LD_LIBRARY_PATH=$ORACLE_HOME/lib:${LD_LIBRARY_PATH:-}",
            "umask 022",
        ]
    )
    return [
        *_profile_block_lines("grid", "GRID", grid_profile),
        *_profile_block_lines("oracle", "ORACLE", oracle_profile),
    ]


def _profile_block_lines(user: str, label: str, body: str) -> list[str]:
    home = f"/home/{user}"
    bashrc = f"{home}/.bashrc"
    bash_profile = f"{home}/.bash_profile"
    begin = f"# BEGIN ORACLE-AUTO {label} PROFILE"
    end = f"# END ORACLE-AUTO {label} PROFILE"
    return [
        f"touch {bashrc} {bash_profile}",
        f"awk '/{begin}/{{skip=1}} /{end}/{{skip=0; next}} !skip{{print}}' {bashrc} > {bashrc}.oracle-auto",
        f"cat >> {bashrc}.oracle-auto <<'EOF'\n{begin}\n{body}\n{end}\nEOF",
        f"mv {bashrc}.oracle-auto {bashrc}",
        f"if ! grep -q '# ORACLE-AUTO source bashrc' {bash_profile}; then cat >> {bash_profile} <<'EOF'\n# ORACLE-AUTO source bashrc\nif [ -f ~/.bashrc ]; then\n  . ~/.bashrc\nfi\nEOF\nfi",
        f"chown {user}:oinstall {bashrc} {bash_profile}",
    ]


def _asm_sid(config: AutomationConfig, node: NodeConfig) -> str:
    if config.install_type != "rac":
        return "+ASM"
    site = config.site_for_node(node)
    index = next(index for index, item in enumerate(site.nodes, start=1) if item.host == node.host)
    return f"+ASM{index}"


def _hosts_block(config: AutomationConfig) -> str:
    entries: list[str] = []
    for node in config.all_nodes:
        entries.append(_hosts_line(node.public_ip, node.host))
        if node.private_ip:
            entries.append(_hosts_line(node.private_ip, node.private_hostname))
        if node.vip_ip:
            entries.append(_hosts_line(node.vip_ip, node.vip_hostname))
    return "\n".join(entries)


def _hosts_line(ip: str, hostname: str) -> str:
    short = hostname.split(".", 1)[0]
    return f"{ip} {hostname} {short}"


def _resolv_conf(config: AutomationConfig) -> str:
    lines = ["# Generated by oracle-auto"]
    for resolver in config.dns.resolvers:
        lines.append(f"nameserver {resolver}")
    if config.dns.search_domains:
        lines.append("search " + " ".join(config.dns.search_domains))
    return "\n".join(lines)
