# Raspberry Pi deployment

Recommended: Raspberry Pi OS **64-bit** (`uname -m` should print `aarch64`), with the T8311 plugged directly into the Pi and network access to all four cameras. The Pi needs no graphical desktop. Camera connectivity, USB access, and actual PTZ behavior must be tested on the Pi.

## Docker Compose (recommended)

Install Docker Engine and the Compose plugin using the [official Debian instructions](https://docs.docker.com/engine/install/debian/) for your OS version. Enable Docker at boot with `sudo systemctl enable --now docker`. Run these commands with an account allowed to use Docker (or prefix Docker commands with `sudo`).

```sh
git clone https://github.com/camdenyoung/axispy.git
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

### Slow focus responses

Focus commands default to a 2-second response timeout, while the connection/PTZ timeout remains 0.5 seconds. Set `AXIS_FOCUS_TIMEOUT=3.0` in `.env` for a slower camera (range 0.1–10 seconds); the effective focus read timeout is never shorter than `AXIS_TIMEOUT`. Recreate the container to apply `.env` changes. Identical held-focus requests are coalesced with watchdog refreshes twice per second, and autofocus is disabled once per focus hold. Release/reversal requests are sent immediately; a synchronous HTTP request already in progress can still delay polling until its timeout. A `ReadTimeout` means a response did not arrive in time; it does not prove the camera rebooted or lost its network connection. After a timed-out movement, the controller attempts a stop immediately and requires centering/releasing controls before resuming. A failed stop retains the normal reconnect backoff.

### Device access and certificates

Compose mounts `/dev/input` read-only and permits reads of Linux input devices (major 13). It does not enable privileged mode or mount all of `/dev`. The container runs as root with all Linux capabilities dropped to read root-owned input devices. The input-directory mount allows device nodes to reappear after reconnect. This grants access to other input devices on the Pi too; use a dedicated Pi where practical. Missing `/dev/input`, an unrecognized joystick, or host security policies can still prevent access; check `ls -l /dev/input` and `docker compose run --rm axispy --list`.

HTTPS verification stays enabled. For a private camera CA, add a read-only certificate mount under `volumes` in `compose.yaml`, such as `./certs:/certs:ro`, and set `AXIS_J1_CA_BUNDLE=/certs/camera-ca.pem` in `.env` for each applicable camera. Camera requests go directly to the cameras and do not use ambient HTTP proxies. No inbound container ports are needed.

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
