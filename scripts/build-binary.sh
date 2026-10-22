#!/bin/sh
# Run from any directory on the target OS/architecture, e.g. the Raspberry Pi.
set -eu
cd "$(dirname "$0")/.."
python3 -m venv .venv
.venv/bin/python -m pip install --disable-pip-version-check -e '.[build]'
.venv/bin/python -m PyInstaller --clean --noconfirm --onefile --name axispy axispy.py
printf '%s\n' 'Executable: dist/axispy. Place your .env beside it, then run ./axispy.'
