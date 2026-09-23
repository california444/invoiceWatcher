"""
mailer.py – SMTP-Versand des GiroCode-QR-Codes

Sendet eine E-Mail mit:
  - Textbody (extrahierte Zahlungsfelder zur Sichtkontrolle)
  - GiroCode als image/png-Anhang (girocode.png)
"""
import logging
import smtplib
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


def send_girocode(
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    recipient: str,
    subject: str,
    payment_data: dict,
    qr_png: bytes,
    source: str = "unbekannt",
    reply_to_message_id: str = "",
    smtp_from: str = "",
) -> None:
    """
    Sendet eine E-Mail mit dem GiroCode-QR-Bild an `recipient`.

    Parameters
    ----------
    smtp_host, smtp_port, smtp_user, smtp_password : SMTP-Verbindungsdaten
    recipient           : Absender der eingehenden Rechnungsmail
    subject             : Betreff der Original-Rechnung
    payment_data        : dict mit name, iban, bic, amount, reference
    qr_png              : PNG-Bytes des GiroCode-QR-Codes
    source              : Quelle der Daten ("ZUGFeRD/Factur-X", "XRechnung", "LLM")
    reply_to_message_id : Message-ID der Original-Mail fuer Thread-Gruppierung
    smtp_from           : Absenderadresse fuer den From-Header (Default: smtp_user)
    """
    sender = smtp_from or smtp_user

    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = subject
    if reply_to_message_id:
        msg["In-Reply-To"] = reply_to_message_id
        msg["References"] = reply_to_message_id

    name      = payment_data.get("name") or "-"
    iban      = payment_data.get("iban") or "-"
    bic       = payment_data.get("bic") or "-"
    amount    = payment_data.get("amount", 0)
    reference = payment_data.get("reference") or "-"

    body = (
        f"Empfänger: {name}\n"
        f"IBAN: {iban}\n"
        f"BIC: {bic}\n"
        f"Betrag: EUR {amount:.2f}\n"
        f"Verwendungszweck: {reference}\n"
        f"\n"
    )
    msg.attach(MIMEText(body, "plain", "utf-8"))

    img_part = MIMEImage(qr_png, _subtype="png")
    img_part.add_header("Content-Disposition", "attachment", filename="girocode.png")
    msg.attach(img_part)

    _send(smtp_host, smtp_port, smtp_user, smtp_password, sender, recipient, msg)
    logger.info("GiroCode-E-Mail gesendet an %s (Betreff: %s)", recipient, msg["Subject"])


def _send(
    host: str,
    port: int,
    user: str,
    password: str,
    sender: str,
    recipient: str,
    msg: MIMEMultipart,
) -> None:
    """Sendet die Nachricht via SMTP_SSL (Port 465) oder STARTTLS (alle anderen Ports)."""
    if port == 465:
        with smtplib.SMTP_SSL(host, port) as server:
            server.login(user, password)
            server.sendmail(sender, [recipient], msg.as_bytes())
    else:
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(user, password)
            server.sendmail(sender, [recipient], msg.as_bytes())
