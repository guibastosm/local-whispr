"""Screen capture + voice command via Ollama multimodal."""

from __future__ import annotations

import base64
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from localwhispr.config import OllamaConfig


def _capture_screenshot() -> bytes | None:
    """Capture screenshot of the screen. Tries multiple methods in order."""
    # Method 1: Simulate PrintScreen via ydotool and grab from clipboard (GNOME Wayland)
    if shutil.which("ydotool") and shutil.which("wl-paste"):
        img = _screenshot_via_printscreen()
        if img:
            return img

    # Method 2: gnome-screenshot directly to file
    if shutil.which("gnome-screenshot"):
        img = _screenshot_via_tool(["gnome-screenshot", "-f"])
        if img:
            return img

    # Method 3: grim (sway, other Wayland compositors)
    if shutil.which("grim"):
        img = _screenshot_via_tool(["grim"])
        if img:
            return img

    print("[localwhispr] ERROR: No screenshot method worked.")
    print("[localwhispr] On GNOME 49+, LocalWhispr uses PrintScreen + clipboard.")
    return None


def _screenshot_via_printscreen() -> bytes | None:
    """Simulate Shift+PrintScreen, GNOME captures full screen directly to clipboard."""
    import time

    try:
        # Simulate Shift+PrintScreen (Shift=42, PrintScreen=99)
        # GNOME maps Shift+Print to direct full screen capture
        subprocess.run(
            ["ydotool", "key", "42:1", "99:1", "99:0", "42:0"],
            timeout=2,
            capture_output=True,
        )

        # Wait for GNOME to process and copy to clipboard
        time.sleep(2.0)

        # Grab the image from clipboard
        result = subprocess.run(
            ["wl-paste", "--type", "image/png", "--no-newline"],
            capture_output=True,
            timeout=3,
        )

        if result.returncode == 0 and len(result.stdout) > 100:
            print(f"[localwhispr] Screenshot via Shift+PrintScreen+clipboard ({len(result.stdout)} bytes)")
            return result.stdout

        return None

    except Exception as e:
        print(f"[localwhispr] WARNING: screenshot via PrintScreen failed: {e}")
        return None


def _screenshot_via_tool(cmd_prefix: list[str]) -> bytes | None:
    """Capture screenshot via CLI tool that saves to file."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        result = subprocess.run(
            [*cmd_prefix, tmp_path],
            capture_output=True,
            timeout=5,
        )

        if result.returncode != 0:
            return None

        screenshot_path = Path(tmp_path)
        if screenshot_path.exists() and screenshot_path.stat().st_size > 0:
            return screenshot_path.read_bytes()
        return None

    except Exception:
        return None
    finally:
        Path(tmp_path).unlink(missing_ok=True)


class ScreenshotCommand:
    """Process voice commands with visual context (screenshot)."""

    def __init__(self, config: OllamaConfig | None = None) -> None:
        from localwhispr.config import OllamaConfig as OC

        cfg = config or OC()
        self._base_url = cfg.base_url.rstrip("/")
        self._model = cfg.vision_model

    def execute(self, voice_command: str) -> str:
        """Capture screenshot, combine with voice command and send to multimodal LLM."""
        if not voice_command.strip():
            return ""

        # Capture screenshot
        screenshot_bytes = _capture_screenshot()

        if screenshot_bytes is None:
            print("[localwhispr] Executing command without screenshot...")
            return self._text_only_command(voice_command)

        # Encode as base64 for the Ollama API
        screenshot_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")

        print(f"[localwhispr] Screenshot captured ({len(screenshot_bytes)} bytes)")
        print(f"[localwhispr] Voice command: {voice_command[:80]}...")

        try:
            response = httpx.post(
                f"{self._base_url}/api/generate",
                json={
                    "model": self._model,
                    "prompt": (
                        "You are looking at the user's computer screen. "
                        "The user made the following voice request:\n\n"
                        f'"{voice_command}"\n\n'
                        "Respond directly and helpfully based on what you see on the screen "
                        "and the user's request. Respond ONLY with the requested content, "
                        "no extra explanations. "
                        "IMPORTANT: Respond in the SAME LANGUAGE as the user's request."
                    ),
                    "images": [screenshot_b64],
                    "stream": False,
                    "options": {
                        "temperature": 0.3,
                        "num_predict": 4096,
                    },
                },
                timeout=60.0,
            )
            response.raise_for_status()
            data = response.json()
            result = data.get("response", "").strip()

            if result:
                print(f"[localwhispr] AI response: {result[:100]}...")
            return result

        except httpx.ConnectError:
            print(f"[localwhispr] ERROR: Could not connect to Ollama.")
            return f"[ERROR: Ollama not reachable at {self._base_url}]"
        except Exception as e:
            print(f"[localwhispr] ERROR in screenshot command: {e}")
            return f"[ERROR: {e}]"

    def _text_only_command(self, voice_command: str) -> str:
        """Fallback: execute command without screenshot."""
        try:
            response = httpx.post(
                f"{self._base_url}/api/generate",
                json={
                    "model": self._model,
                    "prompt": voice_command,
                    "stream": False,
                    "options": {
                        "temperature": 0.3,
                        "num_predict": 4096,
                    },
                },
                timeout=60.0,
            )
            response.raise_for_status()
            data = response.json()
            return data.get("response", "").strip()
        except Exception as e:
            print(f"[localwhispr] ERROR in text command: {e}")
            return f"[ERROR: {e}]"
