from __future__ import annotations

import subprocess
import sys
from pathlib import Path


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "CampusNetAutoLogin"


def _winreg():
    try:
        import winreg
    except ImportError as error:
        raise OSError("开机启动设置仅支持 Windows") from error
    return winreg


def startup_command(entry_script: Path) -> str:
    executable = Path(sys.executable)
    pythonw = executable.with_name("pythonw.exe")
    if not pythonw.exists():
        pythonw = executable
    return subprocess.list2cmdline([str(pythonw), str(entry_script.resolve()), "run"])


def install_startup(entry_script: Path) -> str:
    winreg = _winreg()
    command = startup_command(entry_script)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command)
    return command


def uninstall_startup() -> bool:
    winreg = _winreg()
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            RUN_KEY,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.DeleteValue(key, VALUE_NAME)
        return True
    except FileNotFoundError:
        return False


def read_startup_command() -> str | None:
    winreg = _winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return str(value)
    except FileNotFoundError:
        return None
