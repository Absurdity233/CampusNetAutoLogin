from __future__ import annotations

import argparse
import getpass
import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from .config import AppConfig, default_config_path, default_log_path, load_config, save_config
from .credentials import delete_credentials, read_credentials, write_credentials
from .logging_setup import configure_logging
from .network import current_wifi_ssid, has_internet, set_wifi_profile_autoconnect
from .service import attempt_login, run_forever, single_instance_mutex
from .startup import install_startup, read_startup_command, uninstall_startup


def _entry_script() -> Path:
    return Path(__file__).resolve().parents[1] / "campusnet.py"


def _require_windows() -> None:
    if os.name != "nt":
        raise SystemExit("此程序仅支持 Windows")


def _load_credentials_or_exit(config: AppConfig):
    credentials = read_credentials(config.credential_target)
    if credentials is None:
        raise SystemExit("尚未保存账号密码，请先运行：python campusnet.py setup")
    return credentials


def command_setup(_args) -> int:
    existing = load_config()
    detected_ssid = current_wifi_ssid()
    print("\n校园网自动登录配置（密码会保存到 Windows 凭据管理器）")
    print("登录网址可留空，程序会通过 Windows 联网检测地址触发跳转。\n")

    login_url = input(f"登录页面 URL [{existing.login_url or '自动检测'}]: ").strip()
    if not login_url:
        login_url = existing.login_url

    default_ssids = existing.allowed_ssids or ([detected_ssid] if detected_ssid else [])
    current_ssids = ",".join(default_ssids)
    ssids_text = input(
        f"校园网 Wi-Fi 名称，多个用英文逗号分隔 [{current_ssids or '必须填写'}]: "
    ).strip()
    allowed_ssids = (
        [item.strip() for item in ssids_text.split(",") if item.strip()]
        if ssids_text
        else default_ssids
    )
    if not allowed_ssids:
        raise SystemExit("为防止向其他热点泄露校园网密码，必须填写校园 Wi-Fi 名称")

    saved_credentials = read_credentials(existing.credential_target)
    username_default = saved_credentials.username if saved_credentials else ""
    username = input(f"账号 [{username_default}]: ").strip() or username_default
    if not username:
        raise SystemExit("账号不能为空")

    password_prompt = "密码（直接回车保留原密码）: " if saved_credentials else "密码: "
    password = getpass.getpass(password_prompt)
    if not password and saved_credentials:
        password = saved_credentials.password
    if not password:
        raise SystemExit("密码不能为空")

    config = AppConfig(**asdict(existing))
    config.login_url = login_url
    config.allowed_ssids = allowed_ssids or []
    config_path = save_config(config)
    write_credentials(config.credential_target, username, password)
    for ssid in config.allowed_ssids:
        if set_wifi_profile_autoconnect(ssid):
            print(f"已将 Wi-Fi 配置 {ssid!r} 设为自动连接。")
        else:
            print(f"提示：尚未找到 Wi-Fi 配置 {ssid!r}，首次手动连接后程序仍会主动重试。")
    print(f"\n配置已保存：{config_path}")
    print("账号密码已安全保存到 Windows 凭据管理器。")
    return 0


def command_run(args) -> int:
    config = load_config()
    credentials = _load_credentials_or_exit(config)
    configure_logging(console=bool(args.once or args.show_browser))
    if args.once or args.show_browser:
        with single_instance_mutex():
            return 0 if attempt_login(config, credentials, show_browser=args.show_browser) else 1
    run_forever()
    return 0


def command_install_startup(_args) -> int:
    command = install_startup(_entry_script())
    print("已创建登录 Windows 后运行的计划任务：")
    print(command)
    return 0


def command_uninstall_startup(_args) -> int:
    removed = uninstall_startup()
    print("已删除登录计划任务。" if removed else "未找到登录计划任务。")
    return 0


def command_status(_args) -> int:
    config = load_config()
    credentials = read_credentials(config.credential_target)
    startup = read_startup_command()
    status = {
        "config_path": str(default_config_path()),
        "log_path": str(default_log_path()),
        "login_url": config.login_url or "自动检测",
        "allowed_ssids": config.allowed_ssids or "任意已连接 Wi-Fi",
        "auto_connect_wifi": config.auto_connect_wifi,
        "credentials_saved": credentials is not None,
        "username": credentials.username if credentials else None,
        "startup_enabled": startup is not None,
        "startup_type": "scheduled_task",
        "startup_command": startup,
        "current_ssid": current_wifi_ssid(),
        "internet_available": has_internet(config),
    }
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


def command_open_config(_args) -> int:
    config_path = default_config_path()
    if not config_path.exists():
        save_config(AppConfig())
    subprocess.Popen(["notepad.exe", str(config_path)])
    return 0


def command_clear_credentials(_args) -> int:
    config = load_config()
    removed = delete_credentials(config.credential_target)
    print("已删除账号密码。" if removed else "未找到已保存的账号密码。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Windows 校园网自动登录")
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup_parser = subparsers.add_parser("setup", help="设置登录网址和账号密码")
    setup_parser.set_defaults(handler=command_setup)

    run_parser = subparsers.add_parser("run", help="运行自动登录服务")
    run_parser.add_argument("--once", action="store_true", help="只检测并登录一次")
    run_parser.add_argument("--show-browser", action="store_true", help="显示浏览器并登录一次")
    run_parser.set_defaults(handler=command_run)

    install_parser = subparsers.add_parser("install-startup", help="创建登录计划任务")
    install_parser.set_defaults(handler=command_install_startup)

    uninstall_parser = subparsers.add_parser("uninstall-startup", help="删除登录计划任务")
    uninstall_parser.set_defaults(handler=command_uninstall_startup)

    status_parser = subparsers.add_parser("status", help="查看运行配置与网络状态")
    status_parser.set_defaults(handler=command_status)

    config_parser = subparsers.add_parser("open-config", help="用记事本打开高级配置")
    config_parser.set_defaults(handler=command_open_config)

    clear_parser = subparsers.add_parser("clear-credentials", help="删除保存的账号密码")
    clear_parser.set_defaults(handler=command_clear_credentials)
    return parser


def main(argv: list[str] | None = None) -> int:
    _require_windows()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except KeyboardInterrupt:
        return 130
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        print(f"错误：{error}", file=sys.stderr)
        return 2
