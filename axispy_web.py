"""Read-only dashboard, independent VAPIX status probes, and bounded live logs."""
from collections import deque
from datetime import datetime, timezone
import errno
from http.client import RemoteDisconnected
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
import re
import socket
import threading
import time
from urllib.parse import parse_qs, quote, quote_plus, urlsplit

import requests
from requests.auth import HTTPDigestAuth

from axispy_ui import HTML


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def request_failure_reason(exc):
    """Expose structured network causes without formatting credential-bearing URLs."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status:
        return f"HTTP {status}"
    if isinstance(exc, requests.ConnectTimeout):
        return "ConnectTimeout"
    if not isinstance(exc, requests.ConnectionError) or isinstance(exc, requests.exceptions.SSLError):
        return type(exc).__name__
    pending, seen = [exc], set()
    labels = {errno.ECONNREFUSED: "connection refused", errno.ECONNRESET: "connection reset",
              errno.ENETUNREACH: "network unreachable", errno.EHOSTUNREACH: "host unreachable",
              errno.ETIMEDOUT: "connection timed out", errno.EPIPE: "broken pipe"}
    while pending:
        cause = pending.pop()
        if not isinstance(cause, BaseException) or id(cause) in seen:
            continue
        seen.add(id(cause))
        if isinstance(cause, socket.gaierror):
            return "ConnectionError: DNS resolution failed"
        if isinstance(cause, RemoteDisconnected):
            return "ConnectionError: connection closed before HTTP response"
        if isinstance(cause, OSError) and cause.errno in labels:
            return f"ConnectionError: {labels[cause.errno]} (errno {cause.errno})"
        pending.extend([getattr(cause, "reason", None), cause.__cause__, cause.__context__, *cause.args])
    return "ConnectionError: underlying socket cause unavailable"


class LiveLogs(logging.Handler):
    def __init__(self, passwords, capacity=1000):
        super().__init__()
        self.records = deque(maxlen=capacity)
        self.sequence = 0
        self.guard = threading.Lock()
        self.passwords = {variant for password in passwords if password
                          for variant in (password, quote(password, safe=""), quote_plus(password))}

    def emit(self, record):
        message = record.getMessage()
        for password in sorted(self.passwords, key=len, reverse=True):
            message = message.replace(password, "[redacted]")
        with self.guard:
            self.sequence += 1
            self.records.append(dict(id=self.sequence, time=datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="seconds"), level=record.levelname, message=message))

    def since(self, after):
        with self.guard:
            if after > self.sequence:
                after = 0  # Server was restarted; don't wait for the old cursor.
            return dict(cursor=self.sequence, entries=[dict(item) for item in self.records if item["id"] > after])


class Dashboard:
    def __init__(self, configs, controller, host="127.0.0.1", port=8080, timeout=.5, interval=5, clock=None):
        raw_poll_interval = os.environ.get("AXIS_WEB_POLL_INTERVAL_MS", "").strip()
        try:
            self.poll_interval_ms = int(raw_poll_interval or "1000")
        except ValueError:
            raise ValueError("AXIS_WEB_POLL_INTERVAL_MS must be an integer from 100 to 60000") from None
        if not 100 <= self.poll_interval_ms <= 60000:
            raise ValueError("AXIS_WEB_POLL_INTERVAL_MS must be an integer from 100 to 60000")
        raw_preview_fps = os.environ.get("AXIS_WEB_PREVIEW_FPS", "").strip()
        try:
            self.preview_fps = int(raw_preview_fps or "25")
        except ValueError:
            raise ValueError("AXIS_WEB_PREVIEW_FPS must be an integer from 1 to 60") from None
        if not 1 <= self.preview_fps <= 60:
            raise ValueError("AXIS_WEB_PREVIEW_FPS must be an integer from 1 to 60")
        self.configs = configs
        self.controller = controller
        self.host, self.port = host, port
        self.timeout = (min(timeout, 2), min(timeout, 2))
        self.interval = interval
        self.clock = clock or time.monotonic
        self.last_success = [None] * len(configs)
        self.offline_threshold = 3
        self.offline_grace = 15
        self.guard = threading.Lock()
        self.finished = threading.Event()
        self.joystick = None
        self.command_revision = [0] * len(configs)
        self.probe_errors = [None] * len(configs)
        self.probe_log_at = [0.] * len(configs)
        self.states = [dict(id=i, slot=c.get("slot", i + 1), name=c["name"], url=c["url"], channel=c.get("channel", 1), online=None,
                            checked_at=None, last_seen=None, consecutive_failures=0, status_note=None, control_error=None, error=None, focus="unknown", focus_source=None, focus_updated_at=None,
                            ptz_activity="unknown")
                       for i, c in enumerate(configs)]
        self.logger = logging.getLogger("axispy")
        self.logs = LiveLogs([os.environ.get(c["password_env"], "") for c in configs])
        self.server = None
        self.threads = []

    def set_joystick(self, name):
        with self.guard:
            self.joystick = name

    def _reachable(self, index):
        state = self.states[index]
        self.last_success[index] = self.clock()
        state.update(online=True, checked_at=timestamp(), last_seen=timestamp(),
                     consecutive_failures=0, status_note=None)

    def command_result(self, index, online, focus=None, ptz_active=None):
        with self.guard:
            state = self.states[index]
            if not online:
                # A rejected/slow control command is not proof of a network outage.
                state["control_error"] = "Control request failed; see Debug logs"
                state["ptz_activity"] = "unknown"
                return
            self.command_revision[index] += 1
            self._reachable(index)
            state.update(error=None, control_error=None)
            if ptz_active is not None:
                state["ptz_activity"] = "active" if ptz_active else "idle"
            if focus is not None:
                state.update(focus=focus, focus_source="command", focus_updated_at=timestamp())

    def snapshot(self):
        with self.guard:
            return dict(version="0.2.5", time=timestamp(), selected=self.controller.selected,
                        poll_interval_ms=self.poll_interval_ms, preview_fps=self.preview_fps,
                        armed=bool(self.controller.armed and self.joystick is not None), joystick=self.joystick,
                        cameras=[dict(state, selected=state["id"] == self.controller.selected) for state in self.states])

    def _probe_diagnostic(self, index, reason, started):
        now = self.clock()
        elapsed = max(0., now - started)
        with self.guard:
            previous = self.probe_errors[index]
            report_failure = reason is not None and (reason != previous or now - self.probe_log_at[index] >= 60)
            report_recovery = reason is None and previous is not None
            self.probe_errors[index] = reason
            if report_failure:
                self.probe_log_at[index] = now
        # Report even if a newer command superseded this check's status update.
        # Never log raw request exception strings or camera response bodies.
        if report_failure:
            self.logger.warning("%s health check failed: %s after %.2fs (connect/read timeouts %.2f/%.2fs)",
                                self.configs[index]["name"], reason, elapsed, *self.timeout)
        elif report_recovery:
            self.logger.info("%s health check recovered after %.2fs", self.configs[index]["name"], elapsed)

    def probe(self, index, session):
        config = self.configs[index]
        started = self.clock()
        with self.guard:
            revision = self.command_revision[index]
        response = None
        try:
            response = session.get(config["url"].rstrip("/") + "/axis-cgi/com/ptz.cgi",
                                   params={"camera": config.get("channel", 1), "query": "position"},
                                   headers={"Connection": "close"},
                                   timeout=self.timeout, allow_redirects=False)
            response.raise_for_status()
            body = response.text.strip()
            values = dict(line.split("=", 1) for line in body.splitlines() if "=" in line)
            if response.status_code != 200 or body.lower().startswith(("error", "<")) or not any(key in values for key in ("pan", "tilt", "zoom", "focus", "autofocus")):
                raise ValueError("Camera did not return VAPIX position status")
            mode = {"on": "auto", "off": "manual"}.get(values.get("autofocus", "").strip().lower())
            self._probe_diagnostic(index, None, started)
            with self.guard:
                if revision != self.command_revision[index]:
                    return  # A newer control response supersedes this probe.
                state = self.states[index]
                self._reachable(index)
                state.update(error=None)
                if mode:
                    state.update(focus=mode, focus_source="camera", focus_updated_at=timestamp())
        except (requests.RequestException, ValueError) as exc:
            reason = str(exc) if isinstance(exc, ValueError) else request_failure_reason(exc)
            self._probe_diagnostic(index, reason, started)
            with self.guard:
                if revision != self.command_revision[index]:
                    return
                state = self.states[index]
                if response is not None:
                    # Even a 401/503 or an unsupported status query proves that
                    # the camera responded. Show the API problem separately.
                    self._reachable(index)
                    state.update(error=reason)
                else:
                    failures = state["consecutive_failures"] + 1
                    last_success = self.last_success[index]
                    recently_seen = last_success is not None and self.clock() - last_success < self.offline_grace
                    offline = failures >= self.offline_threshold and not recently_seen
                    if offline:
                        state["online"] = False
                    state.update(checked_at=timestamp(), error=reason, consecutive_failures=failures,
                                 status_note=f"No camera response confirmed ({reason})" if offline else
                                 f"Health check delayed ({reason}; {failures} consecutive {'failure' if failures == 1 else 'failures'}); confirming connectivity")

    def _monitor(self, index):
        config = self.configs[index]
        # Retain Digest challenge state, but never retain an idle TCP pool
        # across health checks. Some camera firmware closes idle keep-alives.
        auth = HTTPDigestAuth(config["username"], os.environ[config["password_env"]])
        while not self.finished.is_set():
            with requests.Session() as session:
                session.auth = auth
                session.trust_env = False
                session.verify = config.get("ca_bundle", True)
                self.probe(index, session)
            self.finished.wait(self.interval)

    def stream(self, index, handler):
        """Relay MJPEG without sharing the control or health-check sessions."""
        config = self.configs[index]
        started = False
        try:
            with requests.Session() as session:
                session.trust_env = False
                session.verify = config.get("ca_bundle", True)
                session.auth = HTTPDigestAuth(config["username"], os.environ[config["password_env"]])
                with session.get(config["url"].rstrip("/") + "/axis-cgi/mjpg/video.cgi",
                                 params={"camera": config.get("channel", 1), "resolution": "640x480",
                                         "fps": self.preview_fps, "compression": 50},
                                 stream=True, timeout=(2, 5), allow_redirects=False) as response:
                    kind = response.headers.get("Content-Type", "")
                    # Only relay MJPEG. Do not forward camera errors, redirects,
                    # credentials, or arbitrary upstream headers to the browser.
                    if response.status_code != 200 or not re.fullmatch(
                            r'multipart/x-mixed-replace[ \t]*;[ \t]*boundary=(?:[A-Za-z0-9_./=-]{1,100}|"[A-Za-z0-9_./= -]{1,100}")',
                            kind, re.IGNORECASE):
                        handler.send_error(502, "Camera MJPEG preview unavailable")
                        return
                    handler.send_response(200)
                    handler.send_header("Content-Type", kind)
                    handler.send_header("Cache-Control", "no-store")
                    handler.send_header("X-Content-Type-Options", "nosniff")
                    handler.send_header("Connection", "close")
                    handler.end_headers()
                    started = True
                    for chunk in response.iter_content(chunk_size=1024):
                        if self.finished.is_set():
                            break
                        if chunk:
                            handler.wfile.write(chunk)
                            handler.wfile.flush()
        except requests.RequestException:
            if not started:
                handler.send_error(502, "Camera MJPEG preview unavailable")
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass  # Turning previews off or closing a tab disconnects the viewer.
        finally:
            handler.close_connection = True

    def start(self):
        dashboard = self
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urlsplit(self.path)
                if parsed.path == "/":
                    payload, kind = HTML.encode(), "text/html; charset=utf-8"
                elif parsed.path.startswith("/api/stream/"):
                    camera_id = parsed.path.removeprefix("/api/stream/")
                    if not camera_id.isascii() or not camera_id.isdecimal() or len(camera_id) > 3:
                        self.send_error(404)
                        return
                    index = int(camera_id)
                    if index >= len(dashboard.configs) or parsed.query:
                        self.send_error(404)
                        return
                    # Bound writes to slow/disconnected viewers as well as camera reads.
                    self.connection.settimeout(5)
                    dashboard.stream(index, self)
                    return
                elif parsed.path == "/api/status":
                    payload, kind = json.dumps(dashboard.snapshot()).encode(), "application/json"
                elif parsed.path == "/api/logs":
                    try:
                        after = int(parse_qs(parsed.query).get("after", ["0"])[0])
                        if after < 0:
                            raise ValueError()
                    except ValueError:
                        self.send_error(400, "Invalid log cursor")
                        return
                    payload, kind = json.dumps(dashboard.logs.since(after)).encode(), "application/json"
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *_):
                pass  # Dashboard polling must not fill the live console log.

        self.server = ThreadingHTTPServer((self.host, self.port), Handler)
        self.port = self.server.server_port
        self.logger.addHandler(self.logs)
        self.threads = [threading.Thread(target=self.server.serve_forever, daemon=True)]
        self.threads += [threading.Thread(target=self._monitor, args=(i,), daemon=True) for i in range(len(self.configs))]
        for thread in self.threads:
            thread.start()
        self.logger.info("Web dashboard started on port %s", self.port)
        return self

    def stop(self):
        self.finished.set()
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        for thread in self.threads:
            thread.join(timeout=6)
        self.logger.removeHandler(self.logs)
        self.logs.close()
