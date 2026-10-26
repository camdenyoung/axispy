"""Read-only dashboard, independent VAPIX status probes, and bounded live logs."""
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
import threading
from urllib.parse import parse_qs, quote, quote_plus, urlsplit

import requests
from requests.auth import HTTPDigestAuth

from axispy_ui import HTML


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
    def __init__(self, configs, controller, host="127.0.0.1", port=8080, timeout=.5, interval=5):
        self.configs = configs
        self.controller = controller
        self.host, self.port = host, port
        self.timeout = (min(timeout, .5), min(timeout, 2))
        self.interval = interval
        self.guard = threading.Lock()
        self.finished = threading.Event()
        self.joystick = None
        self.command_revision = [0] * len(configs)
        self.states = [dict(id=i, name=c["name"], url=c["url"], channel=c.get("channel", 1), online=None,
                            checked_at=None, error=None, focus="unknown", focus_source=None, focus_updated_at=None)
                       for i, c in enumerate(configs)]
        self.logger = logging.getLogger("axispy")
        self.logs = LiveLogs([os.environ.get(c["password_env"], "") for c in configs])
        self.server = None
        self.threads = []

    def set_joystick(self, name):
        with self.guard:
            self.joystick = name

    def command_result(self, index, online, focus=None):
        with self.guard:
            self.command_revision[index] += 1
            state = self.states[index]
            state.update(online=online, checked_at=timestamp(), error=None if online else "Control request failed")
            if focus is not None:
                state.update(focus=focus, focus_source="command", focus_updated_at=timestamp())

    def snapshot(self):
        with self.guard:
            return dict(version="0.2.0", time=timestamp(), selected=self.controller.selected,
                        armed=bool(self.controller.armed and self.joystick is not None), joystick=self.joystick,
                        cameras=[dict(state, selected=state["id"] == self.controller.selected) for state in self.states])

    def probe(self, index, session):
        config = self.configs[index]
        with self.guard:
            revision = self.command_revision[index]
        try:
            response = session.get(config["url"].rstrip("/") + "/axis-cgi/com/ptz.cgi",
                                   params={"camera": config.get("channel", 1), "query": "position"},
                                   timeout=self.timeout, allow_redirects=False)
            response.raise_for_status()
            body = response.text.strip()
            values = dict(line.split("=", 1) for line in body.splitlines() if "=" in line)
            if response.status_code != 200 or body.lower().startswith(("error", "<")) or not any(key in values for key in ("pan", "tilt", "zoom", "focus", "autofocus")):
                raise ValueError("Camera did not return VAPIX position status")
            mode = {"on": "auto", "off": "manual"}.get(values.get("autofocus", "").strip().lower())
            with self.guard:
                if revision != self.command_revision[index]:
                    return  # A newer control response supersedes this probe.
                state = self.states[index]
                state.update(online=True, checked_at=timestamp(), error=None)
                if mode:
                    state.update(focus=mode, focus_source="camera", focus_updated_at=timestamp())
        except (requests.RequestException, ValueError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            reason = f"HTTP {status}" if status else (str(exc) if isinstance(exc, ValueError) else type(exc).__name__)
            with self.guard:
                if revision == self.command_revision[index]:
                    self.states[index].update(online=False, checked_at=timestamp(), error=reason)

    def _monitor(self, index):
        config = self.configs[index]
        with requests.Session() as session:
            session.auth = HTTPDigestAuth(config["username"], os.environ[config["password_env"]])
            session.trust_env = False
            session.verify = config.get("ca_bundle", True)
            while not self.finished.is_set():
                self.probe(index, session)
                self.finished.wait(self.interval)

    def start(self):
        dashboard = self
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urlsplit(self.path)
                if parsed.path == "/":
                    payload, kind = HTML.encode(), "text/html; charset=utf-8"
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
