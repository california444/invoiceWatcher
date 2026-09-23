"""
invoice_watcher.py – IMAP-IDLE-basierter Rechnungsmonitor

Workflow:
  1. Verbindet sich per IMAP IDLE mit dem Posteingang.
  2. Bei neuen E-Mails: PDF- und XML-Anhänge extrahieren.
  3. Strukturierte Rechnung parsen (ZUGFeRD/Factur-X oder XRechnung).
  4. Fallback: LLM (OpenAI-kompatible API) analysiert PDF-Seiten als Bilder.
  5. Aus den Zahlungsdaten einen GiroCode/EPC-QR-Code erzeugen.
  6. QR-Code per E-Mail an den Absender der Rechnungsmail zurücksenden.

Mehrere IMAP-Konten:
  Nummerierte Env-Vars IMAP_HOST_0, IMAP_HOST_1, … definieren mehrere Konten.
  Jedes Konto wird in einem eigenen Thread überwacht.
  Fallback: IMAP_HOST (ohne Suffix) für ein einzelnes Konto.

Starten:
  cp .env.example .env   # Zugangsdaten eintragen
  pip install -r requirements.txt
  python invoice_watcher.py
"""
import email
import logging
import os
import ssl
import threading
import time
from dataclasses import dataclass
from email.message import Message
from email.utils import getaddresses, parseaddr
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

SMTP_HOST = os.environ["SMTP_HOST"]
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.environ["SMTP_USER"]
SMTP_PASSWORD = os.environ["SMTP_PASSWORD"]
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER)

LLM_API_BASE_URL = os.getenv("LLM_API_BASE_URL", "https://openrouter.ai/api/v1")
LLM_API_KEY = os.environ["LLM_API_KEY"]
LLM_MODEL = os.getenv("LLM_MODEL", "qwen/qwen3-vl-8b-instruct")
LLM_MAX_PAGES = int(os.getenv("LLM_MAX_PAGES", "3"))  # 0 = alle Seiten

# IDLE wird alle 28 Minuten erneuert (Server-Timeout liegt meist bei 30 Min.)
_IDLE_REFRESH_SECS = 28 * 60
# Wartezeit bei Verbindungsfehlern vor erneutem Verbindungsversuch
_RECONNECT_DELAY_SECS = 30


# ---------------------------------------------------------------------------
# IMAP-Konto-Konfiguration
# ---------------------------------------------------------------------------

@dataclass
class ImapAccount:
    host: str
    port: int
    user: str
    password: str
    mailbox: str
    target_recipient: Optional[str] = None


def load_accounts() -> list[ImapAccount]:
    """
    Lädt IMAP-Konten aus nummerierten Env-Vars (IMAP_HOST_0, IMAP_HOST_1, …).
    Fallback: IMAP_HOST (ohne Suffix) für ein einzelnes Konto.
    """
    accounts: list[ImapAccount] = []
    index = 0
    while True:
        host = os.getenv(f"IMAP_HOST_{index}")
        if not host:
            break
        accounts.append(ImapAccount(
            host=host,
            port=int(os.getenv(f"IMAP_PORT_{index}", "993")),
            user=os.environ[f"IMAP_USER_{index}"],
            password=os.environ[f"IMAP_PASSWORD_{index}"],
            mailbox=os.getenv(f"IMAP_MAILBOX_{index}", "INBOX"),
            target_recipient=os.getenv(f"IMAP_TARGET_RECIPIENT_{index}"),
        ))
        index += 1

    # Fallback auf einzelnes Konto ohne Suffix
    if not accounts and os.getenv("IMAP_HOST"):
        accounts.append(ImapAccount(
            host=os.environ["IMAP_HOST"],
            port=int(os.getenv("IMAP_PORT", "993")),
            user=os.environ["IMAP_USER"],
            password=os.environ["IMAP_PASSWORD"],
            mailbox=os.getenv("IMAP_MAILBOX", "INBOX"),
            target_recipient=os.getenv("IMAP_TARGET_RECIPIENT"),
        ))

    if not accounts:
        raise RuntimeError(
            "Keine IMAP-Konten konfiguriert. "
            "Bitte IMAP_HOST_0 / IMAP_USER_0 / … oder IMAP_HOST / IMAP_USER / … setzen."
        )

    return accounts


# ---------------------------------------------------------------------------
# IMAP IDLE Loop
# ---------------------------------------------------------------------------

def run(account: ImapAccount) -> None:
    """Hauptschleife für ein Konto: verbindet sich und läuft bis KeyboardInterrupt."""
    logger.info("[%s] Invoice Watcher gestartet. Überwache %s auf %s",
                account.user, account.mailbox, account.host)
    while True:
        try:
            _idle_session(account)
        except KeyboardInterrupt:
            logger.info("[%s] Invoice Watcher beendet.", account.user)
            break
        except Exception as exc:
            logger.error(
                "[%s] Verbindung unterbrochen: %s – erneuter Versuch in %d s",
                account.user, exc, _RECONNECT_DELAY_SECS,
            )
            time.sleep(_RECONNECT_DELAY_SECS)


_ICLOUD_IDLE_ERRORS = (
    "Unexpected IDLE response",
    "Server replied with a response that violates the IMAP protocol",
)


def _is_icloud_idle_error(exc: Exception) -> bool:
    return any(msg in str(exc) for msg in _ICLOUD_IDLE_ERRORS)


def _idle_start(client: IMAPClient, account: ImapAccount) -> None:
    """Startet IDLE nach dem Leeren ausstehender Server-Antworten (iCloud-kompatibel)."""
    # NOOP lässt den Server alle gepufferten untagged Responses (z. B. FETCH FLAGS)
    # abschicken, sodass idle() danach sauber starten kann.
    try:
        client.noop()
    except Exception:
        pass
    client.idle()


def _idle_session(account: ImapAccount) -> None:
    """Führt eine IMAP-Sitzung mit IDLE-Loop durch."""
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    with IMAPClient(account.host, port=account.port, ssl=True, ssl_context=ssl_context) as client:
        client.login(account.user, account.password)
        client.select_folder(account.mailbox, readonly=False)
        logger.info("[%s] IMAP-Verbindung hergestellt.", account.user)

        # Beim Start alle bereits ungelesenen Nachrichten verarbeiten
        processed_uids: set[int] = set()
        _process_unseen(client, processed_uids, account)

        idle_start = time.monotonic()
        _idle_start(client, account)
        logger.info("[%s] IDLE aktiv – warte auf neue Nachrichten…", account.user)

        while True:
            # Wie lange noch bis zum nächsten IDLE-Refresh?
            elapsed = time.monotonic() - idle_start
            timeout = max(5, _IDLE_REFRESH_SECS - int(elapsed))

            try:
                responses = client.idle_check(timeout=timeout)
            except Exception as exc:
                # iCloud sendet während IDLE unaufgefordert FETCH-Responses
                # (z. B. Flag-Änderungen). imapclient wirft dafür eine Exception.
                # IDLE neu starten statt die Verbindung zu trennen.
                if _is_icloud_idle_error(exc):
                    logger.debug("[%s] Unerwartete IDLE-Antwort ignoriert: %s", account.user, exc)
                    client.idle_done()
                    idle_start = time.monotonic()
                    _idle_start(client, account)
                    continue
                raise

            # Neue Nachrichten vorhanden?
            has_new = any(
                isinstance(r, tuple) and len(r) >= 2 and r[1] == b"EXISTS"
                for r in responses
            )

            if has_new:
                client.idle_done()
                _process_unseen(client, processed_uids, account)
                idle_start = time.monotonic()
                _idle_start(client, account)
            elif time.monotonic() - idle_start >= _IDLE_REFRESH_SECS:
                # IDLE erneuern, um Server-Timeout zu vermeiden
                client.idle_done()
                logger.debug("[%s] IDLE erneuert.", account.user)
                idle_start = time.monotonic()
                _idle_start(client, account)


def _process_unseen(client: IMAPClient, processed_uids: set[int], account: ImapAccount) -> None:
    """Holt alle UNSEEN-Nachrichten und verarbeitet noch nicht behandelte."""
    uids = client.search(["UNSEEN"])
    new_uids = [u for u in uids if u not in processed_uids]
    if not new_uids:
        return

    logger.info("[%s] %d neue ungelesene Nachricht(en) gefunden.", account.user, len(new_uids))
    # BODY.PEEK[] statt RFC822: markiert Nachrichten nicht automatisch als \Seen,
    # sodass iCloud keine unaufgeforderten FLAGS-Responses schickt.
    messages = client.fetch(new_uids, ["BODY.PEEK[]"])
    for uid, data in messages.items():
        raw: Optional[bytes] = data.get(b"BODY[]")
        if not raw:
            logger.debug("[%s] UID %d: kein Nachrichteninhalt im Fetch-Ergebnis.", account.user, uid)
            continue
        processed_uids.add(uid)
        try:
            mark_seen = _handle_message(uid, raw, account)
        except Exception as exc:
            logger.error("[%s] Fehler bei Verarbeitung von UID %d: %s",
                         account.user, uid, exc, exc_info=True)
            continue
        if mark_seen:
            # Markiert die Nachricht dauerhaft (serverseitig) als bearbeitet, damit sie
            # nach einem Reconnect/Neustart nicht erneut verarbeitet wird (Duplikate).
            try:
                client.add_flags([uid], [b"\\Seen"])
            except Exception as exc:
                logger.warning("[%s] UID %d: konnte nicht als gelesen markiert werden: %s",
                                account.user, uid, exc)


# ---------------------------------------------------------------------------
# Nachrichtenverarbeitung
# ---------------------------------------------------------------------------

def _matches_target_recipient(msg: Message, target: str) -> bool:
    """Prüft, ob `target` unter den Empfängern (To/Cc) der Nachricht ist."""
    target = target.strip().lower()
    addresses = getaddresses(msg.get_all("To", []) + msg.get_all("Cc", []))
    return any(addr.strip().lower() == target for _, addr in addresses)


def _handle_message(uid: int, raw: bytes, account: ImapAccount) -> bool:
    """
    Verarbeitet eine einzelne E-Mail-Nachricht.

    Gibt zurück, ob die Nachricht als bearbeitet (\\Seen) markiert werden soll.
    Nur Nachrichten, die den konfigurierten Zieladressen-Filter passieren, werden
    markiert – so bleiben alle anderen Mails im Postfach unangetastet, während
    bereits behandelte Rechnungsmails nach einem Reconnect/Neustart nicht erneut
    verarbeitet werden.
    """
    msg = email.message_from_bytes(raw)
    subject = msg.get("Subject", "(kein Betreff)")
    sender = msg.get("From", "?")
    message_id = msg.get("Message-ID", "")

    if account.target_recipient and not _matches_target_recipient(msg, account.target_recipient):
        logger.debug(
            "[%s] UID %d: nicht an Zieladresse %s adressiert – übersprungen.",
            account.user, uid, account.target_recipient,
        )
        return False

    # Die GiroCode-Mail geht an den Absender (From-Header) der Rechnungsmail zurück.
    qr_recipient = parseaddr(msg.get("From", ""))[1]
    if not qr_recipient:
        logger.warning("[%s] UID %d: kein gültiger From-Header – übersprungen.", account.user, uid)
        return False

    logger.info("[%s] Verarbeite UID %d von %s: %s", account.user, uid, sender, subject)

    pdfs, xmls = _extract_attachments(msg)

    if not pdfs and not xmls:
        logger.info("[%s] UID %d: keine PDF/XML-Anhänge – übersprungen.", account.user, uid)
        return True

    payment_data, source = _extract_payment_data(pdfs, xmls)

    if not payment_data:
        logger.info("[%s] UID %d: keine Zahlungsdaten extrahierbar – übersprungen.",
                    account.user, uid)
        return True

    if not payment_data.get("iban"):
        logger.warning("[%s] UID %d: IBAN fehlt – übersprungen.", account.user, uid)
        return True

    logger.info(
        "[%s] UID %d: Zahlungsdaten gefunden (Quelle: %s)\n"
        "  Empfänger:        %s\n"
        "  IBAN:             %s\n"
        "  BIC:              %s\n"
        "  Betrag:           EUR %.2f\n"
        "  Verwendungszweck: %s",
        account.user, uid, source,
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
        smtp_from=SMTP_FROM,
        recipient=qr_recipient,
        subject=subject,
        payment_data=payment_data,
        qr_png=qr_png,
        source=source,
        reply_to_message_id=message_id,
    )
    logger.info("[%s] UID %d: GiroCode erfolgreich gesendet.", account.user, uid)
    return True


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
    accounts = load_accounts()
    logger.info("%d IMAP-Konto/Konten geladen.", len(accounts))

    if len(accounts) == 1:
        # Einzelnes Konto – direkt im Hauptthread ausführen
        run(accounts[0])
    else:
        # Mehrere Konten – je ein Daemon-Thread
        threads = [
            threading.Thread(target=run, args=(account,), daemon=True, name=account.user)
            for account in accounts
        ]
        for t in threads:
            t.start()

        stop = threading.Event()
        try:
            stop.wait()
        except KeyboardInterrupt:
            logger.info("Invoice Watcher beendet.")
