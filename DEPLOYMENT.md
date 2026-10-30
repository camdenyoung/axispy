# Raspberry Pi deployment

The v0.2 web dashboard is available on `main`. Follow [WEB_DASHBOARD.md](WEB_DASHBOARD.md) for a separate checkout and rollback instructions. The original controller remains available at tag `v0.1.0`.

Recommended: Raspberry Pi OS **64-bit** (`uname -m` should print `aarch64`), with the T8311 plugged directly into the Pi and network access to all four cameras. The Pi needs no graphical desktop. Camera connectivity, USB access, and actual PTZ behavior must be tested on the Pi.

## Docker Compose (recommended)

Install Docker Engine and the Compose plugin using the [official Debian instructions](https://docs.docker.com/engine/install/debian/) for your OS version. Enable Docker at boot with `sudo systemctl enable --now docker`. Run these commands with an account allowed to use Docker (or prefix Docker commands with `sudo`).

```sh
git clone --branch main https://github.com/camdenyoung/axispy.git
cd axispy
cp .env.example .env
chmod 600 .env
nano .env
```

Enter the four camera URLs, usernames and passwords in `.env`. Defaults already match the reported T8311 mappings. No TOML file or virtual environment is needed. Then:

```sh
docker compose build
docker compose run --rm axispy --list
docker compose run --rm axispy --inspect
# Ctrl+C to finish inspection, then:
docker compose up -d
docker compose logs -f
```

The container starts the controller without extra arguments and stays running when USB or a camera is unavailable. It checks for the joystick every two seconds, retries failed camera operations with stop commands every five seconds, and requires centering after reconnection. Docker restarts the container only if the process exits unexpectedly; it also starts again after reboot when Docker starts. The device index may change if multiple controllers are attached; recheck `--list` and set `AXIS_DEVICE` if necessary. Initial selection is J1 on process startup. The selected camera is preserved across USB and camera reconnects. Movement is inhibited until centered. If an offline camera has not been moving, J1–J4 can select another camera; if its previous movement or stop is uncertain, switching is blocked until a stop is confirmed.

Logs report waiting, failure reasons (including HTTP status), and reconnection without logging passwords or repeating identical failures on every poll. Invalid configuration or missing required password variables still fail at startup; these must be corrected in `.env` before deployment.

Only run one controller instance at a time. Before running inspection or manual controller commands after deployment, use `docker compose down` first. A cloud machine or a Mac Docker VM does not automatically have access to the Pi's USB devices.

```sh
# Stop (sends stop commands before exiting):
docker compose down
# Apply .env changes:
docker compose up -d --force-recreate
# Update code and rebuild:
git pull --ff-only
docker compose up -d --build
```

The image is built locally for the Pi's architecture; no x86 image or prebuilt image download is assumed. For a TLS inspection proxy during image builds, Dockerfile accepts an optional BuildKit secret named `build_ca` containing the trusted CA PEM (for example, `docker build --secret id=build_ca,src=/path/to/ca.pem -t axispy .`). It is used only during dependency installation and is not retained in the image; TLS verification stays enabled. Standard 64-bit Raspberry Pi OS is the target. 32-bit Pi OS is not validated and may require additional build dependencies.

### Persistent logs on the Pi

The application logs to both the console (`docker compose logs -f`) and a rotating file. Compose mounts `./logs` on the Pi at `/var/log/axispy` inside the container, and writes `axispy.log` there. Docker creates the host directory if needed. No shell inside the container is required:

```sh
tail -F logs/axispy.log
# Include current and rotated logs when diagnosing earlier problems:
ls -lh logs/axispy.log*
```

If you pre-create a custom host log directory, it must be writable by the container’s root user. With the default capability restrictions, a directory owned by a different user and writable only by that user will fail. For a dedicated log directory, initialize it with `sudo mkdir -p logs`, `sudo chown root:root logs`, and `sudo chmod 755 logs` (adjust the path for `AXIS_LOG_DIR`). Docker normally creates the default directory with suitable ownership automatically.

Log entries have UTC timestamps. Files append across container recreation and reboot; the default rotation keeps the current file plus three backups of up to 10 MiB each (roughly 40 MiB total). Backups are `axispy.log.1` (newest) through `axispy.log.3`. Reading and rotation use the bind mount even though the rest of the container filesystem is read-only. The container's root user creates the files; on typical Raspberry Pi OS they are readable by other users, but host permissions/umask can require `sudo tail -F logs/axispy.log`.

Optional `.env` settings:

```dotenv
AXIS_LOG_DIR=./logs
AXIS_LOG_MAX_BYTES=10485760
AXIS_LOG_BACKUP_COUNT=3
```

`AXIS_LOG_DIR` changes the host directory and can be an absolute path; Compose fixes the container file to `/var/log/axispy/axispy.log`. For native executable/source use, set `AXIS_LOG_FILE=/path/to/axispy.log` to enable file logging. File logging is optional outside Compose. File-size and backup-count settings must be positive integers. An unwritable log location causes a clear startup error rather than silently dropping the requested persistent log. Logs are not automatically migrated from Docker's old console log history.

After updating the code or settings, run `docker compose up -d --build --force-recreate`. Keep `.env` private; log contents include camera names, status and error details, with existing credential redaction. Do not configure two controller instances to write the same rotating file.

### Matching speeds between cameras

Global `AXIS_MAX_PAN`, `AXIS_MAX_TILT` and `AXIS_MAX_ZOOM` remain the defaults. Optional per-camera settings override them independently:

```dotenv
AXIS_J1_MAX_ZOOM=30
AXIS_J2_MAX_ZOOM=50
AXIS_J1_MAX_PAN=60
AXIS_J1_MAX_TILT=60
```

Use J1–J4 with any of the three axes. Values must be integers from 0 to 100; 0 disables that axis for that camera. Unset or blank variables retain a per-camera TOML value if one is configured, otherwise inherit the global value. In TOML, add `max_pan`, `max_tilt` or `max_zoom` to the relevant `[[cameras]]` section. Per-camera environment values override per-camera TOML values; both override global defaults. Recreate the container after changing `.env`.

The same VAPIX percentage does not guarantee the same physical or perceived speed across camera configurations, lenses, zoom positions or firmware. These limits provide manual calibration, not automatic speed synchronization. Start at equivalent zoom positions, use the same joystick deflection, and reduce the faster camera's limit gradually. The example values above are illustrative; no physical calibration has been measured.

### Slow focus responses

Focus commands default to a 2-second response timeout, while the connection/PTZ timeout remains 0.5 seconds. Set `AXIS_FOCUS_TIMEOUT=3.0` in `.env` for a slower camera (range 0.1–10 seconds); the effective focus read timeout is never shorter than `AXIS_TIMEOUT`. Recreate the container to apply `.env` changes. Identical held-focus requests are coalesced with watchdog refreshes twice per second, and autofocus is disabled once per focus hold. Release/reversal requests are sent immediately; a synchronous HTTP request already in progress can still delay polling until its timeout. A `ReadTimeout` means a response did not arrive in time; it does not prove the camera rebooted or lost its network connection. After a timed-out movement, the controller attempts a stop immediately and requires centering/releasing controls before resuming. A failed stop retains the normal reconnect backoff.

### Device access and certificates

Compose mounts `/dev/input` read-only and permits reads of Linux input devices (major 13). It does not enable privileged mode or mount all of `/dev`. The container runs as root with all Linux capabilities dropped to read root-owned input devices. The input-directory mount allows device nodes to reappear after reconnect. This grants access to other input devices on the Pi too; use a dedicated Pi where practical. Missing `/dev/input`, an unrecognized joystick, or host security policies can still prevent access; check `ls -l /dev/input` and `docker compose run --rm axispy --list`.

HTTPS verification is enabled by default. For a private camera CA, add a read-only certificate mount under `volumes` in `compose.yaml`, such as `./certs:/certs:ro`, and set `AXIS_J1_CA_BUNDLE=/certs/camera-ca.pem` in `.env` for each applicable camera. Camera requests go directly to the cameras and do not use ambient HTTP proxies. No inbound container ports are needed.

### Environment variables

Required for each `n` from 1 to 4: `AXIS_Jn_URL` and `AXIS_Jn_PASSWORD`. `AXIS_Jn_USERNAME` defaults to `operator`; `AXIS_Jn_CHANNEL` defaults to `1`; `AXIS_Jn_CA_BUNDLE` is optional. See `.env.example` for joystick and timing overrides. L/R default to focus near/far. Override with `AXIS_FOCUS_NEAR_BUTTON=4`, `AXIS_FOCUS_FAR_BUTTON=5`, and `AXIS_FOCUS_SPEED=30` (1–100). Use `-1` to disable a focus button, or swap the two indices to reverse directions. Autofocus is disabled on manual focus commands. Hold the relevant J1–J4 button for five seconds to re-enable it (once per hold), or use the camera interface. A short J press still selects immediately. Release L/R before making another manual focus adjustment; any L/R already held when autofocus is restored is suppressed until released. Set `AXIS_AUTOFOCUS_HOLD_SECONDS=3` in `.env` to change the hold duration. Positive decimal values are supported; unset or blank defaults to 5 seconds (or `control.autofocus_hold_seconds` if explicitly set in TOML). After changing `.env`, apply it with `docker compose up -d --force-recreate`. No `.env` changes are required to enable the default L/R mappings.

Passwords containing `$`, `#`, or spaces should be single-quoted in `.env`. Never commit `.env`; Docker administrators can inspect container environment variables, so restrict access to the Pi and Docker. The application never prints password values.

## Diagnosing camera responses

Run `docker compose logs --tail=100 -f axispy`. A `redirect to HTTPS` message means the camera's HTTP endpoint redirects rather than accepting the command: enable HTTP in the camera's settings or configure HTTPS with a trusted certificate. A `PTZ command rejected: Error: ...` message includes the camera's VAPIX rejection text; check the named parameter, PTZ capabilities, selected channel, and user permissions. The controller does not automatically follow redirects. HTTP 401/403 indicate authentication/authorization failures. Response error details are bounded and the configured password is redacted.

## Standalone executable

Install build prerequisites with `sudo apt-get install -y python3-venv binutils`, then build once **on the Pi** (or a machine with the same OS/architecture):

```sh
./scripts/build-binary.sh
cp .env.example dist/.env
chmod 600 dist/.env
nano dist/.env
./dist/axispy
```

`dist/axispy` bundles Python and the dependencies: launching it requires no Python installation, virtual-environment activation, TOML file, or command-line options. Keep `.env` next to the executable. It is a native executable for the machine where it was built: an x86 Linux build will not run on a Pi, and a macOS build will not run on Linux. The one-file executable extracts bundled libraries into a temporary directory at startup; that directory must allow execution. Host system libraries and USB device permissions are still required. For missing SDL libraries on Raspberry Pi OS, install `libsdl2-2.0-0` and `libglib2.0-0` using apt.

The build uses a virtual environment internally, but the resulting executable does not need one to run. If launching it through systemd, use its absolute path for `ExecStart` and allow enough shutdown time for stop retries. Docker Compose already provides startup and restart management without writing a separate service.

## Configuration precedence

Existing process environment overrides `.env`; environment settings override TOML; built-in defaults supply the confirmed joystick mapping. Native executables load `.env` and optional `config.toml` beside the executable; source installations use the working directory. `AXISPY_ENV_FILE` can select another dotenv path, and `AXISPY_CONFIG` or `--config` can select a TOML file. Explicitly requested missing TOML files fail rather than silently falling back.

The HTTP tests and container/executable smoke checks do not validate real camera movement, authentication or watchdog behavior. Complete the low-speed hardware checks in README.md before leaving the controller unattended.

## Disable unused cameras

Set `AXIS_J3_ENABLED=false` and `AXIS_J4_ENABLED=false` in `.env` to run
with J1 and J2 only. Disabled cameras are hidden from the dashboard and receive
no requests; their URLs and passwords are optional. Original J-button mappings
are preserved, and at least one camera must remain enabled. See
[README.md](README.md#disable-unused-cameras) for all options.
Apply Docker changes with `docker compose up -d --build --force-recreate`;
restart source/native installations.

## HTTPS cameras with untrusted certificates

Set the camera URL to `https://...` and add `AXIS_J1_VERIFY_TLS=false` in `.env`
(likewise J2–J4) to explicitly skip certificate verification for that camera.
This covers control commands, dashboard health checks, and live video, and
supersedes its CA bundle. Other cameras keep their own verification settings.
Apply `.env` changes with `docker compose up -d`; rebuild after updating code.
HTTPS remains encrypted but the camera identity is not verified. See
[README.md](README.md#per-camera-https-certificate-verification) for defaults,
TOML precedence, and the trusted-CA alternative.

## Diagnosing slow camera selection or control

A control request taking at least 0.25 seconds logs its camera, operation
(stop/movement/autofocus), result and total elapsed time. The same slow operation
and result is logged at most once every 30 seconds per camera. The diagnostic
also includes final-response header timing, response-body framing, and configured
connect/read limits, without raw exception messages, camera URLs or passwords.
Find these messages in **Debug logs** or `docker compose logs`.

A fast header response with a much slower total and `body framing connection-close`
can indicate waiting for the camera to finish/close its response. A slow header
response instead includes connection, authentication or camera processing time.
The header timing is for the final response. When Requests retries a Digest
challenge, it does not provide this timing; the diagnostic marks it unavailable
instead of reporting a misleading zero. Authentication round trips are still
included in the total. These measurements help distinguish the causes;
they do not change timeouts, connection handling, stop confirmation or rearming.

### Read-only timing comparison with the controller client

To compare curl with the application's own Python/Requests client, run:

```sh
docker compose exec axispy axispy --diagnose-camera J3
```

The check uses the running container's injected environment and the same camera
configuration, credentials, TLS policy, channel, timeouts and Session/Digest
implementation as the control client. It makes three consecutive read-only
`query=position` requests in one diagnostic session. It does not send stop,
movement, focus or autofocus commands, initialize SDL, or open the service's
rotating log file. The running controller does not need to be stopped.

Each sample prints time to final headers (including Digest challenge exchanges),
body-read time, total time, status code, HTTP response version, authentication
challenge count and body framing. It does not print URLs, usernames, passwords,
or response contents. The first and repeat samples help distinguish initial
connection/authentication costs from repeat-request delays. A diagnostic session
is separate from the active controller; it does not keep the active connection
warm. A failed query returns a nonzero exit status with a sanitized reason.
