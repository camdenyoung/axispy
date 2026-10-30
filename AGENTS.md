# axispyPTZ: workflow and chat handoff

This file is for Codex and other contributors resuming work without the previous
chat. Read it before changing the repository. Keep the current-state section up
to date when work is released, paused, or changes direction. Git and the user's
latest instructions take precedence over a historical snapshot in this file.

## Current state — 30 October 2026

- Repository: `camdenyoung/axispy`. The product/dashboard name is **axispyPTZ**;
  the Python package and CLI are `axispy`.
- Latest approved release: **v0.2.13**, merged and pushed to `main` at
  `c9f76f1` (the release commit; later documentation commits may follow).
- **Do not merge `feature-preview-reconnect` yet.** The user explicitly wants
  more time to test it. Its current commit is `f45a344`; it is committed and
  pushed, but has no release tag. Further work needs to preserve this decision.
- This handoff is a documentation-only addition to `main`. It does not approve
  or include the preview experiment, and does not need a version bump or tag.

### Paused preview experiment

On v0.2.13, low-quality MJPEG previews worked locally, but over Tailscale they
froze silently after roughly 20–30 seconds. Toggling previews off/on restored
streams briefly. `feature-preview-reconnect` changes the browser from native
MJPEG `<img>` streaming to `fetch` with multipart frame parsing and JPEG blob
URLs. It detects 15 seconds without a complete frame, displays a reconnecting
message, and retries with a 3–15 second backoff. It cancels streams and retries
when video is hidden, the tab is hidden, or a camera is offline. It retains only
the newest pending frame while decoding. The relay's browser-write timeout is
increased from 5 to 15 seconds; upstream camera-read limits remain unchanged.

The user reports streams now stay live over Tailscale without visible stalls
or reconnect messages. **Latency has not been measured against the old version.**
Both versions can accumulate network buffering. The new JavaScript processing
can add delay/client CPU work, and there is no guarantee of a maximum preview
latency. Receiving frames continuously can keep the stall watchdog satisfied
even if those frames are delayed. Do not claim that it cannot drift or that
Tailscale bandwidth was proved to be the cause. The user's current concern is
local-network latency and possible delay buildup; leave time for hardware tests.

Before this pause, validation passed: 321 pytest tests, 84 responsive layout
combinations, and Chromium recovery checks (multipart boundaries split across
chunks, silent stalls, EOF, HTTP failures, backoff, toggle/tab cancellation,
offline recovery, and a real browser → Python relay → simulated camera reconnect).
These are historical results, not evidence that a new environment was tested.
The experiment's additional scripts are `tests/browser_preview_reconnect.py`
and `tests/browser_preview_support.py`; they are not on released main.

## Working with the user

1. **Create a new branch before changing application code.** Use hyphenated
   names such as `feature-optional-cameras`; the user specifically rejected
   slash-separated names such as `feature/optional-cameras`.
2. Make the change, run appropriate checks, **commit and push the branch** so
   the user can test it on the real Pi. A local uncommitted change is not enough.
   State the branch name and provide copyable terminal commands when useful.
3. The user validates real cameras, USB control, and remote access. Wait for
   their explicit merge/release instruction before merging feature changes.
4. When they say “merge and tag as vX.Y.Z”, complete the version bump, checks,
   commit, merge, annotated tag, and push. Keep the dashboard's version display
   in sync. Do not stop after preparing a local merge or tag.
5. Do not merge/tag the paused preview work merely because a new chat starts.
   Do not reapply old releases or publish a cloud environment unless requested.
6. Preserve `.env`, ignored `config.toml`, logs, credentials, and unrelated work.
   Never print secrets or commit real camera credentials. Use examples for docs.
7. Keep the embedded HTML/CSS/JavaScript human-readable. The user explicitly
   requested readable formatting and released that cleanup as v0.2.10.

The user prefers direct action and concise explanations, with clear limits on
what was actually tested. Do not repeatedly ask permission for already
requested work. Explain technical commands plainly; shell commands and Python
heredocs are pasted into a terminal, not entered into a Python script verbatim.

## Project and deployment facts

- Target deployment: Docker Compose on a **Linux Raspberry Pi**, with an AXIS
  T8311 USB joystick and up to four Axis VAPIX PTZ cameras. Linux x86-64 is also
  intended to work when built for that machine; Linux is the required platform.
- Production camera connections use **HTTP for now**, by the user's choice.
  HTTPS works with recent refinements but needs more real-world testing. Never
  silently change their URLs to HTTPS or regress the working HTTP control path.
- Only one controller instance should control the joystick/cameras at a time.
  Cloud tests use simulated cameras; they do not establish real USB/PTZ behavior.
- `AXIS_Jn_ENABLED=false` disables a camera slot. Preserve physical J1–J4 slot
  mappings even when only nonconsecutive slots are enabled. At least one is needed.
- TLS verification is on by default; `AXIS_Jn_VERIFY_TLS=false` explicitly opts
  out per camera, including control, health probes, and previews. Badges show
  HTTP/HTTPS/unverified HTTPS, addresses link to each camera's web UI, and camera
  channel numbers are intentionally omitted from cards (still used by VAPIX).
- HTTPS-only control workers keep per-camera Digest/TLS sessions warm using
  read-only position queries. HTTP retains its direct control path. Do not
  remove safety stops, neutral/release requirements, or replay movement on retry.
- Dashboard previews are relayed through the host, not fetched directly from
  camera URLs by the browser. Each viewer/camera opens its own upstream stream;
  there is no shared fan-out. More viewers increase camera/host/network traffic.
- Preview defaults: 25 fps, low 640×360; medium is 1280×720, high is 1920×1080.
  `.env` keys: `AXIS_WEB_PREVIEW_FPS`, `AXIS_WEB_PREVIEW_QUALITY`. All are 16:9.
- CPU/memory metrics describe the **host**. Network in/out Mbps measure the
  process's network namespace: the container in normal Compose bridge mode,
  the host in host-network/native mode. They are not application-only counters.
- System usage is hidden by default. Usage, preview, and debug visibility are
  remembered with **localStorage**, not cookies, per browser/origin.
- Responsive layouts differ for 1/2 vs 3/4 cameras. Test all four counts; avoid
  undoing the carefully tuned 600, 1280, and 1440 px layout rules without review.
  Header usage visibility must not shift the camera cards.
- v0.2.13 uses local timestamps with UTC offsets in file logs and dashboard
  data/logs. Compose mounts the host's `/etc/localtime` read-only, including DST.
  Native runs use OS timezone rules. Console messages themselves have no added
  timestamp; Docker may separately provide timestamps when requested.

## Files to know

- `axispy.py`: configuration, joystick/controller, VAPIX/Digest requests, HTTPS
  workers, file logging, read-only diagnostic CLI.
- `axispy_web.py`: threaded HTTP dashboard, independent health probes, MJPEG
  relay, live-log buffer, host/network usage sampling.
- `axispy_ui.py`: formatted HTML/CSS/JavaScript in the raw `HTML` Python string.
- `.env.example`, `config.example.toml`: public configuration references.
- `compose.yaml`, `Dockerfile`: Linux/Pi deployment, USB/log/timezone mounts.
- `README.md`, `DEPLOYMENT.md`, `WEB_DASHBOARD.md`: configuration, deployment,
  troubleshooting, UI behavior. Consult them before guessing option names.
- `tests/test_*.py`: pytest suite. `tests/browser_*.py`: optional Playwright
  checks, run explicitly; they are not automatically collected by pytest.

## Starting or recovering a cloud environment

A previous chat repeatedly lost execution access; starting a new chat attached
to the repository sometimes restored it. Do not assume a prior chat's process
IDs, container, virtual environment, credentials, or filesystem still exist.

1. Confirm actual shell access, locate the checkout (previously
   `/workspace/axispy`), and inspect `git status --short --branch`, branches,
   recent commits, and tags before edits. Do not reset or overwrite dirty work.
2. If the managed cloud runtime skill/tools are available, read the runtime
   skill and check `environment_status`. If an environment is starting, wait
   using the supported tool. A connected status is not proof shell commands work.
3. Read the current network policy before network operations; retain the
   injected proxy and CA trust. Do not disable TLS verification for downloads
   or Git to work around access failures. Camera-specific TLS opt-outs are a
   separate explicit application feature.
4. Reuse `.venv` if working; otherwise create one with Python 3.11+ and install
   the project and test dependencies:

   ```sh
   python3 -m venv .venv
   .venv/bin/python -m pip install -e . pytest
   # For optional browser checks:
   .venv/bin/python -m pip install playwright
   # Use existing system Chromium, or install Chromium through Playwright.
   ```

5. Run `.venv/bin/python -m pytest -q`. Tests host simulated HTTP/HTTPS servers
   on localhost. `PermissionError: Operation not permitted` creating sockets
   can mean the execution sandbox blocked them; use the platform's supported
   approval/execution mechanism, not application changes to hide the failure.
6. Browser checks use Playwright with system `chromium`/`chromium-browser` when
   present. Run relevant scripts explicitly, e.g.:

   ```sh
   .venv/bin/python tests/browser_dashboard_layout.py
   .venv/bin/python tests/browser_dashboard_preferences.py
   .venv/bin/python tests/browser_camera_connections.py
   .venv/bin/python tests/browser_network_usage.py
   # On feature-preview-reconnect:
   .venv/bin/python tests/browser_preview_reconnect.py
   ```

7. If execution remains unavailable, report precisely what is blocked. You can
   provide manual steps, but do not claim to have committed, tested, merged, or
   pushed without confirming it. Do not reuse stale session IDs from this file.

Cloud environment publication and Git publication are different operations.
Do not publish/reconfigure an environment as part of an ordinary Git release.
For Docker builds in the managed environment, follow its runtime skill's proxy
and CA guidance; the Dockerfile supports an optional build-only `build_ca` secret.

## Release checklist (only when requested)

- Fetch `origin` main and tags; inspect dirty state and existing tags. Never
  overwrite an existing release tag or force-push over unrelated work.
- Bump the version in **all five**: `pyproject.toml`, `axispy_ui.py`,
  `axispy_web.py`, `README.md`, `tests/test_web.py`. Search for stale versions.
- Run `git diff --check`, pytest, and relevant browser checks. Commit the bump
  on the approved feature branch.
- Switch to main, update it with `git merge --ff-only origin/main`, then merge
  the approved branch with `--no-ff`. Resolve genuine conflicts deliberately.
- Create an annotated `vX.Y.Z` tag on the resulting main commit. Push main,
  the feature branch, and the tag (prefer `git push --atomic origin main
  BRANCH refs/tags/vX.Y.Z`). Verify the push, tag target, and clean working tree.
- Report release/version/checks and Pi update commands. Do not delete branches
  or create a GitHub Release unless asked. Update this handoff's current state.

## Commands for the user's Pi

Run from their existing repository/Compose directory. `origin` is Git's local
name for the GitHub remote. A branch switch changes that checkout's code;
rebuilding applies it to the running container.

```sh
# Test a branch (replace BRANCH with its exact name):
git fetch origin
git switch BRANCH
git pull --ff-only
docker compose up -d --build

# Return to released main:
git fetch origin --tags
git switch main
git pull --ff-only origin main
docker compose up -d --build

# Apply .env-only changes reliably (restart alone does not reload container env):
docker compose up -d --force-recreate
```

Refresh the browser after UI updates. Keep the real `.env` in place. If manually
committing on the Pi, Git needs user.name/user.email configured. GitHub HTTPS
pushes require supported token/credential-helper authentication (or SSH), not
an account password. Never ask the user to paste tokens into chat.
