FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       tesseract-ocr \
       tesseract-ocr-eng \
       tesseract-ocr-fra \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 65532 quarantine \
    && useradd --uid 65532 --gid 65532 --create-home --shell /usr/sbin/nologin quarantine \
    && mkdir -p /worker/app/downloads

WORKDIR /worker

COPY quarantine/requirements.txt /worker/requirements.txt
RUN pip install --no-cache-dir -r /worker/requirements.txt

COPY quarantine/app_init.py /worker/app/__init__.py
COPY quarantine/downloads_init.py /worker/app/downloads/__init__.py
COPY app/downloads/models.py /worker/app/downloads/models.py
COPY app/downloads/stages.py /worker/app/downloads/stages.py
COPY app/downloads/detector.py /worker/app/downloads/detector.py
COPY app/downloads/extract.py /worker/app/downloads/extract.py
COPY app/downloads/image_processing.py /worker/app/downloads/image_processing.py
COPY app/downloads/evidence.py /worker/app/downloads/evidence.py
COPY app/downloads/security.py /worker/app/downloads/security.py
COPY quarantine/worker_entry.py /worker/worker_entry.py

RUN chown -R 65532:65532 /worker
USER 65532:65532

ENTRYPOINT ["python", "/worker/worker_entry.py"]
