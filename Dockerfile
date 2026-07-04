# Debian 12 (bookworm), not the default trixie: Playwright's install-deps
# references font packages (ttf-unifont, ttf-ubuntu-font-family) that were
# renamed on Debian 13, which breaks the browser install. bookworm is a
# Playwright-supported base and installs cleanly.
FROM python:3.11-slim-bookworm

WORKDIR /app

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install the Playwright browser + its system libraries (used for JS rendering
# and the TikTok fallback). Best-effort: if a future base-image change breaks
# the OS-deps step, the image still builds and only JS rendering degrades —
# every other endpoint works without a browser.
RUN playwright install --with-deps chromium \
    || playwright install chromium \
    || echo "WARNING: Playwright browser install failed — JS rendering will be unavailable."

# Copy app
COPY app/ ./app/
COPY sdk/ ./sdk/

# Install SDK in development mode
RUN pip install -e ./sdk/python/

EXPOSE 8000

# Honor $PORT when the platform injects one (Coolify, Railway, ...), else 8000.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
