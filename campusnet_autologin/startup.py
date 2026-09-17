from __future__ import annotations

import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


TASK_NAME = "CampusNetAutoLogin"
LEGACY_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
LEGACY_VALUE_NAME = "CampusNetAutoLogin"
CREATE_NO_WINDOW = 0x08000000
TASK_XML_NAMESPACE = {"task": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


def startup_command(entry_script: Path) -> str:
    executable = Path(sys.executable)
    pythonw = executable.with_name("pythonw.exe")
    if not pythonw.exists():
        pythonw = executable
    return subprocess.list2cmdline([str(pythonw), str(entry_script.resolve()), "run"])


def _run_schtasks(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["schtasks.exe", *arguments],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
            check=False,
            creationflags=CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as error:
        raise OSError("计划任务操作超时") from error
    except OSError as error:
        raise OSError("无法调用 Windows 计划任务工具 schtasks.exe") from error


def _task_error(action: str, result: subprocess.CompletedProcess[str]) -> OSError:
    detail = (result.stderr or result.stdout or "未知错误").strip()
    return OSError(f"{action}计划任务失败：{detail}")


def _delete_legacy_run_value() -> bool:
    try:
        import winreg
    except ImportError:
        return False
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            LEGACY_RUN_KEY,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.DeleteValue(key, LEGACY_VALUE_NAME)
        return True
    except FileNotFoundError:
        return False


def install_startup(entry_script: Path) -> str:
    command = startup_command(entry_script)
    result = _run_schtasks(
        [
            "/Create",
            "/TN",
            TASK_NAME,
            "/SC",
            "ONLOGON",
            "/DELAY",
            "0000:10",
            "/RL",
            "LIMITED",
            "/IT",
            "/TR",
            command,
            "/F",
        ]
    )
    if result.returncode != 0:
        raise _task_error("创建", result)
    _delete_legacy_run_value()
    return command


def uninstall_startup() -> bool:
    scheduled = read_startup_command() is not None
    if scheduled:
        result = _run_schtasks(["/Delete", "/TN", TASK_NAME, "/F"])
        if result.returncode != 0:
            raise _task_error("删除", result)
    legacy_removed = _delete_legacy_run_value()
    return scheduled or legacy_removed


def read_startup_command() -> str | None:
    result = _run_schtasks(["/Query", "/TN", TASK_NAME, "/XML"])
    if result.returncode != 0:
        return None
    try:
        root = ET.fromstring(result.stdout)
    except ET.ParseError:
        return f"计划任务：{TASK_NAME}"

    command = root.findtext(".//task:Exec/task:Command", namespaces=TASK_XML_NAMESPACE)
    arguments = root.findtext(".//task:Exec/task:Arguments", namespaces=TASK_XML_NAMESPACE)
    if not command:
        return f"计划任务：{TASK_NAME}"
    return " ".join(part for part in (command, arguments) if part)
