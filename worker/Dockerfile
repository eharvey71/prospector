# Playwright's official image ships Chromium + all system deps.
FROM mcr.microsoft.com/playwright/python:v1.61.0-noble

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8080
# Single worker per container: one browser at a time, scale via Cloud Run
# instances (concurrency=1 in the deploy command), not threads.
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT} --workers 1
