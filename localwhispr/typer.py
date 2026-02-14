"""Text injection into focused app via ydotool or wtype (Wayland)."""

from __future__ import annotations

import shutil
import subprocess
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from localwhispr.config import TypingConfig


def _has_command(cmd: str) -> bool:
    return shutil.which(cmd) is not None


class Typer:
    """Types text into the currently focused app using Wayland tools."""

    def __init__(self, config: TypingConfig | None = None) -> None:
        from localwhispr.config import TypingConfig as TC

        cfg = config or TC()
        self._method = cfg.method
        self._delay_ms = cfg.delay_ms
        self._validated = False

    def _validate(self) -> None:
        if self._validated:
            return

        if self._method == "ydotool" and not _has_command("ydotool"):
            print("[localwhispr] WARNING: ydotool not found, trying wtype...")
            if _has_command("wtype"):
                self._method = "wtype"
            else:
                raise RuntimeError(
                    "No typing tool found. "
                    "Install ydotool or wtype: sudo pacman -S ydotool wtype"
                )
        elif self._method == "wtype" and not _has_command("wtype"):
            print("[localwhispr] WARNING: wtype not found, trying ydotool...")
            if _has_command("ydotool"):
                self._method = "ydotool"
            else:
                raise RuntimeError(
                    "No typing tool found. "
                    "Install ydotool or wtype: sudo pacman -S ydotool wtype"
                )

        self._validated = True

    def type_text(self, text: str) -> None:
        """Type text into the focused app."""
        if not text:
            return

        self._validate()

        # Always use clipboard for text with Unicode/accented characters
        # ydotool type doesn't handle well characters like ã, í, ç, õ, etc.
        self._type_clipboard(text)

    def _type_ydotool(self, text: str) -> None:
        """Type using ydotool (works on most Wayland compositors)."""
        try:
            result = subprocess.run(
                ["ydotool", "type", "--key-delay", str(self._delay_ms), "--", text],
                capture_output=True,
                timeout=30,
            )
            if result.returncode != 0:
                stderr = result.stderr.decode().strip()
                if "failed to connect" in stderr.lower() or "socket" in stderr.lower():
                    print("[localwhispr] ERROR: ydotoold is not running.")
                    print("[localwhispr] Run: systemctl --user enable --now ydotool")
                    # Fallback to clipboard
                    self._type_clipboard(text)
                else:
                    print(f"[localwhispr] ERROR ydotool: {stderr}")
                    self._type_clipboard(text)
        except FileNotFoundError:
            print("[localwhispr] ERROR: ydotool not found.")
            self._type_clipboard(text)
        except subprocess.TimeoutExpired:
            print("[localwhispr] ERROR: ydotool timeout.")
        except Exception as e:
            print(f"[localwhispr] ERROR ydotool: {e}")
            self._type_clipboard(text)

    def _type_wtype(self, text: str) -> None:
        """Type using wtype (requires virtual-keyboard protocol support)."""
        try:
            result = subprocess.run(
                ["wtype", "--delay", str(self._delay_ms), "--", text],
                capture_output=True,
                timeout=30,
            )
            if result.returncode != 0:
                print(f"[localwhispr] ERROR wtype: {result.stderr.decode().strip()}")
                self._type_clipboard(text)
        except Exception as e:
            print(f"[localwhispr] ERROR wtype: {e}")
            self._type_clipboard(text)

    def _type_clipboard(self, text: str) -> None:
        """Copy to clipboard via wl-copy and simulate Ctrl+V.

        The wl-copy process stays alive to keep the text in the clipboard,
        allowing the user to paste again with Ctrl+V.
        """
        print("[localwhispr] Typing via clipboard + Ctrl+V")
        try:
            if not _has_command("wl-copy"):
                print("[localwhispr] ERROR: wl-copy not found. Install: sudo pacman -S wl-clipboard")
                return

            # Kill previous wl-copy (if exists) before starting a new one
            self._kill_prev_wl_copy()

            # wl-copy on Wayland is a "clipboard owner" -- needs to stay alive
            # to keep content in clipboard. We keep it alive until next use.
            self._wl_copy_proc = subprocess.Popen(
                ["wl-copy", "--", text],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )

            # Brief wait for clipboard to register
            time.sleep(0.15)

            # Simulate Ctrl+V to paste
            paste_ok = False
            if _has_command("ydotool"):
                result = subprocess.run(
                    ["ydotool", "key", "29:1", "47:1", "47:0", "29:0"],
                    capture_output=True,
                    timeout=10,
                )
                paste_ok = result.returncode == 0
            elif _has_command("wtype"):
                result = subprocess.run(
                    ["wtype", "-M", "ctrl", "-k", "v"],
                    capture_output=True,
                    timeout=10,
                )
                paste_ok = result.returncode == 0

            if not paste_ok:
                print("[localwhispr] WARNING: failed to simulate Ctrl+V. Text is in clipboard, paste manually.")

            # DO NOT kill wl-copy — it stays alive to keep clipboard persistent.
            # Will be killed only when new text is copied.

        except Exception as e:
            print(f"[localwhispr] ERROR in clipboard: {e}")

    def _kill_prev_wl_copy(self) -> None:
        """Kill the previous wl-copy process, if it exists."""
        proc = getattr(self, "_wl_copy_proc", None)
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
