# axispy

Control four Axis PTZ cameras from an Axis T8311 USB joystick using the cameras' VAPIX HTTP API. Pan and tilt use two joystick axes, zoom uses a third (typically the twist axis), and physical J1–J4 buttons select the camera. Only the selected camera receives movement commands.

Requires Python 3.11+ and an operating system that exposes the T8311 as an SDL joystick. Linux, Windows, and macOS are supported by the dependencies, and the mapping below was confirmed from user-supplied T8311 inspection output. Check it on your own device if behavior differs. No proprietary Axis client is needed if SDL can see the joystick.

## Raspberry Pi / standalone deployment

For an environment-variable-only Docker Compose deployment or a standalone executable that needs no virtual environment at runtime, see [DEPLOYMENT.md](DEPLOYMENT.md).

## Install

Run on the computer physically connected to the joystick, with network access to the cameras:

```sh
python -m venv .venv
# Linux/macOS:
. .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e .
axispy --list
axispy --inspect
```

Move each axis independently and press J1, J2, J3, J4. `--inspect` prints **zero-based SDL indices**, without contacting cameras. Copy `config.example.toml` to `config.toml`, then set `pan_axis`, `tilt_axis`, `zoom_axis`, and `camera_buttons` to your observed values. The example matches the T8311 inspection reported below. Adjust inversion and deadzone as needed. `device` selects the USB joystick index printed by `--list`; inspect again if device enumeration changes.

### Confirmed T8311 mapping

Reported device: `InduKey(c) AXIS T8311 Joystick`, 3 axes, 6 buttons; SDL GUID `0300455c252200000232000001010000`.

| Physical control | SDL input | Configuration / behavior |
| --- | --- | --- |
| Left / right | Axis 0, negative / positive | `pan_axis = 0`, `invert_pan = false` |
| Up / down | Axis 1, negative / positive | `tilt_axis = 1`, `invert_tilt = true` (VAPIX positive tilt is up) |
| Twist left / right | Axis 2, negative / positive | `zoom_axis = 2`, `invert_zoom = false` (left zooms out, right zooms in) |
| J1 / J2 / J3 / J4 | Buttons 0 / 1 / 2 / 3 | `camera_buttons = [0, 1, 2, 3]` |
| L / R | Buttons 4 / 5 | Hold L for focus near, R for focus far; release to stop |

The trace included incidental cross-axis values up to 0.153 while moving another axis. The default deadzone is 0.18 to suppress those small unintended pan/zoom commands. Existing `config.toml` files are not overwritten: change their `deadzone` manually. Small center offsets such as +0.004 are also filtered. Axis extrema did not all reach ±1; full stick travel may therefore produce slightly less than the configured maximum speed.

If no joystick appears, check local OS USB/game-controller recognition and permissions. On Linux your user needs read access to the relevant `/dev/input/event*` device; use your distribution's input-device permission mechanism. A joystick plugged into your laptop is not automatically available inside a remote cloud environment or container. USB forwarding/device passthrough must be configured separately if running there.

## Configure cameras

The four `[[cameras]]` entries correspond to J1–J4 in order. Set each camera origin (`https://host`), username, and PTZ channel (`channel`, normally 1). Each camera must support continuous PTZ commands at `/axis-cgi/com/ptz.cgi`, and the account must have PTZ control permission. The controller uses HTTP Digest authentication.

Supply passwords through environment variables rather than the config file:

```sh
export AXIS_J1_PASSWORD='your-camera-1-password'
export AXIS_J2_PASSWORD='your-camera-2-password'
export AXIS_J3_PASSWORD='your-camera-3-password'
export AXIS_J4_PASSWORD='your-camera-4-password'
axispy --config config.toml
```

In PowerShell use `$env:AXIS_J1_PASSWORD = '...'`, and likewise for J2–J4. All four passwords are required at startup. Set variables securely on the local host; avoid committing credentials. `config.toml` is ignored by Git.

HTTPS certificates are verified. For a private camera CA, add `ca_bundle = "/absolute/path/to/camera-ca.pem"` to each applicable camera entry. HTTP can be configured explicitly for cameras that require it, but does not encrypt camera traffic. Camera requests deliberately bypass ambient proxy configuration and use a direct network connection.

## Operation

- Start with the joystick centered. J1 is selected initially.
- Move the stick for pan/tilt and the configured zoom axis for zoom. Speeds scale from the deadzone to the configured maximum (0–100).
- Press J1–J4 to stop the old camera and select the corresponding camera. Center the joystick before moving the newly selected camera. An offline camera that has not been moving does not block selecting another camera. If the old camera may still be moving and its stop cannot be confirmed, selection stays on it until a stop succeeds; retry your selection after reconnection.
- Hold **L** for manual focus near (in) and **R** for focus far (out). Release to stop focus, even while panning, tilting or zooming; holding both buttons stops focus. Default focus speed is 30 (range 1–100).
- Manual focus sends `autofocus=off` and `continuousfocusmove`; autofocus stays off after release so the camera does not undo your focus adjustment. Hold that camera’s **J1, J2, J3 or J4 button for five continuous seconds** to re-enable autofocus. Set `AXIS_AUTOFOCUS_HOLD_SECONDS=3` in `.env` to change the hold duration; it accepts positive numbers (including decimals) and defaults to 5 seconds when unset or blank. A short press still selects the camera immediately. Each hold triggers once; release and hold again to repeat. Logs confirm `Autofocus enabled for Jn` only after a successful request. If the camera is unavailable, release and repeat after recovery. Focus support depends on camera/lens capabilities.
- If L/R is held when autofocus is restored, manual focus is paused until both focus buttons are released. Holding a J button across USB attachment/reconnection does not trigger autofocus; release it and start a fresh hold.
- Startup, camera switches and reconnects require centering the stick **and releasing both focus buttons** before control is armed. Focus is also stopped during camera switches, USB disconnects and shutdown.
- Releasing the controls sends zero-speed commands. Optionally configure `deadman_button` for hold-to-move behavior. Button assignments must be distinct; to reuse L or R as a deadman, disable its focus mapping with `focus_near_button = -1` or `focus_far_button = -1`.
- With no joystick attached, the controller stays running and checks for USB every two seconds. Unplugging it triggers stop attempts and returns to waiting; reconnecting requires centering before movement.
- Camera connection, authentication, or PTZ request failures disarm movement and retry with stop commands every five seconds. Recovery requires a successful stop and then centering before movement; held-stick commands are never replayed. Repeated identical failures log once, with new messages when the failure changes or the camera recovers.
- Ctrl+C and SIGTERM trigger stop attempts before exit. Invalid configuration still fails at startup so it can be corrected.

Input changes are sent at up to `rate_hz` (10 by default). Unchanged movement commands are coalesced, with watchdog refreshes every 0.5 seconds; idle cameras are not polled continuously. Focus changes and release stops bypass the refresh interval. `autofocus=off` is sent once per manual focus hold instead of on every refresh. Focus/autofocus requests use `focus_timeout` (2 seconds by default) for reading the response; PTZ and connection timeouts retain the `timeout` setting (0.5 seconds by default). A timed-out movement causes one immediate stop attempt; if acknowledged, the controller stays connected but requires centering/releasing before resuming. If stopping also fails, normal outage backoff applies. Each HTTP operation has bounded connect/read timeouts; an unresponsive camera can delay polling and button handling. This is a small synchronous controller, not a hard real-time control system.

Commands include `continuouspantiltmove`, `continuouszoommove`, `continuousfocusmove` (when focusing), and `continuoustimeout` (default 2 seconds). Confirm support and timeout behavior for your camera model/firmware in the [Axis VAPIX PTZ documentation](https://developer.axis.com/vapix/network-video/pantiltzoom-api/). A successful HTTP response does not prove the camera enforces a watchdog. With low maximum speeds, test neutral stop, camera switching, unplugging the joystick, and loss of network connectivity before operational use. If a camera ignores the timeout, motion may continue after a process crash or network outage; a client cannot deliver a stop over a broken connection. Keep independent camera controls available.

## Development and validation

```sh
python -m pip install pytest
python -m pytest -q
```

Tests cover axis scaling, invalid configuration, the hold button, neutral rearming, camera-switch stop ordering/failure, manual focus and release stopping, shutdown retries, outage backoff, USB reconnects, and real HTTP requests against a local simulated VAPIX endpoint. No test contacts physical cameras. USB mappings have been confirmed from user-supplied inspection output; real camera authentication, mechanical movement, and firmware timeout behavior still require hardware validation.

To develop in the cloud, use the existing checkout; no additional Git worktree is needed. `axispy --list` exercises SDL discovery even when no USB device is passed through. Run the controller itself on a host with both joystick access and camera connectivity.
