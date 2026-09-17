from __future__ import annotations

import ctypes
import logging
import time
from contextlib import contextmanager

from .config import AppConfig, load_config
from .credentials import Credentials, read_credentials
from .network import connect_preferred_wifi, current_wifi_ssid, has_internet, ssid_is_allowed
from .portal import PortalLoginError, login_to_portal


LOGGER = logging.getLogger(__name__)
ERROR_ALREADY_EXISTS = 183


@contextmanager
def single_instance_mutex():
    if not hasattr(ctypes, "WinDLL"):
        yield
        return

    ctypes.set_last_error(0)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_bool
    handle = kernel32.CreateMutexW(None, False, "Local\\CampusNetAutoLogin")
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())

    try:
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            raise RuntimeError("校园网自动登录程序已经在运行")
        yield
    finally:
        kernel32.CloseHandle(handle)


def _wait_for_ssid(config: AppConfig) -> str | None:
    ssid = current_wifi_ssid()
    if ssid is not None or config.wifi_wait_seconds <= 0:
        return ssid

    LOGGER.info("等待 Wi-Fi 连接，最长 %d 秒", config.wifi_wait_seconds)
    deadline = time.monotonic() + config.wifi_wait_seconds
    while time.monotonic() < deadline:
        time.sleep(1)
        ssid = current_wifi_ssid()
        if ssid is not None:
            return ssid
    return None


def attempt_login(
    config: AppConfig,
    credentials: Credentials,
    *,
    show_browser: bool = False,
) -> bool:
    ssid = _wait_for_ssid(config)
    if ssid is None and config.auto_connect_wifi:
        LOGGER.info("当前未连接 Wi-Fi，尝试连接配置的校园 Wi-Fi")
        ssid = connect_preferred_wifi(config)
    if ssid is None:
        LOGGER.info("尚未连接 Wi-Fi")
        return False

    if not ssid_is_allowed(config, ssid):
        if not config.auto_connect_wifi:
            LOGGER.info("当前 Wi-Fi %r 不在允许列表中，保持当前连接", ssid)
            return False
        LOGGER.info("当前 Wi-Fi %r 不是目标网络，尝试连接配置的校园 Wi-Fi", ssid)
        ssid = connect_preferred_wifi(config)
        if not ssid_is_allowed(config, ssid):
            LOGGER.info("目标校园 Wi-Fi 尚未连接，当前为 %r", ssid)
            return False

    if has_internet(config):
        LOGGER.info("网络已连通，无需认证")
        return True

    LOGGER.info("检测到校园 Wi-Fi %r，开始网页认证", ssid)
    try:
        result = login_to_portal(config, credentials, show_browser=show_browser)
        return result.success
    except PortalLoginError as error:
        LOGGER.error("校园网认证失败: %s", error)
        return False
    except Exception:
        LOGGER.exception("校园网认证发生未预期错误")
        return False


def run_forever() -> None:
    delay = 5
    with single_instance_mutex():
        LOGGER.info("校园网自动登录服务已启动")
        while True:
            try:
                config = load_config()
                credentials = read_credentials(config.credential_target)
                if credentials is None:
                    LOGGER.error("未找到保存的校园网账号密码")
                    time.sleep(config.check_interval_seconds)
                    continue

                online = attempt_login(config, credentials)
                if online:
                    delay = config.retry_initial_seconds
                    time.sleep(config.check_interval_seconds)
                else:
                    time.sleep(delay)
                    delay = min(delay * 2, config.retry_max_seconds)
            except Exception:
                LOGGER.exception("自动登录循环发生未预期错误")
                time.sleep(delay)
                delay = min(delay * 2, 300)
