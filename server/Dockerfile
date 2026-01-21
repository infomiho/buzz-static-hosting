FROM python:3.12-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy project files
COPY pyproject.toml uv.lock ./
COPY src/ ./src/

# Install dependencies
RUN uv sync --frozen --no-dev

RUN mkdir -p /data/sites

VOLUME /data

EXPOSE 8080

CMD ["uv", "run", "python", "-m", "server", "--port", "8080"]
