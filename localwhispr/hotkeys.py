"""Global hotkeys via evdev for Wayland."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Callable

import evdev
from evdev import InputDevice, ecodes

from localwhispr.config import HotkeyConfig


def _find_keyboard_devices() -> list[InputDevice]:
    """Find all available keyboard devices."""
    devices = []
    for path in sorted(Path("/dev/input/").glob("event*")):
        try:
            dev = InputDevice(str(path))
            caps = dev.capabilities(verbose=False)
            # Check if device has keyboard keys (EV_KEY)
            if ecodes.EV_KEY in caps:
                key_caps = caps[ecodes.EV_KEY]
                # Check if it has common keyboard keys (A-Z)
                if ecodes.KEY_A in key_caps and ecodes.KEY_Z in key_caps:
                    devices.append(dev)
                else:
                    dev.close()
            else:
                dev.close()
        except (PermissionError, OSError):
            continue
    return devices


def _key_name_to_code(name: str) -> int:
    """Convert evdev key name (e.g. 'KEY_LEFTCTRL') to numeric code."""
    code = getattr(ecodes, name, None)
    if code is None:
        raise ValueError(f"Unknown key: {name}. Use evdev names like KEY_LEFTCTRL, KEY_A, etc.")
    return code


class HotkeyListener:
    """Listens to global hotkeys via evdev and fires callbacks."""

    def __init__(
        self,
        config: HotkeyConfig,
        on_dictation_start: Callable[[], None],
        on_dictation_stop: Callable[[], None],
        on_screenshot_start: Callable[[], None],
        on_screenshot_stop: Callable[[], None],
    ) -> None:
        self._config = config

        # Callbacks
        self._on_dictation_start = on_dictation_start
        self._on_dictation_stop = on_dictation_stop
        self._on_screenshot_start = on_screenshot_start
        self._on_screenshot_stop = on_screenshot_stop

        # Convert names to codes
        self._dictation_keys = {_key_name_to_code(k) for k in config.dictation}
        self._screenshot_keys = {_key_name_to_code(k) for k in config.screenshot_command}

        # State
        self._pressed_keys: set[int] = set()
        self._dictation_active = False
        self._screenshot_active = False
        self._dictation_press_time: float = 0
        self._mode = config.mode
        self._hold_threshold = config.hold_threshold_ms / 1000.0

    async def run(self) -> None:
        """Main hotkey listening loop."""
        devices = _find_keyboard_devices()
        if not devices:
            print("[localwhispr] ERROR: No keyboard found.")
            print("[localwhispr] Check if your user is in the 'input' group:")
            print("[localwhispr]   sudo usermod -aG input $USER")
            print("[localwhispr]   (requires logout/login)")
            return

        print(f"[localwhispr] Listening on {len(devices)} keyboard device(s)")
        for dev in devices:
            print(f"[localwhispr]   - {dev.name} ({dev.path})")

        dict_names = " + ".join(self._config.dictation)
        screenshot_names = " + ".join(self._config.screenshot_command)
        print(f"[localwhispr] Hotkeys: dictation={dict_names}, screenshot={screenshot_names}")
        print(f"[localwhispr] Mode: {self._mode}")

        tasks = [asyncio.create_task(self._listen_device(dev)) for dev in devices]
        await asyncio.gather(*tasks)

    async def _listen_device(self, device: InputDevice) -> None:
        """Listen to events from a specific device."""
        try:
            async for event in device.async_read_loop():
                if event.type != ecodes.EV_KEY:
                    continue

                key_event = evdev.categorize(event)
                code = key_event.scancode

                if key_event.keystate == evdev.KeyEvent.key_down:
                    self._pressed_keys.add(code)
                    self._handle_key_down()
                elif key_event.keystate == evdev.KeyEvent.key_up:
                    self._handle_key_up(code)
                    self._pressed_keys.discard(code)

        except (OSError, IOError) as e:
            print(f"[localwhispr] Device disconnected: {device.name} ({e})")

    def _handle_key_down(self) -> None:
        """Process key press."""
        # Check dictation combo
        if self._dictation_keys.issubset(self._pressed_keys):
            if not self._dictation_active and not self._screenshot_active:
                self._dictation_press_time = time.monotonic()

                if self._mode == "hold":
                    self._dictation_active = True
                    self._on_dictation_start()
                elif self._mode == "toggle":
                    # Toggle: start/stop on key_down
                    pass  # Handled in _handle_dictation_toggle
                elif self._mode == "both":
                    # In "both" mode, start recording immediately
                    # If released quickly (< threshold), treat as toggle
                    self._dictation_active = True
                    self._on_dictation_start()

        # Check screenshot combo
        if self._screenshot_keys.issubset(self._pressed_keys):
            if not self._screenshot_active and not self._dictation_active:
                self._screenshot_active = True
                self._on_screenshot_start()

    def _handle_key_up(self, released_code: int) -> None:
        """Process key release."""
        # Screenshot: release any key in combo -> stop
        if self._screenshot_active and released_code in self._screenshot_keys:
            self._screenshot_active = False
            self._on_screenshot_stop()
            return

        # Dictation: logic depends on mode
        if released_code in self._dictation_keys:
            if self._mode == "hold" and self._dictation_active:
                self._dictation_active = False
                self._on_dictation_stop()

            elif self._mode == "toggle":
                if not self._dictation_active:
                    self._dictation_active = True
                    self._on_dictation_start()
                else:
                    self._dictation_active = False
                    self._on_dictation_stop()

            elif self._mode == "both" and self._dictation_active:
                elapsed = time.monotonic() - self._dictation_press_time
                if elapsed < self._hold_threshold:
                    # Short press: toggle mode — keep recording, stop on next press
                    pass  # Keep recording, will be stopped on next key_down
                else:
                    # Long press: hold mode — stop recording
                    self._dictation_active = False
                    self._on_dictation_stop()

    def stop_if_active(self) -> None:
        """Stop recording if active (for external use in toggle mode)."""
        if self._dictation_active:
            self._dictation_active = False
            self._on_dictation_stop()
