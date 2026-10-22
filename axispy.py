"""Read an SDL USB joystick and control four Axis cameras through VAPIX."""
from __future__ import annotations

import argparse
import logging
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
STOP = (0, 0, 0)


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

    @classmethod
    def load(cls, path: str | None = None) -> Settings:
        data = {
            "joystick": dict(device=0, pan_axis=0, tilt_axis=1, zoom_axis=2,
                             invert_pan=False, invert_tilt=True, invert_zoom=False,
                             deadzone=.18, max_pan=70, max_tilt=70, max_zoom=50,
                             camera_buttons=[0, 1, 2, 3], deadman_button=-1),
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
        for key, cast in (("rate_hz", float), ("timeout", float), ("continuous_timeout", int)):
            if f"AXIS_{key.upper()}" in os.environ:
                data["control"][key] = cast(os.environ[f"AXIS_{key.upper()}"])
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
        if not 0 <= j.get("deadzone", .18) < 1:
            raise ValueError("deadzone must be between 0 (inclusive) and 1 (exclusive)")
        for axis in ("pan", "tilt", "zoom"):
            if type(j.get(f"max_{axis}", 70)) is not int or not 0 <= j.get(f"max_{axis}", 70) <= 100:
                raise ValueError("Maximum speeds must be integers from 0 to 100")
        c = data.get("control", {})
        rate, timeout = float(c.get("rate_hz", 10)), float(c.get("timeout", .5))
        watchdog = c.get("continuous_timeout", 2)
        if not .5 <= rate <= 50 or not .1 <= timeout <= 10:
            raise ValueError("rate_hz must be 0.5-50; timeout must be 0.1-10 seconds")
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
        return cls(j, cameras, rate, timeout, watchdog)


class CameraCommandError(RuntimeError):
    """A camera response suitable for reporting without request credentials."""


class Camera:
    def __init__(self, config: dict, timeout: float, watchdog: int):
        password = os.environ.get(config["password_env"])
        if not password:
            raise ValueError(f"Set {config['password_env']} before starting")
        self.name = config["name"]
        self.url = config["url"].rstrip("/") + "/axis-cgi/com/ptz.cgi"
        self.channel = config.get("channel", 1)
        self.timeout = timeout
        self.watchdog = watchdog
        self.session = requests.Session()
        self.session.auth = HTTPDigestAuth(config["username"], password)
        # Do not route local camera credentials through ambient HTTP proxies.
        self.session.trust_env = False
        self.session.verify = config.get("ca_bundle", True)
        if not isinstance(self.session.verify, (str, bool)) or self.session.verify is False:
            raise ValueError("Use a trusted ca_bundle; TLS verification cannot be disabled")

    def move(self, motion: tuple[int, int, int]):
        pan, tilt, zoom = motion
        response = self.session.get(self.url, params={
            "camera": self.channel,
            "continuouspantiltmove": f"{pan},{tilt}",
            "continuouszoommove": str(zoom),
            "continuoustimeout": self.watchdog,
        }, timeout=self.timeout, allow_redirects=False)
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
        except (requests.RequestException, RuntimeError) as exc:
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
        if index in self.unavailable:
            LOG.info("%s reconnected; center joystick to enable movement", self.cameras[index].name)
        self.unavailable.discard(index)
        self.failure_reason.pop(index, None)
        self.retry_at[index] = 0
        if motion == STOP:
            self.may_be_moving.discard(index)
            self.stop_required.discard(index)
        return True

    def select(self, index):
        if index == self.selected:
            return
        self.armed = False
        stopped = self._move(self.selected, STOP)
        if not stopped and self.selected in self.may_be_moving:
            LOG.warning("Cannot switch: stop for %s is unconfirmed; retry after reconnection",
                        self.cameras[self.selected].name)
            return
        self.selected = index
        self.stop_required.add(index)
        self._move(index, STOP)
        LOG.info("Selected %s; center joystick to enable movement", self.cameras[index].name)

    def send(self, motion):
        if self.selected in self.stop_required:
            self.armed = False
            self._move(self.selected, STOP)
            return
        if motion == STOP:
            self.armed = True
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


def read_motion(joystick, config):
    if config.get("deadman_button", -1) >= 0 and not joystick.get_button(config["deadman_button"]):
        return STOP
    return tuple(speed(joystick.get_axis(config[f"{axis}_axis"]),
                       config.get("deadzone", .18), config.get(f"max_{axis}", 70),
                       config.get(f"invert_{axis}", False)) for axis in ("pan", "tilt", "zoom"))


def run(args):
    # SDL joystick polling needs no graphical window or display server.
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    import pygame
    pygame.display.init()
    pygame.joystick.init()
    cameras = []
    controller = None
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
                cameras.append(Camera(config, settings.timeout, settings.continuous_timeout))
            controller = Controller(cameras)
            LOG.info("Selected %s; waiting for joystick and camera readiness", cameras[0].name)
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
                        controller.select(settings.joystick["camera_buttons"].index(event.button))
                        last_motion = None
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
                            if max(*j["camera_buttons"], j.get("deadman_button", -1)) >= candidate.get_numbuttons():
                                raise ValueError("Button index out of range; run --inspect")
                        joystick = candidate
                        LOG.info("Joystick connected: %s; center joystick to enable movement", joystick.get_name())
                        if controller:
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
                    except pygame.error:
                        LOG.warning("Joystick read failed; stopping camera and waiting for USB")
                        joystick.quit()
                        joystick = None
                        controller.stop()
                        next_connect = time.monotonic() + 2
                        waiting_logged = True
                        continue
                    if now >= next_send or (motion == STOP and motion != last_motion):
                        controller.send(motion)
                        last_motion = motion
                        next_send = time.monotonic() + 1 / settings.rate
            time.sleep(.005 if joystick is not None and controller else .05)
    finally:
        if controller:
            controller.stop()
        for camera in cameras:
            camera.close()
        pygame.quit()
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--list", action="store_true", help="List USB/SDL joysticks without connecting to cameras")
    mode.add_argument("--inspect", action="store_true", help="Print axes and buttons to calibrate mappings; no camera traffic")
    parser.add_argument("--device", type=int, default=0, help="Device index for --inspect")
    parser.add_argument("--config", help="Optional TOML configuration; environment variables override it")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path.cwd()
        # Existing service/process environment always takes precedence over .env.
        load_dotenv(os.environ.get("AXISPY_ENV_FILE", str(base / ".env")), override=False)
        if args.config is None:
            args.config = os.environ.get("AXISPY_CONFIG")
            if args.config is None and (base / "config.toml").is_file():
                args.config = str(base / "config.toml")
        run(args)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, requests.RequestException) as exc:
        # Avoid logging requests exception URLs, which may contain network details.
        LOG.error("Controller stopped (%s): %s", type(exc).__name__, str(exc) if not isinstance(exc, requests.RequestException) else "Camera connection or authentication failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
