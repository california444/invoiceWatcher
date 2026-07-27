FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1
WORKDIR /app

# Systemabhängigkeiten für PyMuPDF und git (ändert sich praktisch nie)
RUN apt-get update \
    && apt-get install -y --no-install-recommends libmupdf-dev git \
    && rm -rf /var/lib/apt/lists/*

ARG GIT_REPO=https://github.com/california444/invoiceWatcher.git
ARG GIT_REF=main

# Nur requirements.txt holen, damit die pip-install-Schicht unabhängig vom
# restlichen Quellcode im Cache bleibt (ändert sich selten).
RUN git clone --depth=1 --branch ${GIT_REF} ${GIT_REPO} /tmp/src \
    && cp /tmp/src/requirements.txt . \
    && rm -rf /tmp/src

# Abhängigkeiten installieren – Cache-Hit, solange requirements.txt gleich bleibt
RUN pip install --no-cache-dir -r requirements.txt

# Aktuellen Quellcode klonen. CACHEBUST erzwingt bei Bedarf einen frischen
# Klon, ohne die teuren Layer oben (apt-get/pip) erneut auszuführen:
#   docker build --build-arg CACHEBUST=$(date +%s) .
ARG CACHEBUST=0
RUN git clone --depth=1 --branch ${GIT_REF} ${GIT_REPO} /tmp/src \
    && cp -a /tmp/src/. . \
    && rm -rf /tmp/src

# Nicht-Root-Benutzer
RUN addgroup --system app && adduser --system --ingroup app app
USER app

ENTRYPOINT ["python", "invoice_watcher.py"]
