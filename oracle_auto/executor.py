"""SSH executor manual.

This module is the only place that opens SSH sessions. Runners pass a target
node and shell command here; dry-run mode returns the exact SSH command without
touching remote servers.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from threading import Thread
from typing import TextIO

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

    def run(self, node: NodeConfig, command: str, timeout: int | None = 60) -> CommandResult:
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
        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []
        process = subprocess.Popen(
            ssh_command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout_thread = Thread(
            target=_stream_output,
            args=(process.stdout, stdout_chunks, sys.stdout),
            daemon=True,
        )
        stderr_thread = Thread(
            target=_stream_output,
            args=(process.stderr, stderr_chunks, sys.stderr),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            stdout_thread.join()
            stderr_thread.join()
            stdout = "".join(stdout_chunks).strip()
            stderr = "".join(stderr_chunks).strip()
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
        stdout_thread.join()
        stderr_thread.join()
        return CommandResult(
            host=node.host,
            command=self._format_display_command(target, command),
            returncode=returncode,
            stdout="".join(stdout_chunks).strip(),
            stderr="".join(stderr_chunks).strip(),
        )

    def _build_ssh_command(self, target: str, command: str) -> list[str]:
        ssh_command = [
            "ssh",
            "-p",
            str(self.config.port),
            "-o",
            f"ConnectTimeout={self.config.connect_timeout}",
            "-o",
            f"ServerAliveInterval={self.config.server_alive_interval}",
            "-o",
            f"ServerAliveCountMax={self.config.server_alive_count_max}",
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


def _stream_output(source: TextIO | None, chunks: list[str], sink: TextIO) -> None:
    if source is None:
        return
    for line in iter(source.readline, ""):
        chunks.append(line)
        print(line, end="", file=sink, flush=True)
    source.close()
