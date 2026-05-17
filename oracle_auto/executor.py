"""SSH executor manual.

This module is the only place that opens SSH sessions. Runners pass a target
node and shell command here; dry-run mode returns the exact SSH command without
touching remote servers.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from threading import Thread
from typing import Any, TextIO

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
            args=(process.stdout, stdout_chunks, sys.stdout, node.host),
            daemon=True,
        )
        stderr_thread = Thread(
            target=_stream_output,
            args=(process.stderr, stderr_chunks, sys.stderr, node.host),
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

    def transfer(self, transfer: Any, timeout: int | None = 60) -> CommandResult:
        source_target = self._target(transfer.source_node)
        target_target = self._target(transfer.target_node)
        display = (
            f"scp {source_target}:{transfer.source_path} "
            f"{target_target}:{transfer.target_path}"
        )
        if self.dry_run:
            return CommandResult(
                host=transfer.target_node.host,
                command=display,
                returncode=0,
                stdout="DRY-RUN",
                stderr="",
                skipped=True,
            )

        with tempfile.TemporaryDirectory(prefix="oracle-auto-transfer-") as tmp:
            local_path = Path(tmp) / Path(transfer.source_path).name
            pull = self._build_scp_command(f"{source_target}:{transfer.source_path}", str(local_path))
            push = self._build_scp_command(str(local_path), f"{target_target}:{transfer.target_path}")

            pull_result = _run_local(pull, timeout=timeout)
            if not pull_result.ok:
                return CommandResult(
                    host=transfer.target_node.host,
                    command=display,
                    returncode=pull_result.returncode,
                    stdout=pull_result.stdout,
                    stderr=pull_result.stderr,
                )
            push_result = _run_local(push, timeout=timeout)
            return CommandResult(
                host=transfer.target_node.host,
                command=display,
                returncode=push_result.returncode,
                stdout="\n".join(part for part in [pull_result.stdout, push_result.stdout] if part).strip(),
                stderr="\n".join(part for part in [pull_result.stderr, push_result.stderr] if part).strip(),
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

    def _build_scp_command(self, source: str, target: str) -> list[str]:
        scp_command = [
            "scp",
            "-P",
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
            scp_command.extend(["-i", self.config.key_file])
        scp_command.extend([source, target])
        return scp_command

    def _target(self, node: NodeConfig) -> str:
        user = node.ssh_user or self.config.user
        return f"{user}@{node.host}"

    @staticmethod
    def _format_display_command(target: str, command: str) -> str:
        return f"ssh {target} {command!r}"


def _stream_output(source: TextIO | None, chunks: list[str], sink: TextIO, host: str) -> None:
    if source is None:
        return
    for line in iter(source.readline, ""):
        chunks.append(line)
        print(f"[{host}] {line}", end="", file=sink, flush=True)
    source.close()


def _run_local(command: list[str], timeout: int | None) -> CommandResult:
    try:
        process = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            host="local",
            command=" ".join(command),
            returncode=124,
            stdout=(exc.stdout or "").strip() if isinstance(exc.stdout, str) else "",
            stderr=f"Command timed out after {timeout} seconds.",
        )
    return CommandResult(
        host="local",
        command=" ".join(command),
        returncode=process.returncode,
        stdout=process.stdout.strip(),
        stderr=process.stderr.strip(),
    )
