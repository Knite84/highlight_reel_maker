FROM python:3.13-slim-trixie
COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /uvx /usr/local/bin/
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libimage-exiftool-perl fonts-dejavu-core libvulkan1 mesa-vulkan-drivers \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --group ml
COPY backend/app ./app
COPY backend/tests ./tests
COPY backend/tools ./tools
RUN find app tests tools -type f -exec chmod 644 {} +
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "10"]
