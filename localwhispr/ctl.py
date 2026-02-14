"""CLI client to send commands to the LocalWhispr daemon via Unix socket."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

SOCKET_PATH = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "localwhispr.sock"


async def send_command(command: str) -> str:
    """Send a command to the daemon and return the response."""
    if not SOCKET_PATH.exists():
        print("[localwhispr] Daemon is not running.")
        print("[localwhispr] Start with: localwhispr serve")
        sys.exit(1)

    try:
        reader, writer = await asyncio.open_unix_connection(str(SOCKET_PATH))
        writer.write(command.encode())
        await writer.drain()

        response = await asyncio.wait_for(reader.read(4096), timeout=5.0)
        writer.close()
        await writer.wait_closed()
        return response.decode().strip()

    except ConnectionRefusedError:
        print("[localwhispr] Daemon is not responding.")
        print("[localwhispr] Restart with: localwhispr serve")
        sys.exit(1)
    except asyncio.TimeoutError:
        print("[localwhispr] Timeout waiting for daemon response.")
        sys.exit(1)


def ctl_main(args: list[str]) -> None:
    """Entry point for the 'ctl' subcommand."""
    if not args:
        print("Usage: localwhispr ctl <command>")
        print()
        print("Available commands:")
        print("  dictate      Toggle dictation recording (start/stop)")
        print("  screenshot   Toggle screenshot + multimodal AI recording")
        print("  meeting      Toggle meeting recording (mic + system audio)")
        print("  status       Check current daemon status")
        print("  stop         Cancel ongoing recording")
        print("  ping         Check if daemon is alive")
        print("  quit         Shut down the daemon")
        sys.exit(0)

    command = args[0]
    valid_commands = {"dictate", "screenshot", "meeting", "status", "stop", "ping", "quit"}

    if command not in valid_commands:
        print(f"[localwhispr] Unknown command: {command}")
        print(f"[localwhispr] Valid commands: {', '.join(sorted(valid_commands))}")
        sys.exit(1)

    response = asyncio.run(send_command(command))
    print(response)
