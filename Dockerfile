FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libssl3 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./
COPY scripts ./scripts
COPY client_secret_*.json ./
COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh

RUN mkdir -p videos

CMD ["./entrypoint.sh"]
