# VK Forma — Presentation Studio.
# Python 3.12+, Node.js для декодера сжатых
# EOT/MTX-шрифтов, LibreOffice Impress для экспорта PDF/PNG.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    STUDIO_DATA_DIR=/app/data \
    STUDIO_PORT=8765

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        nodejs \
        libreoffice-impress \
        fontconfig \
        fonts-dejavu-core \
        fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Сначала только зависимости — слой переиспользуется, пока lock не меняется.
COPY requirements.lock ./
RUN python -m pip install --no-cache-dir -r requirements.lock

COPY . .

# Непривилегированный пользователь; HOME нужен LibreOffice (office.py передаёт его в env процесса).
RUN useradd --create-home --uid 1000 studio \
    && mkdir -p /app/data \
    && chown -R studio:studio /app
USER studio
ENV HOME=/home/studio

EXPOSE 8765

# cli.py привязывает сервер к 127.0.0.1, поэтому в контейнере запускаем uvicorn
# напрямую на 0.0.0.0; create_app() — та же фабрика, что использует CLI.
CMD ["sh", "-c", "exec uvicorn studio.app:create_app --factory --host 0.0.0.0 --port \"${STUDIO_PORT:-8765}\""]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.getenv(\"STUDIO_PORT\",\"8765\")}/api/health', timeout=4)"]
