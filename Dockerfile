FROM python:3.11-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv /uv /usr/local/bin/uv

# Install system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    wget \
    gnupg \
    unzip \
    libglib2.0-0 \
    libnss3 \
    libgconf-2-4 \
    libfontconfig1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies using uv and lockfile
COPY pyproject.toml uv.lock ./

# Install into a location outside /app to avoid volume mount shadowing
ENV UV_PROJECT_ENVIRONMENT="/venv"
RUN uv sync --frozen --no-install-project

COPY . .

# Enable the virtual environment
ENV PATH="/venv/bin:"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN chmod +x docker-entrypoint.sh

ENTRYPOINT ["./docker-entrypoint.sh"]
