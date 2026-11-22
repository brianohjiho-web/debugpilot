# The public website (Hugging Face Spaces). Only "Scan a site" is open; pasted code is switched off.
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    DEBUGPILOT_PUBLIC=1 \
    DEBUGPILOT_DATA=/data
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && python -m playwright install --with-deps chromium
COPY server.py ./
COPY agent ./agent
COPY runner ./runner
COPY web ./web
COPY watcher ./watcher
COPY demo-site ./demo-site
COPY demo-booking ./demo-booking
COPY demo-power ./demo-power
# Hugging Face runs the container as user 1000.
RUN useradd -m -u 1000 app && mkdir -p /data && chown -R 1000 /data /app/data /app/demo-site 2>/dev/null || true
USER 1000
EXPOSE 7860
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "7860", "--proxy-headers", "--forwarded-allow-ips", "*"]
