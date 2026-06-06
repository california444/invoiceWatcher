FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1
WORKDIR /app

# Systemabhängigkeiten für PyMuPDF
RUN apt-get update \
    && apt-get install -y --no-install-recommends libmupdf-dev \
    && rm -rf /var/lib/apt/lists/*

# Abhängigkeiten zuerst kopieren (Layer-Caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Quellcode kopieren (.env wird bewusst nicht kopiert)
COPY *.py .

# Nicht-Root-Benutzer
RUN addgroup --system app && adduser --system --ingroup app app
USER app

ENV IMAP_HOST=${IMAP_HOST} \
    IMAP_PORT=${IMAP_PORT} \
    IMAP_USER=${IMAP_USER} \
    IMAP_PASSWORD=${IMAP_PASSWORD} \
    IMAP_MAILBOX=${IMAP_MAILBOX} \
    SMTP_HOST=${SMTP_HOST} \
    SMTP_PORT=${SMTP_PORT} \
    SMTP_USER=${SMTP_USER} \
    SMTP_PASSWORD=${SMTP_PASSWORD} \
    LLM_API_BASE_URL=${LLM_API_BASE_URL} \
    LLM_API_KEY=${LLM_API_KEY} \
    LLM_MODEL=${LLM_MODEL} \
    LLM_MAX_PAGES=${LLM_MAX_PAGES}

ENTRYPOINT ["python", "invoice_watcher.py"]
