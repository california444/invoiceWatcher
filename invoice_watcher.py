"""
invoice_watcher.py – IMAP-IDLE-basierter Rechnungsmonitor

Workflow:
  1. Verbindet sich per IMAP IDLE mit dem Posteingang.
  2. Bei neuen E-Mails: PDF- und XML-Anhänge extrahieren.
  3. Strukturierte Rechnung parsen (ZUGFeRD/Factur-X oder XRechnung).
  4. Fallback: LLM (OpenAI-kompatible API) analysiert PDF-Seiten als Bilder.
  5. Aus den Zahlungsdaten einen GiroCode/EPC-QR-Code erzeugen.
  6. QR-Code per E-Mail an die konfigurierte Adresse senden.

Starten:
  cp .env.example .env   # Zugangsdaten eintragen
  pip install -r requirements.txt
  python invoice_watcher.py
"""
import email
import logging
import os
import ssl
import time
from email.message import Message
from typing import Optional

import certifi
from dotenv import load_dotenv
from imapclient import IMAPClient

from epc_qr import PaymentData, generate_epc_qr
from invoice_parser import parse_pdf, parse_xml_attachment
from llm_extractor import extract_from_pdf
from mailer import send_girocode

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Konfiguration aus .env
# ---------------------------------------------------------------------------
load_dotenv()

IMAP_HOST = os.environ["IMAP_HOST"]
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
IMAP_USER = os.environ["IMAP_USER"]
IMAP_PASSWORD = os.environ["IMAP_PASSWORD"]
IMAP_MAILBOX = os.getenv("IMAP_MAILBOX", "INBOX")

SMTP_HOST = os.environ["SMTP_HOST"]
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.environ["SMTP_USER"]
SMTP_PASSWORD = os.environ["SMTP_PASSWORD"]

LLM_API_BASE_URL = os.getenv("LLM_API_BASE_URL", "https://openrouter.ai/api/v1")
LLM_API_KEY = os.environ["LLM_API_KEY"]
LLM_MODEL = os.getenv("LLM_MODEL", "qwen/qwen3-vl-8b-instruct")
LLM_MAX_PAGES = int(os.getenv("LLM_MAX_PAGES", "3"))  # 0 = alle Seiten

# IDLE wird alle 28 Minuten erneuert (Server-Timeout liegt meist bei 30 Min.)
_IDLE_REFRESH_SECS = 28 * 60
# Wartezeit bei Verbindungsfehlern vor erneutem Verbindungsversuch
_RECONNECT_DELAY_SECS = 30


# ---------------------------------------------------------------------------
# IMAP IDLE Loop
# ---------------------------------------------------------------------------

def run() -> None:
    """Hauptschleife: verbindet sich mit IMAP und läuft bis KeyboardInterrupt."""
    logger.info("Invoice Watcher gestartet. Überwache %s auf %s", IMAP_MAILBOX, IMAP_HOST)
    while True:
        try:
            _idle_session()
        except KeyboardInterrupt:
            logger.info("Invoice Watcher beendet.")
            break
        except Exception as exc:
            logger.error(
                "Verbindung unterbrochen: %s – erneuter Versuch in %d s",
                exc,
                _RECONNECT_DELAY_SECS,
            )
            time.sleep(_RECONNECT_DELAY_SECS)


def _idle_session() -> None:
    """Führt eine IMAP-Sitzung mit IDLE-Loop durch."""
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    with IMAPClient(IMAP_HOST, port=IMAP_PORT, ssl=True, ssl_context=ssl_context) as client:
        client.login(IMAP_USER, IMAP_PASSWORD)
        client.select_folder(IMAP_MAILBOX, readonly=False)
        logger.info("IMAP-Verbindung hergestellt.")

        # Beim Start alle bereits ungelesenen Nachrichten verarbeiten
        processed_uids: set[int] = set()
        _process_unseen(client, processed_uids)

        idle_start = time.monotonic()
        client.idle()
        logger.info("IDLE aktiv – warte auf neue Nachrichten…")

        while True:
            # Wie lange noch bis zum nächsten IDLE-Refresh?
            elapsed = time.monotonic() - idle_start
            timeout = max(5, _IDLE_REFRESH_SECS - int(elapsed))

            responses = client.idle_check(timeout=timeout)

            # Neue Nachrichten vorhanden?
            has_new = any(
                isinstance(r, tuple) and len(r) >= 2 and r[1] == b"EXISTS"
                for r in responses
            )

            if has_new:
                client.idle_done()
                _process_unseen(client, processed_uids)
                idle_start = time.monotonic()
                client.idle()
            elif time.monotonic() - idle_start >= _IDLE_REFRESH_SECS:
                # IDLE erneuern, um Server-Timeout zu vermeiden
                client.idle_done()
                logger.debug("IDLE erneuert.")
                idle_start = time.monotonic()
                client.idle()


def _process_unseen(client: IMAPClient, processed_uids: set[int]) -> None:
    """Holt alle UNSEEN-Nachrichten und verarbeitet noch nicht behandelte."""
    uids = client.search(["UNSEEN"])
    new_uids = [u for u in uids if u not in processed_uids]
    if not new_uids:
        return

    logger.info("%d neue ungelesene Nachricht(en) gefunden.", len(new_uids))
    messages = client.fetch(new_uids, ["RFC822"])
    for uid, data in messages.items():
        raw: Optional[bytes] = data.get(b"RFC822")
        if not raw:
            continue
        processed_uids.add(uid)
        try:
            _handle_message(uid, raw)
        except Exception as exc:
            logger.error("Fehler bei Verarbeitung von UID %d: %s", uid, exc, exc_info=True)


# ---------------------------------------------------------------------------
# Nachrichtenverarbeitung
# ---------------------------------------------------------------------------

def _handle_message(uid: int, raw: bytes) -> None:
    """Verarbeitet eine einzelne E-Mail-Nachricht."""
    msg = email.message_from_bytes(raw)
    subject = msg.get("Subject", "(kein Betreff)")
    sender = msg.get("From", "?")
    message_id = msg.get("Message-ID", "")
    recipient = msg.get("To", "")
    if not recipient:
        logger.warning("UID %d: kein To-Header – übersprungen.", uid)
        return
    logger.info("Verarbeite UID %d von %s: %s", uid, sender, subject)

    pdfs, xmls = _extract_attachments(msg)

    if not pdfs and not xmls:
        logger.info("UID %d: keine PDF/XML-Anhänge – übersprungen.", uid)
        return

    payment_data, source = _extract_payment_data(pdfs, xmls)

    if not payment_data:
        logger.info("UID %d: keine Zahlungsdaten extrahierbar – übersprungen.", uid)
        return

    if not payment_data.get("iban"):
        logger.warning("UID %d: IBAN fehlt – übersprungen.", uid)
        return

    logger.info(
        "UID %d: Zahlungsdaten gefunden (Quelle: %s)\n"
        "  Empfänger:        %s\n"
        "  IBAN:             %s\n"
        "  BIC:              %s\n"
        "  Betrag:           EUR %.2f\n"
        "  Verwendungszweck: %s",
        uid,
        source,
        payment_data.get("name") or "-",
        payment_data.get("iban") or "-",
        payment_data.get("bic") or "-",
        float(payment_data.get("amount") or 0),
        payment_data.get("reference") or "-",
    )

    pd = PaymentData(
        name=payment_data.get("name") or "",
        iban=payment_data["iban"],
        bic=payment_data.get("bic") or "",
        amount=float(payment_data.get("amount") or 0),
        reference=payment_data.get("reference") or "",
    )
    qr_png = generate_epc_qr(pd)

    send_girocode(
        smtp_host=SMTP_HOST,
        smtp_port=SMTP_PORT,
        smtp_user=SMTP_USER,
        smtp_password=SMTP_PASSWORD,
        recipient=recipient,
        subject=subject,
        payment_data=payment_data,
        qr_png=qr_png,
        source=source,
        reply_to_message_id=message_id,
    )
    logger.info("UID %d: GiroCode erfolgreich gesendet.", uid)


def _extract_payment_data(
    pdfs: list[bytes],
    xmls: list[bytes],
) -> tuple[Optional[dict], str]:
    """
    Extrahiert Zahlungsdaten aus PDFs/XMLs.
    Gibt (payment_data, source_name) zurück.
    Reihenfolge: XRechnung-XML → ZUGFeRD/Factur-X → LLM-Fallback.
    """
    # 1. XRechnung als standalone XML-Anhang
    for xml_bytes in xmls:
        data = parse_xml_attachment(xml_bytes)
        if data:
            return data, "XRechnung"

    # 2. ZUGFeRD / Factur-X (XML eingebettet in PDF)
    for pdf_bytes in pdfs:
        data = parse_pdf(pdf_bytes)
        if data:
            return data, "ZUGFeRD/Factur-X"

    # 3. LLM-Fallback via API
    if pdfs:
        logger.info("Kein strukturiertes Format gefunden – starte LLM-Analyse…")
        for pdf_bytes in pdfs:
            data = extract_from_pdf(pdf_bytes, LLM_API_BASE_URL, LLM_API_KEY, LLM_MODEL, LLM_MAX_PAGES)
            if data:
                return data, f"LLM ({LLM_MODEL})"

    return None, ""


def _extract_attachments(msg: Message) -> tuple[list[bytes], list[bytes]]:
    """
    Extrahiert PDF- und XML-Anhänge aus einer E-Mail-Nachricht.
    Gibt (pdf_list, xml_list) zurück.
    """
    pdfs: list[bytes] = []
    xmls: list[bytes] = []

    for part in msg.walk():
        content_type = part.get_content_type()
        disposition = part.get("Content-Disposition", "")
        filename = (part.get_filename() or "").lower()

        payload = part.get_payload(decode=True)
        if not payload:
            continue

        is_pdf = content_type == "application/pdf" or filename.endswith(".pdf")
        is_xml = (
            content_type in ("application/xml", "text/xml")
            or filename.endswith(".xml")
        ) and "attachment" in disposition

        if is_pdf:
            pdfs.append(payload)
        elif is_xml:
            xmls.append(payload)

    return pdfs, xmls


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    run()
