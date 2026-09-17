from __future__ import annotations

import re
import subprocess
import time
import urllib.error
import urllib.request

from .config import AppConfig


CREATE_NO_WINDOW = 0x08000000


def _run_netsh_wlan(arguments: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["netsh", "wlan", *arguments],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=15,
            check=False,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def has_internet(config: AppConfig) -> bool:
    request = urllib.request.Request(
        config.connectivity_url,
        headers={
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "User-Agent": "CampusNetAutoLogin/1.0",
        },
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=config.connectivity_timeout_seconds,
        ) as response:
            if response.status != 200:
                return False
            body = response.read(4096).decode("utf-8", errors="replace").strip()
            expected = config.connectivity_expected.strip()
            return not expected or body == expected
    except (OSError, urllib.error.URLError, ValueError):
        return False


def extract_ssid(netsh_output: str) -> str | None:
    match = re.search(r"^\s*SSID\s*:\s*(.*?)\s*$", netsh_output, flags=re.MULTILINE)
    if not match:
        return None
    ssid = match.group(1).strip()
    return ssid or None


def current_wifi_ssid() -> str | None:
    result = _run_netsh_wlan(["show", "interfaces"])
    if result is None:
        return None
    return extract_ssid(result.stdout)


def extract_available_ssids(netsh_output: str) -> list[str]:
    matches = re.findall(
        r"^\s*SSID\s+\d+\s*:\s*(.*?)\s*$",
        netsh_output,
        flags=re.MULTILINE,
    )
    return [ssid.strip() for ssid in matches if ssid.strip()]


def available_wifi_ssids() -> list[str]:
    result = _run_netsh_wlan(["show", "networks"])
    if result is None:
        return []
    return extract_available_ssids(result.stdout)


def connect_saved_wifi(ssid: str) -> bool:
    result = _run_netsh_wlan(["connect", f"name={ssid}", f"ssid={ssid}"])
    return result is not None and result.returncode == 0


def set_wifi_profile_autoconnect(ssid: str) -> bool:
    result = _run_netsh_wlan(
        ["set", "profileparameter", f"name={ssid}", "ConnectionMode=auto"]
    )
    return result is not None and result.returncode == 0


def connect_preferred_wifi(config: AppConfig) -> str | None:
    current = current_wifi_ssid()
    if ssid_is_allowed(config, current):
        return current

    deadline = time.monotonic() + config.wifi_wait_seconds
    while True:
        visible = set(available_wifi_ssids())
        target = next((ssid for ssid in config.allowed_ssids if ssid in visible), None)
        if target:
            connect_saved_wifi(target)
            while time.monotonic() < deadline:
                time.sleep(1)
                current = current_wifi_ssid()
                if current == target:
                    return current
            return current_wifi_ssid()

        if current is not None or time.monotonic() >= deadline:
            return current
        time.sleep(1)
        current = current_wifi_ssid()


def ssid_is_allowed(config: AppConfig, ssid: str | None) -> bool:
    if not config.allowed_ssids:
        return ssid is not None
    return ssid in config.allowed_ssids
