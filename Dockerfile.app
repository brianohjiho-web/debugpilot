# The web app, the agent and the hidden browser used by "Scan a website". User code from the debugger runs in the separate runner container.
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt .
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN pip install --no-cache-dir -r requirements.txt && python -m playwright install --with-deps chromium
COPY server.py ./
COPY agent ./agent
COPY runner ./runner
COPY web ./web
COPY watcher ./watcher
COPY demo-site ./demo-site
COPY demo-booking ./demo-booking
COPY demo-power ./demo-power
RUN useradd --create-home app && mkdir -p /app/data && chown -R app /app/data /app/demo-site
USER app
EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
