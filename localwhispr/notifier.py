"""LocalWhispr status notifications via libnotify and sounds."""

from __future__ import annotations

import subprocess
import shutil

from localwhispr.config import NotificationConfig


def _has_command(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def notify(title: str, body: str = "", config: NotificationConfig | None = None) -> None:
    """Send a notification via notify-send."""
    if config and not config.enabled:
        return

    if not _has_command("notify-send"):
        return

    try:
        cmd = [
            "notify-send",
            "--app-name=LocalWhispr",
            "--transient",
            "--urgency=low",
            title,
        ]
        if body:
            cmd.append(body)
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def play_sound(sound_name: str = "message", config: NotificationConfig | None = None) -> None:
    """Play a feedback sound using canberra-gtk-play or paplay."""
    if config and not config.sound:
        return

    if _has_command("canberra-gtk-play"):
        try:
            subprocess.Popen(
                ["canberra-gtk-play", "-i", sound_name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass


def notify_recording_start(config: NotificationConfig | None = None) -> None:
    # Sound only — no visual notification to avoid flooding the panel
    play_sound("dialog-information", config)


def notify_recording_stop(config: NotificationConfig | None = None) -> None:
    # Sound only — no visual notification to avoid flooding the panel
    play_sound("message-sent-instant", config)


def notify_done(text: str, config: NotificationConfig | None = None) -> None:
    # Sound only — the text has already been pasted into the focused app
    play_sound("message", config)


def notify_error(error: str, config: NotificationConfig | None = None) -> None:
    # Errors deserve a visual notification + sound
    notify("LocalWhispr - Error", error, config)
    play_sound("dialog-error", config)
