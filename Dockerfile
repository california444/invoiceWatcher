# Basis-Image als ARG, damit es nur an einer Stelle steht und der Watcher
# es zur Laufzeit ausgeben kann.
ARG BASE_IMAGE=python:3.13-slim-trixie
FROM ${BASE_IMAGE}

ENV PYTHONUNBUFFERED=1
WORKDIR /app

# Systemabhängigkeiten für PyMuPDF (ändert sich praktisch nie)
RUN apt-get update \
    && apt-get install -y --no-install-recommends libmupdf-dev \
    && rm -rf /var/lib/apt/lists/*

# Erst nur requirements.txt kopieren, dann installieren: solange sich die
# Datei nicht ändert, trifft dieser Layer den Build-Cache, auch wenn am
# Quellcode geschraubt wurde.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Quellcode aus dem Build-Kontext statt per "git clone" zur Build-Zeit.
# Der Clone war über seine Kommandozeile cachebar und lieferte ohne
# CACHEBUST stillschweigend einen alten Stand; so entspricht das Image
# genau dem Commit, aus dem es gebaut wurde.
COPY *.py ./

# Nicht-Root-Benutzer
RUN addgroup --system app && adduser --system --ingroup app app

# Herkunft des Builds bewusst ganz am Ende: APP_REVISION ändert sich mit
# jedem Commit und würde weiter oben den pip-install-Layer jedes Mal
# invalidieren. ARGs von vor dem FROM sind hier nicht mehr sichtbar und
# müssen erneut deklariert werden.
ARG BASE_IMAGE
ARG APP_REVISION=""
ENV APP_BASE_IMAGE=${BASE_IMAGE} \
    APP_REVISION=${APP_REVISION}

USER app

ENTRYPOINT ["python", "invoice_watcher.py"]
