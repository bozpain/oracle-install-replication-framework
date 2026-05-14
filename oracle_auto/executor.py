"""SSH executor manual.

This module is the only place that opens SSH sessions. Runners pass a target
node and shell command here; dry-run mode returns the exact SSH command without
touching remote servers.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from oracle_auto.config import NodeConfig, SSHConfig


@dataclass(frozen=True)
class CommandResult:
    host: str
    command: str
    returncode: int
    stdout: str
    stderr: str
    skipped: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class SSHExecutor:
    def __init__(self, config: SSHConfig, dry_run: bool = False):
        self.config = config
        self.dry_run = dry_run

    def run(self, node: NodeConfig, command: str, timeout: int = 60) -> CommandResult:
        user = node.ssh_user or self.config.user
        target = f"{user}@{node.host}"

        if self.dry_run:
            return CommandResult(
                host=node.host,
                command=self._format_display_command(target, command),
                returncode=0,
                stdout="DRY-RUN",
                stderr="",
                skipped=True,
            )

        ssh_command = self._build_ssh_command(target, command)
        try:
            completed = subprocess.run(
                ssh_command,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            detail = f"Command timed out after {timeout} seconds."
            if stderr.strip():
                detail = f"{detail}\n{stderr.strip()}"
            return CommandResult(
                host=node.host,
                command=self._format_display_command(target, command),
                returncode=124,
                stdout=stdout.strip(),
                stderr=detail,
            )
        return CommandResult(
            host=node.host,
            command=self._format_display_command(target, command),
            returncode=completed.returncode,
            stdout=completed.stdout.strip(),
            stderr=completed.stderr.strip(),
        )

    def _build_ssh_command(self, target: str, command: str) -> list[str]:
        ssh_command = [
            "ssh",
            "-p",
            str(self.config.port),
            "-o",
            f"ConnectTimeout={self.config.connect_timeout}",
            "-o",
            "BatchMode=yes",
            "-o",
            f"StrictHostKeyChecking={self.config.strict_host_key_checking}",
        ]
        if self.config.key_file:
            ssh_command.extend(["-i", self.config.key_file])
        ssh_command.extend([target, command])
        return ssh_command

    @staticmethod
    def _format_display_command(target: str, command: str) -> str:
        return f"ssh {target} {command!r}"
