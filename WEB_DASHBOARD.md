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

- Four camera cards: configured name, linked URL, connection protocol and current availability.
- The camera actually selected by the controller, including when a requested switch is blocked because a stop is unconfirmed.
- Joystick presence and whether movement is armed or inhibited.
- A PTZ indicator always visible on every card, keeping the layout consistent when selection changes: **Active** after an accepted nonzero pan, tilt or zoom command, **Idle** after a successful stop, and **Unconfirmed** before the first command or after a control failure. This reports axispy commands, not measured camera movement. Manual focus and health checks do not count as PTZ activity. The indicator uses the configured dashboard refresh (one second by default), so very brief movements may occur between updates.
- Auto/manual focus, with its source: **Reported by camera** or **Last successful controller command**. If there is no evidence of the mode, it shows **Unknown**. Unavailable cameras retain an explicitly marked last-known focus mode.
- A **Debug logs** button that opens the latest 1,000 application console messages and follows new entries. This is a bounded live buffer since web startup; full persisted history remains in `./logs/axispy.log` and its rotated backups.

Health-check failures now log their reason, elapsed request time, and connect/read timeout limits to the console, persistent log file, and Debug panel. Repeated identical failures are summarized at most once per minute per camera; changed reasons and recoveries are logged immediately. Even a check superseded by a newer control response is logged for diagnosis. The card’s delayed-check notice also includes the failure reason. Connection errors include structured socket causes when available (connection refused/reset, DNS failure, unreachable host/network, or a connection closed before the HTTP response), without printing raw exception URLs or credentials. An immediate connection failure is distinct from waiting for the read timeout. These diagnostics do not establish camera rate limiting by themselves.

The browser updates every 1000 ms by default. Set `AXIS_WEB_POLL_INTERVAL_MS=250` in `.env` for a faster refresh; whole numbers from 100 to 60000 ms are supported, and unset or blank uses 1000 ms. This controls both dashboard status and the open live-log panel, with each cycle waiting for the previous requests to finish. Recreate the container after changing `.env`. This setting does not increase camera traffic or change joystick polling. Four independent background probes check camera position/status every five seconds using read-only `query=position` requests. Each check uses a fresh TCP/session pool and requests `Connection: close`, preventing reuse of idle connections that camera firmware may already have closed. Digest challenge state is retained to avoid an unnecessary authentication challenge on every check. The five-second cadence is unchanged and no immediate extra retry is added. These sessions are independent of joystick control and never send movements, change focus, or run in the controller's polling thread. Successful commands also update the display. A newer command takes precedence over a probe that began earlier.

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

## Disable unused cameras

Set `AXIS_J3_ENABLED=false` and `AXIS_J4_ENABLED=false` in `.env` to run
with J1 and J2 only. Disabled cameras are hidden from the dashboard and receive
no requests; their URLs and passwords are optional. Original J-button mappings
are preserved, and at least one camera must remain enabled. See
[README.md](README.md#disable-unused-cameras) for all options.
Apply Docker changes with `docker compose up -d --build --force-recreate`;
restart source/native installations.

## Optional live camera video

Click **Show live video** in the dashboard header to show a live preview on each
enabled camera card. Click **Hide live video** to stop the streams. Video starts
off on a browser’s first visit and remembers your on/off preference after
reloads. No `.env` changes are needed. Hidden tabs pause their streams and resume when visible again if
video is still enabled. Disabled camera slots never have a preview.

Previews use the Axis VAPIX MJPEG endpoint `/axis-cgi/mjpg/video.cgi`, requesting
640×360 (16:9) at 25 fps by default with compression 50. The camera must support MJPEG at this
resolution and the configured account must have permission to view video.
The configured camera channel, username/password, HTTPS verification and optional
camera CA bundle also apply to video. `AXIS_Jn_VERIFY_TLS=false` disables
certificate verification for that camera’s control, probes and preview; it
defaults to enabled. See the per-camera HTTPS section in README.md. Disconnected cameras show a **Camera Offline** placeholder once health checks
confirm the outage; previews resume automatically when connectivity recovers.
A camera without MJPEG support or video
permission shows a **Preview unavailable** placeholder instead of a broken image; PTZ control and health checks continue.
Toggle video off and back on to retry a failed stream.

The dashboard relays video through the existing Python server using independent
connections, so camera credentials stay on the server and the browser needs no
direct access to the cameras. No transcoder, RTSP gateway, or extra container is
required on ARM64 or x86-64 Linux. Turning video off or leaving the page closes
the viewer connections; stalled camera reads and slow viewer writes have bounded
timeouts. Each visible tab opens one stream per enabled camera, so extra tabs
increase camera and network load. MJPEG uses more bandwidth than H.264; validate
performance with your cameras and Pi before operational use. Previews have no
audio, and displayed frames are not proof that a camera is currently moving or
that a stop command succeeded.

The dashboard has no authentication. Anyone who can reach it can also view the
relayed video. Keep it on a trusted LAN or behind an authenticated reverse proxy,
as described above.

### Preview frame rate

The default preview request is 25 fps. To request a different rate, set
`AXIS_WEB_PREVIEW_FPS=30` in `.env`, then run `docker compose up -d` (or restart
source/native mode). Whole numbers from 1 to 60 are accepted; unset or blank
uses 25. Rebuild after updating application code with `docker compose up -d --build`.
The dashboard labels this as the requested rate; it does not measure delivered
frames. Actual performance depends on camera MJPEG limits, exposure settings,
network bandwidth and host/browser load. Reduce the rate if multiple previews
strain the Pi or network. `AXIS_WEB_POLL_INTERVAL_MS` controls status refresh only
and does not limit live video frame rate.

## Host CPU and memory usage

The dashboard shows **Host CPU usage** and **Host memory usage**, including used
and total RAM. Use **Hide system usage** / **Show system usage** in the header
to hide or show both readings between the app name and the header buttons.
Their space is reserved, so toggling them does not move the camera cards. They
start hidden on a browser’s first visit, then restore your saved preference after
a reload. These are statistics for the Linux host running axispy (your Pi or
x86-64 Linux machine), including other applications on that host. In a VM, the
host is the Linux VM. They are not measurements of the cameras or browser, and
do not represent Docker container usage or its configured resource limits.

CPU usage is the busy percentage across all CPU cores between samples; 100%
means all cores are busy. CPU I/O wait counts as idle. RAM usage is total minus
Linux `MemAvailable`, so reclaimable file cache is treated as available memory.
The first CPU reading displays **Measuring…** until a second sample is available.

Metrics are read from Linux `/proc/stat` and `/proc/meminfo`, cached for one
second across viewers, and refreshed with normal dashboard status polling.
No new Python dependencies, Docker mounts, privileges, or `.env` settings are
needed. If the platform does not expose these statistics, the dashboard displays
**Unavailable** while camera monitoring and control continue normally.

## Responsive live-preview layout

All camera counts use the same 600px and 1280px column boundaries. Preview
positions follow the available card space:

| Viewport width | 1 camera | 2 cameras | 3 cameras | 4 cameras | Preview position |
| --- | --- | --- | --- | --- | --- |
| Below 600px | 1 column | 1 column | 1 column | 1 column | Below details |
| 600–1280px | 1 column | 2 columns | 2 columns | 2 columns | Right of details |
| 1281–1439px | 1 column | 2 columns | 3 columns | 4 columns | Right for 1–2 cameras; below for 3–4 |
| 1440px and above | 1 column | 2 columns | 3 columns | 4 columns | Below details; single camera stays on the right |

Two-camera previews remain on the right through intermediate desktop widths,
avoiding a full-width preview jump between 1280px and the 1440px desktop layout.
These column counts apply with video on or off. Live previews and offline
placeholders share the same 16:9 sizing. Preview layout changes do not restart
working streams. On narrow screens, text wraps and normal page scrolling remains
available.

At desktop sizes of at least 1440 pixels wide and 720 pixels high, the header,
selected-camera panel, cards and footer fill one viewport. This separate height
fitting rule does not change preview positions or column counts. Frames scale to
fit without cropping or stretching. Header system usage reserves its space, so
showing or hiding it does not move the cards. Debug logs open below the desk and
remain scrollable.

Optional browser regression check (requires Playwright and Chromium):

```sh
python tests/browser_dashboard_layout.py
```

## Preview quality

Set `AXIS_WEB_PREVIEW_QUALITY` in `.env` to choose the requested 16:9 MJPEG
resolution for all enabled camera previews:

| Value | Resolution |
| --- | --- |
| `low` (default) | 640×360 |
| `medium` | 1280×720 (HD) |
| `high` | 1920×1080 (Full HD) |

For example, `AXIS_WEB_PREVIEW_QUALITY=medium` requests 720p. Unset or blank uses
`low`; values are case-insensitive. Invalid values fail at startup with a clear
configuration error. The configured camera must support MJPEG at the selected
resolution. The dashboard scales the full frame to fit without cropping or
stretching; the requested resolution does not change card sizing.

After editing `.env`, run `docker compose up -d` (or restart source/native mode).
Rebuild after updating code with `docker compose up -d --build`. Refresh the
browser after updating. Higher quality uses more bandwidth and camera/host/browser
resources, especially with several previews. Frame rate remains independently
controlled by `AXIS_WEB_PREVIEW_FPS`.

## Remembered dashboard preferences

The dashboard remembers whether **system usage**, **live video**, and **Debug
logs** were shown, and restores all three after a refresh or reopening the page.
On a first visit they are all hidden/off. Turning a feature off also saves that
choice. Restoring live video reconnects previews for the enabled cameras; it does
not send any camera control commands.

Preferences use browser local storage, not cookies, and stay in that browser for
the dashboard’s address (scheme, hostname/IP and port). A different browser,
device, or dashboard address has its own defaults. Clearing site data resets the
preferences; private browsing may discard them when the session ends. Existing
open tabs are not changed automatically when another tab changes a preference.
Only three on/off values are saved; no camera credentials, frames, or log content
are stored. If local storage is blocked or full, the buttons continue to work
and the last successfully saved preferences, if any, are used on the next visit.
Hidden tabs still pause video, and leaving the page releases its streams without
changing the saved video preference.

Optional browser preference checks (requires Playwright and Chromium):

```sh
python tests/browser_dashboard_preferences.py
```

## Camera connection badges

Each camera card shows its configured **HTTP** or **HTTPS** protocol beside its
availability. **HTTPS · unverified** means certificate verification has been
explicitly disabled for that camera. Hover over the badge for the verification
setting. The badge describes configuration; Online/Offline separately reports
camera reachability. HTTPS with verification enabled is not a claim that the
camera is currently connected or that its certificate has passed validation.

The camera address links to that camera's own web interface in a new tab using
its configured HTTP/HTTPS URL, without adding credentials. The browser needs
direct network access to the camera and handles its own login/certificate trust;
the application's `VERIFY_TLS=false` setting does not bypass browser warnings.

## Container network traffic

**Network in** and **Network out** appear with CPU and memory under the existing
system-usage toggle. They show received and sent traffic in **Mbps** (megabits
per second), averaged over the interval between samples. They are rates, not
cumulative transfer totals or a percentage of link capacity. No `.env` setting,
extra dependency, container privilege, or mount is required.

The counters come from `/proc/net/dev` and cover non-loopback interfaces in the
application's network namespace. With the supplied Compose file's standard bridge
networking, this measures traffic into and out of the **container**, including
camera streams, dashboard viewers, control requests and other container traffic.
CPU and memory readings still describe the Linux host. In native mode or Docker
host-network mode, network readings also describe the host; they cannot isolate
one application's traffic in a shared host namespace. Additional tunnel/virtual
interfaces can affect aggregated totals.

Samples are cached for one second across viewers. The first reading displays
**Measuring…**; unreadable counters display **Unavailable**. New or reset interface
counters establish a fresh baseline instead of producing a traffic spike. Clearing
a connection does not reset the cumulative counters; the display continues to
measure current receive/transmit rates. Multiple viewers increase outgoing
traffic, and the current one-stream-per-camera-per-viewer relay can also increase
incoming traffic.

Optional browser check:

```sh
python tests/browser_network_usage.py
```
