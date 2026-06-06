"""
epc_qr.py – GiroCode / EPC-QR-Code Generierung nach BCD-Standard
(European Payments Council – SEPA Credit Transfer QR Code)
"""
import io
from dataclasses import dataclass, field

import qrcode
import qrcode.constants


@dataclass
class PaymentData:
    """Zahlungsdaten für einen SEPA-Überweisungsträger."""

    name: str          # Empfänger (max. 70 Zeichen)
    iban: str          # IBAN (max. 34 Zeichen, ohne Leerzeichen)
    bic: str = ""      # BIC (optional seit SEPA 2016, max. 11 Zeichen)
    amount: float = 0.0   # Betrag in EUR (0 = kein Betrag vorausgefüllt)
    reference: str = ""   # Verwendungszweck / Remittance Info (max. 140 Zeichen)
    purpose: str = ""     # Zweckcode / Purpose Code (max. 4 Zeichen, optional)


def generate_epc_qr(data: PaymentData) -> bytes:
    """
    Erzeugt einen EPC-QR-Code (GiroCode) und gibt ihn als PNG-Bytes zurück.

    Format nach EPC069-12 v2.1:
      Zeile 1:  BCD            (Service Tag)
      Zeile 2:  002            (Version)
      Zeile 3:  1              (Zeichensatz: UTF-8)
      Zeile 4:  SCT            (Identification: SEPA Credit Transfer)
      Zeile 5:  BIC            (optional)
      Zeile 6:  Name           (Empfänger, max. 70 Zeichen)
      Zeile 7:  IBAN
      Zeile 8:  EURx.xx        (Betrag, leer = kein Betrag)
      Zeile 9:  Zweckcode      (optional, max. 4 Zeichen)
      Zeile 10: Strukturierte Referenz (leer, exklusiv mit Zeile 11)
      Zeile 11: Verwendungszweck unstrukturiert (max. 140 Zeichen)
      Zeile 12: Hinweis        (nicht übertragen, nur zur Anzeige)
    """
    amount_str = f"EUR{data.amount:.2f}" if data.amount else ""

    payload = "\n".join([
        "BCD",
        "002",
        "1",
        "SCT",
        data.bic.strip()[:11],
        data.name.strip()[:70],
        data.iban.replace(" ", "").upper()[:34],
        amount_str,
        data.purpose.strip()[:4],
        "",                              # Strukturierte Referenz (leer)
        data.reference.strip()[:140],
    ])

    # QR-Code Fehlerkorrektur M (15 %) gemäß EPC-Standard
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(payload.encode("utf-8"))
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
