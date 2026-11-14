"""Spawn and wait for a headless ``sakaki-gm --server`` child process."""

from __future__ import annotations

import subprocess
import sys
import time
from typing import Sequence

from sakaki_gm.net.client import TableClient, TableClientError
from sakaki_gm.net.protocol import DEFAULT_PORT


def headless_server_command(
    *,
    port: int = DEFAULT_PORT,
    bind: str = "127.0.0.1",
    secret: str = "",
    campaign: str = "",
    verbose: bool = False,
    python: str | None = None,
) -> list[str]:
    cmd = [
        python or sys.executable,
        "-m",
        "sakaki_gm",
        "--server",
        "--bind",
        bind,
        "--port",
        str(int(port)),
    ]
    if secret:
        cmd.extend(["--secret", secret])
    if campaign:
        cmd.extend(["--campaign", campaign])
    if verbose:
        cmd.append("-v")
    return cmd


def wait_for_table(
    base_url: str,
    *,
    secret: str = "",
    timeout: float = 15.0,
    interval: float = 0.1,
) -> None:
    """Poll ``/health`` until the table accepts connections."""
    client = TableClient(base_url, secret=secret)
    deadline = time.monotonic() + max(0.5, float(timeout))
    last_error = "table not ready"
    while time.monotonic() < deadline:
        try:
            client.health()
            return
        except TableClientError as exc:
            last_error = str(exc)
        except Exception as exc:  # noqa: BLE001 — connection refused, etc.
            last_error = str(exc)
        time.sleep(interval)
    raise TimeoutError(
        f"Timed out waiting for table at {base_url}: {last_error}"
    )


def spawn_headless_server(
    *,
    port: int = DEFAULT_PORT,
    bind: str = "127.0.0.1",
    secret: str = "",
    campaign: str = "",
    verbose: bool = False,
    python: str | None = None,
    wait: bool = True,
    timeout: float = 15.0,
) -> subprocess.Popen:
    """Start ``python -m sakaki_gm --server`` unbound (unless campaign pinned)."""
    cmd = headless_server_command(
        port=port,
        bind=bind,
        secret=secret,
        campaign=campaign,
        verbose=verbose,
        python=python,
    )
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    if not wait:
        return proc
    # Prefer loopback for the desk even when the service binds 0.0.0.0.
    join_host = "127.0.0.1" if bind in {"0.0.0.0", "::", ""} else bind
    url = f"http://{join_host}:{int(port)}"
    try:
        wait_for_table(url, secret=secret, timeout=timeout)
    except Exception:
        terminate_service(proc)
        raise
    return proc


def terminate_service(
    proc: subprocess.Popen | None, *, timeout: float = 3.0
) -> None:
    if proc is None:
        return
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            pass
