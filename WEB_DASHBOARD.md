# axispy v0.2 — web dashboard

This version is now available on **`main`**, following the merge of `web-dashboard-v2`. The original controller is preserved at tag **`v0.1.0`**; the initial dashboard release is preserved at **`v0.2.0`**. The joystick mappings, manual focus, hold-to-autofocus, environment configuration and persistent log files remain available.

## Try v0.2 without replacing the current checkout

On the Pi, stop the existing controller with `docker compose down` from its current directory. Keep that directory and its `.env` file for rollback. Do not run two controller instances against the same joystick/cameras.

Create a separate v0.2 checkout:

```sh
git clone --branch main https://github.com/camdenyoung/axispy.git axispy-v2
cd axispy-v2
```

Copy your existing `.env` into this new directory, or copy `.env.example` to `.env` and fill in camera details. Your existing camera and joystick settings are compatible. Then:

```sh
docker compose up -d --build
```

Open **`http://<pi-ip>:8080`** in a browser on the Pi's network. The dashboard works while waiting for a joystick, so you can check camera connectivity before plugging one in. Existing live console and persistent file logs remain available. The new checkout uses its own `./logs` directory by default.

To roll back, run `docker compose down` in `axispy-v2`, then `docker compose up -d` in your old checkout. No changes to the old checkout are required.

## What the page shows

- Four camera cards: configured name, URL, VAPIX channel and current availability.
- The camera actually selected by the controller, including when a requested switch is blocked because a stop is unconfirmed.
- Joystick presence and whether movement is armed or inhibited.
- A PTZ indicator on the selected card: **Active** after an accepted nonzero pan, tilt or zoom command, **Idle** after a successful stop, and **Unconfirmed** before the first command or after a control failure. This reports axispy commands, not measured camera movement. Manual focus and health checks do not count as PTZ activity. The indicator uses the existing one-second refresh, so very brief movements may occur between updates.
- Auto/manual focus, with its source: **Reported by camera** or **Last successful controller command**. If there is no evidence of the mode, it shows **Unknown**. Unavailable cameras retain an explicitly marked last-known focus mode.
- A **Debug logs** button that opens the latest 1,000 application console messages and follows new entries. This is a bounded live buffer since web startup; full persisted history remains in `./logs/axispy.log` and its rotated backups.

Health-check failures now log their reason, elapsed request time, and connect/read timeout limits to the console, persistent log file, and Debug panel. Repeated identical failures are summarized at most once per minute per camera; changed reasons and recoveries are logged immediately. Even a check superseded by a newer control response is logged for diagnosis. The card’s delayed-check notice also includes the failure reason. Connection errors include structured socket causes when available (connection refused/reset, DNS failure, unreachable host/network, or a connection closed before the HTTP response), without printing raw exception URLs or credentials. An immediate connection failure is distinct from waiting for the read timeout. These diagnostics do not establish camera rate limiting by themselves.

The browser updates every second. Four independent background probes check camera position/status every five seconds using read-only `query=position` requests. Each check uses a fresh TCP/session pool and requests `Connection: close`, preventing reuse of idle connections that camera firmware may already have closed. Digest challenge state is retained to avoid an unnecessary authentication challenge on every check. The five-second cadence is unchanged and no immediate extra retry is added. These sessions are independent of joystick control and never send movements, change focus, or run in the controller's polling thread. Successful commands also update the display. A newer command takes precedence over a probe that began earlier.

**Online** means a camera has responded to HTTP or accepted a controller command. HTTP authentication errors, busy responses (such as 503), redirects, and unsupported status-query replies are displayed as API errors separately; they do not mean the camera is offline. Control-command failures also appear separately and do not directly toggle connectivity. A single failed network check leaves the previous connectivity state in place, with a confirmation notice. **Offline** requires at least three consecutive failed background network checks and no successful response in the previous 15 seconds. One successful response restores Online immediately and resets the failure count. Before the first successful response, connectivity stays unknown until three failures confirm an outage. The last-response time is displayed, so a delayed check is distinguishable from fresh evidence. This is HTTP connectivity, not an ICMP ping or video-stream check. A read timeout does not prove the camera itself rebooted. Some camera models omit autofocus from their position response; those use the last successful focus command when available. A mode changed outside axispy can only be reflected if the camera reports it.

## Camera selection and autofocus

A short J1–J4 press selects the target without changing autofocus mode. When the old and target cameras are already confirmed stopped, selection and neutral rearming send no additional PTZ/lens commands. This avoids redundant zero-zoom commands that may provoke autofocus adjustments on camera firmware. A camera that is moving, has an unconfirmed stop, or has not yet been contacted still receives the required safety stop. First contact can therefore still produce a lens adjustment; cycling between confirmed-stopped cameras should not. The configured long hold remains the explicit way to re-enable autofocus.

## Network configuration

Compose publishes port 8080 on the Pi. Optional `.env` settings:

```dotenv
AXIS_WEB_PORT=8080
AXIS_WEB_BIND_ADDRESS=0.0.0.0
```

Recreate the container after edits: `docker compose up -d --force-recreate`. These settings control the host-facing port/address; the container listens internally on 8080. No camera credentials are included in the status API, and the live log buffer redacts the configured camera passwords. The interface is read-only and has **no authentication**: use a trusted LAN, firewall the port, or bind `AXIS_WEB_BIND_ADDRESS=127.0.0.1` behind an authenticated reverse proxy. Do not expose it directly to the Internet.

For a native executable or source install, the dashboard is opt-in:

```dotenv
AXIS_WEB_ENABLED=true
AXIS_WEB_HOST=127.0.0.1
AXIS_WEB_PORT=8080
```

Set `AXIS_WEB_HOST=0.0.0.0` for LAN access when appropriate. Source/native mode defaults to loopback and leaves the web server disabled unless explicitly enabled. `--list` and `--inspect` never start the web service. The web interface starts with the normal controller and exits with it; no separate web process needs managing.

## Validation

Automated tests exercise the real HTTP dashboard and a simulated VAPIX camera, selection/focus updates, offline/error responses, unknown focus mode, probe/control races, incremental bounded logs, credential redaction, and route restrictions. Physical Pi USB handling and real-camera reporting still need validation on your hardware.
