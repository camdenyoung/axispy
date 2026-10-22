FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYGAME_HIDE_SUPPORT_PROMPT=1 SDL_VIDEODRIVER=dummy
RUN apt-get update \
    && apt-get install -y --no-install-recommends libsdl2-2.0-0 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml axispy.py ./
# Optional build-only CA for networks with a TLS inspection proxy.
RUN --mount=type=secret,id=build_ca \
    if [ -f /run/secrets/build_ca ]; then export PIP_CERT=/run/secrets/build_ca; fi; \
    python -m pip install --no-cache-dir .
ENTRYPOINT ["axispy"]
