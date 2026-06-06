FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1
WORKDIR /app

# Systemabhängigkeiten für PyMuPDF und git
RUN apt-get update \
    && apt-get install -y --no-install-recommends libmupdf-dev git \
    && rm -rf /var/lib/apt/lists/*

# Quellcode aus Git klonen
ARG GIT_REPO=https://github.com/california444/invoiceWatcher.git
ARG GIT_REF=main
RUN git clone --depth=1 --branch ${GIT_REF} ${GIT_REPO} .

# Abhängigkeiten installieren
RUN pip install --no-cache-dir -r requirements.txt

# Nicht-Root-Benutzer
RUN addgroup --system app && adduser --system --ingroup app app
USER app

ENTRYPOINT ["python", "invoice_watcher.py"]
