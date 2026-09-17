from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from campusnet_autologin.config import (
    AppConfig,
    load_config,
    normalize_login_url,
    save_config,
)
from campusnet_autologin.credentials import Credentials
from campusnet_autologin.network import extract_available_ssids, extract_ssid, ssid_is_allowed
from campusnet_autologin.portal import PortalLoginError, sanitize_url
from campusnet_autologin.service import (
    AttemptResult,
    RetryPolicy,
    ServiceState,
    ServiceStateMachine,
    attempt_login,
)
from campusnet_autologin.startup import install_startup, read_startup_command


class ConfigTests(unittest.TestCase):
    def test_config_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            expected = AppConfig(login_url="http://portal.example/login", allowed_ssids=["Campus"])
            save_config(expected, path)
            self.assertEqual(load_config(path), expected)

    def test_invalid_interval_is_rejected(self):
        config = AppConfig(check_interval_seconds=1)
        with self.assertRaises(ValueError):
            config.validate()

    def test_login_ip_gets_http_scheme(self):
        self.assertEqual(normalize_login_url("10.10.10.52"), "http://10.10.10.52")

    def test_invalid_login_scheme_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize_login_url("file:///passwords.txt")

    def test_invalid_portal_adapter_is_rejected(self):
        config = AppConfig(portal_adapter="unknown")
        with self.assertRaises(ValueError):
            config.validate()


class NetworkTests(unittest.TestCase):
    def test_extract_ssid_ignores_bssid(self):
        output = "Name : Wi-Fi\nState : connected\nSSID : Campus-WiFi\nBSSID : aa:bb:cc"
        self.assertEqual(extract_ssid(output), "Campus-WiFi")

    def test_ssid_allow_list(self):
        config = AppConfig(allowed_ssids=["Campus-WiFi"])
        self.assertTrue(ssid_is_allowed(config, "Campus-WiFi"))
        self.assertFalse(ssid_is_allowed(config, "Home"))

    def test_extract_available_ssids(self):
        output = "SSID 1 : CCIT-WLAN\n    BSSID 1 : aa:bb\nSSID 2 : test"
        self.assertEqual(extract_available_ssids(output), ["CCIT-WLAN", "test"])


class PortalTests(unittest.TestCase):
    def test_sanitize_url_removes_secrets(self):
        self.assertEqual(
            sanitize_url("https://portal.example/login?token=secret#part"),
            "https://portal.example/login",
        )

    def test_eportal_tip_fields_are_supported(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading

        import campusnet_autologin.portal as portal

        submitted = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/mark":
                    submitted.set()
                    body = b"ok"
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                html = '''<!doctype html><title>ePortal Mock</title>
                <input id="username_tip" name="username_tip" value="账号" readonly
                    onclick="document.querySelector('#username').style.display='block'">
                <input id="username" name="username" style="display:none">
                <input id="pwd_tip" name="pwd_tip" value="密码" readonly
                    onclick="document.querySelector('#pwd').style.display='block'">
                <input id="pwd" name="pwd" type="password" style="display:none">
                <a id="loginLink" onclick="fetch('/mark')">login</a>'''.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                self.wfile.write(html)

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        original_check = portal.has_internet
        portal.has_internet = lambda _config: submitted.is_set()
        try:
            config = AppConfig(
                login_url=f"http://127.0.0.1:{server.server_port}/",
                login_verify_seconds=5,
            )
            result = portal.login_to_portal(config, Credentials("student", "secret"))
            self.assertTrue(result.success)
            self.assertEqual(result.adapter_name, "eportal")
        finally:
            portal.has_internet = original_check
            server.shutdown()
            server.server_close()


class ServiceTests(unittest.TestCase):
    @patch("campusnet_autologin.service.login_to_portal")
    @patch("campusnet_autologin.service.connect_preferred_wifi", return_value="Campus")
    @patch("campusnet_autologin.service.current_wifi_ssid", return_value=None)
    @patch("campusnet_autologin.service.has_internet", return_value=True)
    def test_connects_preferred_wifi_then_rechecks_internet(
        self,
        _has_internet,
        _current_wifi_ssid,
        connect_preferred_wifi,
        login_to_portal,
    ):
        config = AppConfig(
            allowed_ssids=["Campus"],
            auto_connect_wifi=True,
            wifi_wait_seconds=2,
        )
        result = attempt_login(config, Credentials("student", "secret"))
        self.assertTrue(result)
        self.assertEqual(result.state, ServiceState.ONLINE)
        connect_preferred_wifi.assert_called_once_with(config)
        login_to_portal.assert_not_called()

    def test_state_machine_rejects_invalid_transition(self):
        machine = ServiceStateMachine()
        with self.assertRaises(RuntimeError):
            machine.transition(ServiceState.ONLINE)

    def test_retry_policy_backs_off_and_resets(self):
        policy = RetryPolicy(
            initial_seconds=5,
            max_seconds=60,
            multiplier=2,
            jitter_ratio=0,
        )
        no_wifi = AttemptResult(ServiceState.NO_WIFI, False, "NO_WIFI")
        wrong_wifi = AttemptResult(
            ServiceState.WRONG_WIFI,
            False,
            "WRONG_WIFI",
            ssid="Home",
        )
        online = AttemptResult(ServiceState.ONLINE, True, "ONLINE", ssid="Campus")

        self.assertEqual(policy.delay_for(no_wifi, online_interval=30), 5)
        self.assertEqual(policy.delay_for(no_wifi, online_interval=30), 10)
        self.assertEqual(policy.delay_for(wrong_wifi, online_interval=30), 5)
        self.assertEqual(policy.delay_for(online, online_interval=30), 30)
        self.assertEqual(policy.delay_for(no_wifi, online_interval=30), 5)

    def test_portal_failure_has_explicit_state(self):
        config = AppConfig(allowed_ssids=["Campus"], wifi_wait_seconds=0)
        with (
            patch("campusnet_autologin.service.current_wifi_ssid", return_value="Campus"),
            patch("campusnet_autologin.service.has_internet", return_value=False),
            patch(
                "campusnet_autologin.service.login_to_portal",
                side_effect=PortalLoginError("PASSWORD_FIELD_NOT_FOUND"),
            ),
        ):
            result = attempt_login(config, Credentials("student", "secret"))

        self.assertFalse(result)
        self.assertEqual(result.state, ServiceState.AUTHENTICATION_FAILED)
        self.assertEqual(result.code, "PASSWORD_FIELD_NOT_FOUND")


class StartupTests(unittest.TestCase):
    @patch("campusnet_autologin.startup._delete_legacy_run_value")
    @patch("campusnet_autologin.startup._run_schtasks")
    def test_install_uses_logon_scheduled_task(self, run_schtasks, delete_legacy):
        run_schtasks.return_value = subprocess.CompletedProcess([], 0, "SUCCESS", "")

        command = install_startup(Path("C:/CampusNetAutoLogin/campusnet.py"))

        arguments = run_schtasks.call_args.args[0]
        self.assertIn("/Create", arguments)
        self.assertIn("ONLOGON", arguments)
        self.assertIn("0000:10", arguments)
        self.assertIn(command, arguments)
        delete_legacy.assert_called_once_with()

    @patch("campusnet_autologin.startup._run_schtasks")
    def test_read_startup_command_parses_task_xml(self, run_schtasks):
        task_xml = '''<?xml version="1.0" encoding="UTF-8"?>
        <Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
          <Actions>
            <Exec>
              <Command>C:\\Python\\pythonw.exe</Command>
              <Arguments>C:\\App\\campusnet.py run</Arguments>
            </Exec>
          </Actions>
        </Task>'''
        run_schtasks.return_value = subprocess.CompletedProcess([], 0, task_xml, "")

        command = read_startup_command()

        self.assertEqual(command, "C:\\Python\\pythonw.exe C:\\App\\campusnet.py run")


if __name__ == "__main__":
    unittest.main()
