"""Configure GNOME keyboard shortcuts for LocalWhispr."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys


SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
KEY = "custom-keybindings"
BASE_PATH = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings"

# Find the localwhispr binary in PATH
LOCALWHISPR_BIN = shutil.which("localwhispr")


def _run_gsettings(*args: str) -> str:
    """Execute gsettings and return stdout."""
    result = subprocess.run(
        ["gsettings", *args],
        capture_output=True, text=True
    )
    return result.stdout.strip()


def _run_dconf(*args: str) -> str:
    """Execute dconf and return stdout."""
    result = subprocess.run(
        ["dconf", *args],
        capture_output=True, text=True
    )
    return result.stdout.strip()


def _get_existing_custom_keybindings() -> list[str]:
    """Return list of existing custom keybinding paths."""
    raw = _run_gsettings("get", SCHEMA, KEY)
    if raw in ("@as []", "[]", ""):
        return []
    try:
        return json.loads(raw.replace("'", '"'))
    except json.JSONDecodeError:
        return []


def _find_localwhispr_slots(existing: list[str]) -> dict[str, str]:
    """Find slots already used by LocalWhispr."""
    slots = {}
    for path in existing:
        name = _run_dconf("read", f"{path}name")
        if "LocalWhispr" in name:
            cmd = _run_dconf("read", f"{path}command")
            if "toggle-service" in cmd.lower() or "Toggle" in name:
                slots["toggle_service"] = path
            elif "dictate" in cmd:
                slots["dictate"] = path
            elif "screenshot" in cmd:
                slots["screenshot"] = path
            elif "meeting" in cmd:
                slots["meeting"] = path
    return slots


def _next_slot_index(existing: list[str]) -> int:
    """Find the next free index for a custom keybinding."""
    used = set()
    for path in existing:
        try:
            idx = int(path.rstrip("/").split("custom")[-1])
            used.add(idx)
        except (ValueError, IndexError):
            continue
    i = 0
    while i in used:
        i += 1
    return i


def _write_keybinding(path: str, name: str, command: str, binding: str) -> None:
    """Write a custom keybinding via dconf."""
    # dconf expects GVariant string format: 'value' with inner quotes escaped
    def _gvariant_str(s: str) -> str:
        escaped = s.replace("'", "\\'")
        return f"'{escaped}'"

    subprocess.run(["dconf", "write", f"{path}name", _gvariant_str(name)], check=True)
    subprocess.run(["dconf", "write", f"{path}command", _gvariant_str(command)], check=True)
    subprocess.run(["dconf", "write", f"{path}binding", _gvariant_str(binding)], check=True)


def setup_gnome_shortcuts(
    toggle_service_binding: str = "<Ctrl><Super>w",
    dictate_binding: str = "<Ctrl><Shift>d",
    screenshot_binding: str = "<Ctrl><Shift>s",
    meeting_binding: str = "<Ctrl><Shift>m",
) -> None:
    """Register (or update) GNOME shortcuts for LocalWhispr."""
    # Check if gsettings/dconf are available
    if not shutil.which("gsettings") or not shutil.which("dconf"):
        print("[localwhispr] ERROR: gsettings or dconf not found.")
        print("[localwhispr] Install with: sudo pacman -S dconf")
        sys.exit(1)

    # Determine base command
    if LOCALWHISPR_BIN:
        base_cmd = LOCALWHISPR_BIN
    else:
        # Fallback: use current venv path
        import os
        venv = os.environ.get("VIRTUAL_ENV")
        if venv:
            base_cmd = f"{venv}/bin/localwhispr"
        else:
            base_cmd = "localwhispr"

    # Toggle service uses a bash one-liner to start/stop the systemd service
    toggle_service_cmd = (
        'bash -c "if systemctl --user is-active --quiet localwhispr; then '
        'systemctl --user stop localwhispr && '
        'canberra-gtk-play -i service-logout; else '
        'systemctl --user start localwhispr && '
        'canberra-gtk-play -i service-login; fi"'
    )

    dictate_cmd = f"{base_cmd} ctl dictate"
    screenshot_cmd = f"{base_cmd} ctl screenshot"
    meeting_cmd = f"{base_cmd} ctl meeting"

    existing = _get_existing_custom_keybindings()
    lw_slots = _find_localwhispr_slots(existing)

    new_paths = list(existing)

    # --- Toggle service shortcut ---
    if "toggle_service" in lw_slots:
        path = lw_slots["toggle_service"]
        print(f"[localwhispr] Updating toggle service shortcut at {path}")
    else:
        idx = _next_slot_index(new_paths)
        path = f"{BASE_PATH}/custom{idx}/"
        new_paths.append(path)
        print(f"[localwhispr] Creating toggle service shortcut at {path}")

    _write_keybinding(path, "LocalWhispr Toggle Service", toggle_service_cmd, toggle_service_binding)
    print(f"  → {toggle_service_binding} → toggle service on/off")

    # --- Dictation shortcut ---
    if "dictate" in lw_slots:
        path = lw_slots["dictate"]
        print(f"[localwhispr] Updating dictation shortcut at {path}")
    else:
        idx = _next_slot_index(new_paths)
        path = f"{BASE_PATH}/custom{idx}/"
        new_paths.append(path)
        print(f"[localwhispr] Creating dictation shortcut at {path}")

    _write_keybinding(path, "LocalWhispr Dictation", dictate_cmd, dictate_binding)
    print(f"  → {dictate_binding} → {dictate_cmd}")

    # --- Screenshot shortcut ---
    if "screenshot" in lw_slots:
        path = lw_slots["screenshot"]
        print(f"[localwhispr] Updating screenshot shortcut at {path}")
    else:
        idx = _next_slot_index(new_paths)
        path = f"{BASE_PATH}/custom{idx}/"
        new_paths.append(path)
        print(f"[localwhispr] Creating screenshot shortcut at {path}")

    _write_keybinding(path, "LocalWhispr Screenshot", screenshot_cmd, screenshot_binding)
    print(f"  → {screenshot_binding} → {screenshot_cmd}")

    # --- Meeting shortcut ---
    if "meeting" in lw_slots:
        path = lw_slots["meeting"]
        print(f"[localwhispr] Updating meeting shortcut at {path}")
    else:
        idx = _next_slot_index(new_paths)
        path = f"{BASE_PATH}/custom{idx}/"
        new_paths.append(path)
        print(f"[localwhispr] Creating meeting shortcut at {path}")

    _write_keybinding(path, "LocalWhispr Meeting", meeting_cmd, meeting_binding)
    print(f"  → {meeting_binding} → {meeting_cmd}")

    # --- Update custom keybindings list ---
    paths_str = str(new_paths).replace('"', "'")
    subprocess.run(
        ["gsettings", "set", SCHEMA, KEY, paths_str],
        check=True,
    )

    print()
    print("[localwhispr] Shortcuts configured successfully!")
    print("[localwhispr] Verify at: Settings > Keyboard > Shortcuts > Custom Shortcuts")
    print()
    print("  Service (on/off):    " + toggle_service_binding)
    print("  Dictation (toggle):  " + dictate_binding)
    print("  Screenshot + AI:     " + screenshot_binding)
    print("  Meeting (toggle):    " + meeting_binding)
