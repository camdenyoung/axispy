"""Read an SDL USB joystick and control four Axis cameras through VAPIX."""
from __future__ import annotations

import argparse
import logging
from logging.handlers import RotatingFileHandler
import math
import os
import signal
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import requests
from dotenv import load_dotenv
from requests.auth import HTTPDigestAuth

LOG = logging.getLogger("axispy")
STOP = (0, 0, 0, 0)


def speed(value: float, deadzone: float, maximum: int, inverted: bool) -> int:
    if not math.isfinite(value) or abs(value) <= deadzone:
        return 0
    magnitude = min(1.0, (abs(value) - deadzone) / (1 - deadzone))
    direction = -1 if value < 0 else 1
    return round(magnitude * maximum) * direction * (-1 if inverted else 1)


@dataclass
class Settings:
    joystick: dict
    cameras: list[dict]
    rate: float
    timeout: float
    continuous_timeout: int
    focus_timeout: float = 2.0
    autofocus_hold_seconds: float = 5.0

    @classmethod
    def load(cls, path: str | None = None) -> Settings:
        data = {
            "joystick": dict(device=0, pan_axis=0, tilt_axis=1, zoom_axis=2,
                             invert_pan=False, invert_tilt=True, invert_zoom=False,
                             deadzone=.18, max_pan=70, max_tilt=70, max_zoom=50,
                             camera_buttons=[0, 1, 2, 3], deadman_button=-1,
                             focus_near_button=4, focus_far_button=5, focus_speed=30),
            "cameras": [dict(name=f"J{i}", url="", username="operator",
                             password_env=f"AXIS_J{i}_PASSWORD", channel=1)
                        for i in range(1, 5)],
            "control": {},
        }
        if path:
            with open(path, "rb") as stream:
                file_data = tomllib.load(stream)
            for section in ("joystick", "control"):
                data[section].update(file_data.get(section, {}))
            data["cameras"] = file_data.get("cameras", data["cameras"])
        for key, default in data["joystick"].items():
            raw = os.environ.get(f"AXIS_{key.upper()}")
            if raw is None:
                continue
            if isinstance(default, bool):
                if raw.lower() not in ("true", "false", "1", "0"):
                    raise ValueError(f"AXIS_{key.upper()} must be true or false")
                value = raw.lower() in ("true", "1")
            elif isinstance(default, list):
                value = [int(item.strip()) for item in raw.split(",")]
            else:
                value = type(default)(raw)
            data["joystick"][key] = value
        for key, cast in (("rate_hz", float), ("timeout", float), ("continuous_timeout", int), ("focus_timeout", float), ("autofocus_hold_seconds", float)):
            if f"AXIS_{key.upper()}" in os.environ:
                raw = os.environ[f"AXIS_{key.upper()}"]
                if key == "autofocus_hold_seconds" and not raw.strip():
                    continue
                data["control"][key] = cast(raw)
        for i, camera in enumerate(data["cameras"], 1):
            for key in ("url", "username", "channel", "ca_bundle"):
                raw = os.environ.get(f"AXIS_J{i}_{key.upper()}")
                if raw is not None:
                    camera[key] = int(raw) if key == "channel" else raw
        j = data["joystick"]
        cameras = data["cameras"]
        if len(cameras) != 4:
            raise ValueError("Configure exactly four cameras in J1-J4 order")
        buttons = j["camera_buttons"]
        if len(buttons) != 4 or len(set(buttons)) != 4:
            raise ValueError("camera_buttons must contain four distinct indices")
        for value in [j.get("device", 0), *buttons, *(j[f"{a}_axis"] for a in ("pan", "tilt", "zoom"))]:
            if type(value) is not int or value < 0:
                raise ValueError("Device, axis and camera button indices must be nonnegative integers")
        deadman = j.get("deadman_button", -1)
        if type(deadman) is not int or deadman < -1:
            raise ValueError("deadman_button must be -1 or a nonnegative integer")
        focus_buttons = [j["focus_near_button"], j["focus_far_button"]]
        if any(type(button) is not int or button < -1 for button in focus_buttons):
            raise ValueError("Focus buttons must be -1 (disabled) or nonnegative integers")
        assigned = buttons + [button for button in [deadman, *focus_buttons] if button >= 0]
        if len(assigned) != len(set(assigned)):
            raise ValueError("Camera, focus and deadman buttons must have distinct indices; use -1 to disable a focus button")
        if type(j["focus_speed"]) is not int or not 1 <= j["focus_speed"] <= 100:
            raise ValueError("focus_speed must be an integer from 1 to 100")
        if not 0 <= j.get("deadzone", .18) < 1:
            raise ValueError("deadzone must be between 0 (inclusive) and 1 (exclusive)")
        for axis in ("pan", "tilt", "zoom"):
            if type(j.get(f"max_{axis}", 70)) is not int or not 0 <= j.get(f"max_{axis}", 70) <= 100:
                raise ValueError("Maximum speeds must be integers from 0 to 100")
        c = data.get("control", {})
        rate, timeout = float(c.get("rate_hz", 10)), float(c.get("timeout", .5))
        watchdog = c.get("continuous_timeout", 2)
        focus_timeout = float(c.get("focus_timeout", 2))
        autofocus_hold_seconds = float(c.get("autofocus_hold_seconds", 5))
        if not math.isfinite(autofocus_hold_seconds) or autofocus_hold_seconds <= 0:
            raise ValueError("autofocus_hold_seconds must be a finite number greater than zero")
        if not .5 <= rate <= 50 or not .1 <= timeout <= 10:
            raise ValueError("rate_hz must be 0.5-50; timeout must be 0.1-10 seconds")
        if not .1 <= focus_timeout <= 10:
            raise ValueError("focus_timeout must be 0.1-10 seconds")
        if type(watchdog) is not int or not 1 <= watchdog <= 300:
            raise ValueError("continuous_timeout must be an integer from 1 to 300 seconds")
        for camera in cameras:
            url = urlsplit(camera["url"])
            if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ("", "/"):
                raise ValueError("Camera URL must be an http(s) origin without credentials")
            if not camera.get("username") or not camera.get("password_env"):
                raise ValueError("Each camera needs username and password_env")
            if type(camera.get("channel", 1)) is not int or camera.get("channel", 1) < 1:
                raise ValueError("Camera channel must be a positive integer")
        return cls(j, cameras, rate, timeout, watchdog, focus_timeout, autofocus_hold_seconds)


class CameraCommandError(RuntimeError):
    """A camera response suitable for reporting without request credentials."""


class Camera:
    def __init__(self, config: dict, timeout: float, watchdog: int, focus_timeout: float = 2):
        password = os.environ.get(config["password_env"])
        if not password:
            raise ValueError(f"Set {config['password_env']} before starting")
        self.name = config["name"]
        self.url = config["url"].rstrip("/") + "/axis-cgi/com/ptz.cgi"
        self.channel = config.get("channel", 1)
        self.timeout = timeout
        self.watchdog = watchdog
        self.focus_active = False
        self.dashboard = None
        self.dashboard_index = None
        self.focus_timeout = max(timeout, focus_timeout)
        self.last_ptz = None
        self.last_focus = None
        self.ptz_sent_at = self.focus_sent_at = 0.
        self.refresh_interval = min(.5, watchdog / 2)
        self.clock = time.monotonic
        self.session = requests.Session()
        self.session.auth = HTTPDigestAuth(config["username"], password)
        # Do not route local camera credentials through ambient HTTP proxies.
        self.session.trust_env = False
        self.session.verify = config.get("ca_bundle", True)
        if not isinstance(self.session.verify, (str, bool)) or self.session.verify is False:
            raise ValueError("Use a trusted ca_bundle; TLS verification cannot be disabled")

    def move(self, motion: tuple[int, int, int, int]):
        pan, tilt, zoom, focus = motion
        now = self.clock()
        ptz = (pan, tilt, zoom)
        ptz_due = (ptz != self.last_ptz or motion == STOP
                   or (any(ptz) and now - self.ptz_sent_at >= self.refresh_interval))
        focus_due = ((focus != 0 and (not self.focus_active or focus != self.last_focus
                                     or now - self.focus_sent_at >= self.refresh_interval))
                     or (focus == 0 and self.focus_active))
        if not ptz_due and not focus_due:
            return
        params = {
            "camera": self.channel,
            "continuouspantiltmove": f"{pan},{tilt}",
            "continuouszoommove": str(zoom),
            "continuoustimeout": self.watchdog,
        }
        if focus_due:
            params["continuousfocusmove"] = str(focus)
            if focus and not self.focus_active:
                params["autofocus"] = "off"
            # Set before the request: even a lost response needs a focus stop.
            if focus:
                self.focus_active = True
        try:
            self._request(params, read_timeout=self.focus_timeout if focus_due else None)
        except (requests.RequestException, RuntimeError):
            self.last_ptz = self.last_focus = None
            raise
        self.last_ptz = ptz
        self.ptz_sent_at = self.clock()
        if focus_due:
            self.focus_active = focus != 0
            self.last_focus = focus
            self.focus_sent_at = self.clock()
        if self.dashboard:
            self.dashboard.command_result(self.dashboard_index, True, "manual" if focus else None)

    def enable_autofocus(self):
        if self.focus_active:
            self._request({"camera": self.channel, "continuousfocusmove": "0"}, read_timeout=self.focus_timeout)
            self.focus_active = False
            self.last_focus = 0
        self._request({"camera": self.channel, "autofocus": "on"}, read_timeout=self.focus_timeout)
        if self.dashboard:
            self.dashboard.command_result(self.dashboard_index, True, "auto")

    def _request(self, params, read_timeout=None):
        response = self.session.get(self.url, params=params,
                                    timeout=(self.timeout, read_timeout or self.timeout), allow_redirects=False)
        response.raise_for_status()
        if 300 <= response.status_code < 400:
            target = urlsplit(response.headers.get("Location", ""))
            if target.scheme == "https" and self.url.startswith("http://"):
                raise CameraCommandError(f"HTTP {response.status_code} redirect to HTTPS; enable HTTP on the camera or configure trusted HTTPS")
            raise CameraCommandError(f"HTTP {response.status_code} redirect; check camera URL and HTTP/HTTPS settings")
        # Axis firmware may acknowledge PTZ commands with 204 No Content.
        if response.status_code == 204:
            return
        if response.status_code != 200:
            raise CameraCommandError(f"Unexpected HTTP {response.status_code} from PTZ endpoint")
        body = response.text.strip()
        if body.lower().startswith("error"):
            # Redact before truncating/normalizing; never report raw request URLs.
            detail = body.replace(self.session.auth.password, "[redacted]")
            detail = " ".join(detail.split())
            detail = "".join(char for char in detail if char.isprintable())[:240]
            raise CameraCommandError(f"PTZ command rejected: {detail}")
        if body.lower().startswith(("<!doctype html", "<html")):
            raise CameraCommandError("PTZ endpoint returned an HTML page; check camera URL, authentication and PTZ support")

    def close(self):
        self.session.close()


class Controller:
    def __init__(self, cameras, retry_interval=5, clock=None):
        self.cameras = cameras
        self.selected = 0
        self.armed = False
        self.retry_interval = retry_interval
        self.clock = clock or time.monotonic
        self.retry_at = [0.0] * len(cameras)
        self.unavailable = set()
        self.failure_reason = {}
        self.stop_required = set(range(len(cameras)))
        # A failed response to a movement request leaves motion uncertain.
        self.may_be_moving = set()

    def _move(self, index, motion, force=False):
        if not force and self.clock() < self.retry_at[index]:
            return False
        if motion != STOP:
            self.may_be_moving.add(index)
        try:
            self.cameras[index].move(motion)
        except requests.ReadTimeout as exc:
            self.armed = False
            self.stop_required.add(index)
            if motion != STOP or index in self.may_be_moving:
                # The camera may have executed a request whose reply was lost.
                # Try one stop immediately, rather than waiting out the backoff.
                try:
                    self.cameras[index].move(STOP)
                except (requests.RequestException, RuntimeError) as stop_exc:
                    return self._failed(index, stop_exc)
                else:
                    self.unavailable.discard(index)
                    self.failure_reason.pop(index, None)
                    self.retry_at[index] = 0
                    self.stop_required.discard(index)
                    self.may_be_moving.discard(index)
                    LOG.warning("%s command response timed out; stop confirmed. Center joystick and release focus buttons before resuming", self.cameras[index].name)
                return False
            return self._failed(index, exc)
        except (requests.RequestException, RuntimeError) as exc:
            return self._failed(index, exc)
        if index in self.unavailable:
            LOG.info("%s reconnected; center joystick and release focus buttons to enable movement", self.cameras[index].name)
        self.unavailable.discard(index)
        self.failure_reason.pop(index, None)
        self.retry_at[index] = 0
        if motion == STOP:
            self.may_be_moving.discard(index)
            self.stop_required.discard(index)
        return True

    def _failed(self, index, exc):
        dashboard = getattr(self.cameras[index], "dashboard", None)
        if dashboard:
            dashboard.command_result(index, False)
        status = getattr(getattr(exc, "response", None), "status_code", None)
        reason = str(exc) if isinstance(exc, CameraCommandError) else (f"HTTP {status}" if status else type(exc).__name__)
        if self.failure_reason.get(index) != reason:
            LOG.warning("%s unavailable (%s); retrying every %ss with stop commands",
                        self.cameras[index].name, reason, self.retry_interval)
        self.failure_reason[index] = reason
        self.unavailable.add(index)
        self.stop_required.add(index)
        self.retry_at[index] = self.clock() + self.retry_interval
        self.armed = False
        return False

    def enable_autofocus(self, index):
        if self.clock() < self.retry_at[index]:
            LOG.warning("Autofocus not enabled for %s: camera unavailable; release and hold its J button again after recovery", self.cameras[index].name)
            return False
        try:
            self.cameras[index].enable_autofocus()
        except (requests.RequestException, RuntimeError) as exc:
            self._failed(index, exc)
            LOG.warning("Autofocus not enabled for %s; release and hold its J button again after recovery", self.cameras[index].name)
            return False
        LOG.info("Autofocus enabled for %s", self.cameras[index].name)
        return True

    def select(self, index):
        if index == self.selected:
            return
        self.armed = False
        needs_stop = self.selected in self.stop_required or self.selected in self.may_be_moving
        stopped = not needs_stop or self._move(self.selected, STOP)
        if not stopped and self.selected in self.may_be_moving:
            LOG.warning("Cannot switch: stop for %s is unconfirmed; retry after reconnection",
                        self.cameras[self.selected].name)
            return
        self.selected = index
        if index in self.stop_required or index in self.may_be_moving:
            self._move(index, STOP)
        LOG.info("Selected %s; center joystick and release focus buttons to enable movement", self.cameras[index].name)

    def send(self, motion, neutral=True):
        if self.selected in self.stop_required:
            self.armed = False
            self._move(self.selected, STOP)
            return
        if not self.armed and (motion != STOP or not neutral):
            # A stop is already confirmed; do not flood the camera while
            # waiting for the operator to release/center the controls.
            return
        if motion == STOP and neutral and not self.armed:
            # The stop handshake is already confirmed. Arming changes local
            # state only; repeating zoom=0 can provoke autofocus on some firmware.
            self.armed = True
            return
        if motion == STOP and self.selected not in self.may_be_moving:
            return  # Already stopped; repeated neutral samples must not touch the lens.
        self._move(self.selected, motion if self.armed else STOP)

    def maintain_stop(self):
        """Retry an unconfirmed stop while waiting for USB, without arming."""
        self.armed = False
        if self.selected in self.stop_required:
            self._move(self.selected, STOP)

    def stop(self):
        self.armed = False
        for attempt in range(3):
            if self._move(self.selected, STOP, force=True):
                return
            LOG.error("Stop attempt %d failed for %s", attempt + 1, self.cameras[self.selected].name)
        LOG.error("Stop unconfirmed: use camera controls if motion continues; waiting to reconnect")


class CameraButtonHolds:
    """One autofocus gesture per continuous camera-button hold."""
    def __init__(self, hold_seconds=5.0):
        self.hold_seconds = hold_seconds
        self.started = {}
        self.fired = set()
        self.blocked = set()

    def reset(self, pressed=()):
        self.started.clear()
        self.fired.clear()
        # A button held across USB attachment must be released before timing it.
        self.blocked = set(pressed)

    def update(self, pressed, now):
        pressed = set(pressed)
        self.blocked.intersection_update(pressed)
        self.fired.intersection_update(pressed)
        self.started = {index: start for index, start in self.started.items() if index in pressed}
        ready = []
        for index in sorted(pressed - self.blocked):
            self.started.setdefault(index, now)
            if index not in self.fired and now - self.started[index] >= self.hold_seconds:
                self.fired.add(index)
                ready.append(index)
        return ready

    def release(self, index):
        self.started.pop(index, None)
        self.fired.discard(index)
        self.blocked.discard(index)


def read_motion(joystick, config):
    if config.get("deadman_button", -1) >= 0 and not joystick.get_button(config["deadman_button"]):
        return STOP
    near = config.get("focus_near_button", 4)
    far = config.get("focus_far_button", 5)
    focus = ((int(bool(joystick.get_button(far))) if far >= 0 else 0)
             - (int(bool(joystick.get_button(near))) if near >= 0 else 0)) * config.get("focus_speed", 30)
    axes = tuple(speed(joystick.get_axis(config[f"{axis}_axis"]),
                       config.get("deadzone", .18), config.get(f"max_{axis}", 70),
                       config.get(f"invert_{axis}", False)) for axis in ("pan", "tilt", "zoom"))
    return (*axes, focus)


def run(args):
    # SDL joystick polling needs no graphical window or display server.
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    import pygame
    pygame.display.init()
    pygame.joystick.init()
    cameras = []
    controller = None
    dashboard = None
    joystick = None
    running = True

    def terminate(*_):
        nonlocal running
        running = False

    previous_handlers = {sig: signal.signal(sig, terminate) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        if args.list:
            count = pygame.joystick.get_count()
            if count == 0:
                print("No SDL joysticks detected on this machine.", flush=True)
            for index in range(count):
                device = pygame.joystick.Joystick(index)
                device.init()
                print(f"Device {index}: {device.get_name()} | axes={device.get_numaxes()} buttons={device.get_numbuttons()} GUID={device.get_guid()}", flush=True)
                device.quit()
            return
        settings = None if args.inspect else Settings.load(args.config)
        index = args.device if args.inspect else settings.joystick.get("device", 0)
        if index < 0:
            raise ValueError("Joystick device index must be nonnegative")
        if settings:
            for config in settings.cameras:
                cameras.append(Camera(config, settings.timeout, settings.continuous_timeout, settings.focus_timeout))
            controller = Controller(cameras)
            if os.environ.get("AXIS_WEB_ENABLED", "false").lower() in ("true", "1"):
                from axispy_web import Dashboard
                port = int(os.environ.get("AXIS_WEB_PORT", "8080"))
                if not 1 <= port <= 65535:
                    raise ValueError("AXIS_WEB_PORT must be between 1 and 65535")
                dashboard = Dashboard(settings.cameras, controller,
                                      host=os.environ.get("AXIS_WEB_HOST", "127.0.0.1"),
                                      port=port, timeout=max(settings.timeout, settings.focus_timeout))
                for i, camera in enumerate(cameras):
                    camera.dashboard = dashboard
                    camera.dashboard_index = i
                dashboard.start()
            LOG.info("Selected %s; waiting for joystick and camera readiness", cameras[0].name)
        holds = CameraButtonHolds(settings.autofocus_hold_seconds if settings else 5.0)
        manual_focus_blocked = False
        last_motion = None
        next_send = next_connect = 0
        waiting_logged = False
        while running:
            for event in pygame.event.get():
                if joystick is None:
                    continue
                if event.type == pygame.JOYDEVICEREMOVED and event.instance_id == joystick.get_instance_id():
                    LOG.warning("Joystick disconnected; stopping camera and waiting for USB")
                    joystick.quit()
                    joystick = None
                    if dashboard:
                        dashboard.set_joystick(None)
                    if controller:
                        controller.stop()
                    waiting_logged = True
                    next_connect = time.monotonic() + 2
                    continue
                if getattr(event, "instance_id", None) != joystick.get_instance_id():
                    continue
                if args.inspect:
                    if event.type == pygame.JOYBUTTONDOWN:
                        print(f"button {event.button} pressed", flush=True)
                    elif event.type == pygame.JOYAXISMOTION:
                        print(f"axis {event.axis}: {event.value:+.3f}", flush=True)
                elif event.type == pygame.JOYBUTTONDOWN:
                    if event.button in settings.joystick["camera_buttons"]:
                        previous_selection = controller.selected
                        controller.select(settings.joystick["camera_buttons"].index(event.button))
                        if controller.selected != previous_selection:
                            last_motion = None
                elif event.type == pygame.JOYBUTTONUP:
                    if event.button in settings.joystick["camera_buttons"]:
                        holds.release(settings.joystick["camera_buttons"].index(event.button))
            if not running:
                break
            now = time.monotonic()
            if joystick is None and now >= next_connect:
                next_connect = now + 2
                candidate = None
                try:
                    if index < pygame.joystick.get_count():
                        candidate = pygame.joystick.Joystick(index)
                        candidate.init()
                        if settings:
                            j = settings.joystick
                            if max(j[f"{a}_axis"] for a in ("pan", "tilt", "zoom")) >= candidate.get_numaxes():
                                raise ValueError("Axis index out of range; run --inspect")
                            if max(*j["camera_buttons"], j.get("deadman_button", -1), j["focus_near_button"], j["focus_far_button"]) >= candidate.get_numbuttons():
                                raise ValueError("Button index out of range; run --inspect")
                        joystick = candidate
                        LOG.info("Joystick connected: %s; center joystick and release focus buttons to enable movement", joystick.get_name())
                        if dashboard:
                            dashboard.set_joystick(joystick.get_name())
                        if controller:
                            holds.reset(i for i, button in enumerate(settings.joystick["camera_buttons"]) if joystick.get_button(button))
                            manual_focus_blocked = False
                            controller.armed = False
                            controller.stop_required.add(controller.selected)
                        waiting_logged = False
                        last_motion = None
                        next_send = 0
                except pygame.error:
                    if candidate is not None:
                        candidate.quit()
                if joystick is None and not waiting_logged:
                    LOG.warning("Joystick %d unavailable; waiting for USB (checking every 2s)", index)
                    waiting_logged = True
            if controller:
                if joystick is None:
                    controller.maintain_stop()
                else:
                    try:
                        motion = read_motion(joystick, settings.joystick)
                        focus_released = not any(
                            joystick.get_button(button) for button in (
                                settings.joystick["focus_near_button"],
                                settings.joystick["focus_far_button"],
                            ) if button >= 0)
                        pressed_cameras = [i for i, button in enumerate(settings.joystick["camera_buttons"]) if joystick.get_button(button)]
                    except pygame.error:
                        LOG.warning("Joystick read failed; stopping camera and waiting for USB")
                        joystick.quit()
                        joystick = None
                        if dashboard:
                            dashboard.set_joystick(None)
                        controller.stop()
                        next_connect = time.monotonic() + 2
                        waiting_logged = True
                        continue
                    for camera_index in holds.update(pressed_cameras, time.monotonic()):
                        if controller.enable_autofocus(camera_index) and camera_index == controller.selected:
                            manual_focus_blocked = not focus_released
                    if manual_focus_blocked:
                        motion = (*motion[:3], 0)
                        if focus_released:
                            manual_focus_blocked = False
                    refresh_needed = (motion != STOP or controller.selected in controller.stop_required
                                      or not controller.armed)
                    if ((now >= next_send and refresh_needed)
                            or (motion == STOP and motion != last_motion)
                            or (last_motion is not None and motion[3] != last_motion[3])):
                        controller.send(motion, neutral=focus_released)
                        last_motion = motion
                        next_send = time.monotonic() + 1 / settings.rate
            time.sleep(.005 if joystick is not None and controller else .05)
    finally:
        if controller:
            controller.stop()
        if dashboard:
            dashboard.stop()
        for camera in cameras:
            camera.close()
        pygame.quit()
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)


def configure_file_logging():
    """Optionally mirror application logs to a bounded, persistent UTF-8 file."""
    path = os.environ.get("AXIS_LOG_FILE", "").strip()
    if not path:
        return None
    max_bytes = int(os.environ.get("AXIS_LOG_MAX_BYTES", 10 * 1024 * 1024))
    backups = int(os.environ.get("AXIS_LOG_BACKUP_COUNT", 3))
    if max_bytes <= 0 or backups <= 0:
        raise ValueError("AXIS_LOG_MAX_BYTES and AXIS_LOG_BACKUP_COUNT must be positive integers")
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(target, maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
    formatter = logging.Formatter("%(asctime)sZ %(levelname)s: %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")
    formatter.converter = time.gmtime
    handler.setFormatter(formatter)
    LOG.addHandler(handler)
    LOG.setLevel(logging.INFO)
    return handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--list", action="store_true", help="List USB/SDL joysticks without connecting to cameras")
    mode.add_argument("--inspect", action="store_true", help="Print axes and buttons to calibrate mappings; no camera traffic")
    parser.add_argument("--device", type=int, default=0, help="Device index for --inspect")
    parser.add_argument("--config", help="Optional TOML configuration; environment variables override it")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    file_handler = None
    try:
        base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path.cwd()
        # Existing service/process environment always takes precedence over .env.
        load_dotenv(os.environ.get("AXISPY_ENV_FILE", str(base / ".env")), override=False)
        file_handler = configure_file_logging()
        LOG.info("axispy starting")
        if args.config is None:
            args.config = os.environ.get("AXISPY_CONFIG")
            if args.config is None and (base / "config.toml").is_file():
                args.config = str(base / "config.toml")
        run(args)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, requests.RequestException) as exc:
        # Avoid logging requests exception URLs, which may contain network details.
        LOG.error("Controller stopped (%s): %s", type(exc).__name__, str(exc) if not isinstance(exc, requests.RequestException) else "Camera connection or authentication failed")
        return 1
    finally:
        if file_handler is not None:
            LOG.info("axispy stopped")
            LOG.removeHandler(file_handler)
            file_handler.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
