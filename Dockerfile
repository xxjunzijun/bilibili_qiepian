FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir streamlink biliup

COPY app ./app
COPY static ./static
COPY .env.example ./.env.example

ENV APP_HOST=0.0.0.0
ENV APP_PORT=8787
ENV APP_DATA_DIR=/app/data
ENV APP_RECORDINGS_DIR=/app/recordings

EXPOSE 8787

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${APP_PORT:-8787}"]
