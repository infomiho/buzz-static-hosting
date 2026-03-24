FROM node:22-slim AS css-builder

WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci
COPY src/server/static/input.css src/server/static/input.css
COPY src/server/templates/ src/server/templates/
RUN npx @tailwindcss/cli -i src/server/static/input.css -o src/server/static/style.css --minify

FROM python:3.12-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy project files
COPY pyproject.toml uv.lock ./
COPY src/ ./src/

# Copy built CSS from builder
COPY --from=css-builder /build/src/server/static/style.css ./src/server/static/style.css

# Install dependencies
RUN uv sync --frozen --no-dev

RUN mkdir -p /data/sites

VOLUME /data

EXPOSE 8080

CMD ["uv", "run", "python", "-m", "server", "--port", "8080"]
