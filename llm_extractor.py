"""
llm_extractor.py – Rechnungsdaten-Extraktion via OpenAI-kompatibler API

Unterstützte Anbieter (OpenAI-kompatibel):
  - OpenRouter   https://openrouter.ai/api/v1   (empfohlen, Qwen2.5-VL verfügbar)
  - Together.ai  https://api.together.xyz/v1
  - OpenAI       https://api.openai.com/v1
"""
import base64
import json
import logging
import re
from typing import Optional

import fitz  # PyMuPDF
from openai import OpenAI, APIError

logger = logging.getLogger(__name__)

# Prompt fordert strukturierte JSON-Ausgabe
_SYSTEM_PROMPT = (
    "You are an invoice data extraction assistant. "
    "Analyze invoice documents and extract payment information. "
    "Always respond with a single valid JSON object, nothing else."
)

_USER_PROMPT = """\
Analyze the invoice in the image and extract the payment information.

Respond ONLY with a valid JSON object in exactly this format:
{
  "is_invoice": true,
  "name": "Beneficiary/Payee full name",
  "iban": "IBAN without spaces",
  "bic": "BIC/SWIFT code or null",
  "amount": 0.00,
  "reference": "Payment reference / Verwendungszweck or null"
}

Rules:
- Set "is_invoice" to false if this is clearly not an invoice, set all other fields to null.
- "amount" must be a number (float), not a string.
- "iban" must be uppercase without spaces.
- If a field is not present in the document, use null.
"""

# IBAN-Basisvalidierung: 2 Buchstaben + 2 Prüfziffern + 10-30 alphanumerische Zeichen
_IBAN_RE = re.compile(r"^[A-Z]{2}[0-9]{2}[A-Z0-9]{10,30}$")


def extract_from_pdf(
    pdf_bytes: bytes,
    api_base_url: str,
    api_key: str,
    model: str,
    max_pages: int = 3,
) -> Optional[dict]:
    """
    Sendet PDF-Seiten als Bilder an eine OpenAI-kompatible API und extrahiert Zahlungsdaten.
    Gibt ein dict {name, iban, bic, amount, reference} zurück oder None.

    max_pages: Maximale Anzahl Seiten (0 = alle Seiten).
    """
    images_b64 = _pdf_to_images_b64(pdf_bytes)
    if not images_b64:
        logger.warning("PDF konnte nicht in Bilder konvertiert werden.")
        return None

    client = OpenAI(base_url=api_base_url, api_key=api_key)
    pages = images_b64 if max_pages == 0 else images_b64[:max_pages]
    logger.debug("Analysiere %d von %d Seite(n) mit Modell '%s'…", len(pages), len(images_b64), model)

    for page_num, img_b64 in enumerate(pages, start=1):
        logger.debug("Sende Seite %d…", page_num)
        result = _query_api(client, img_b64, model)
        if result:
            return result

    return None


# ---------------------------------------------------------------------------
# Interne Hilfsfunktionen
# ---------------------------------------------------------------------------

def _pdf_to_images_b64(pdf_bytes: bytes) -> list[str]:
    """Rendert jede PDF-Seite als PNG und gibt Base64-kodierte Strings zurück."""
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        images: list[str] = []
        for page in doc:
            pix = page.get_pixmap(dpi=150)
            images.append(base64.b64encode(pix.tobytes("png")).decode("ascii"))
        doc.close()
        return images
    except Exception as e:
        logger.error("PDF→Bild-Konvertierung fehlgeschlagen: %s", e)
        return []


def _query_api(client: OpenAI, image_b64: str, model: str) -> Optional[dict]:
    """Sendet ein Bild an die Chat-Completions-API und parst die JSON-Antwort."""
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                        },
                        {"type": "text", "text": _USER_PROMPT},
                    ],
                },
            ],
            response_format={"type": "json_object"},
            timeout=120,
        )
    except APIError as e:
        logger.error("API-Fehler: %s", e)
        return None

    try:
        content = response.choices[0].message.content or ""
        data = json.loads(content)
    except (json.JSONDecodeError, IndexError, AttributeError) as e:
        logger.error("API-Antwort konnte nicht geparst werden: %s", e)
        return None

    if not data.get("is_invoice"):
        logger.info("LLM: keine Rechnung erkannt.")
        return None

    iban = (data.get("iban") or "").replace(" ", "").upper()
    if not iban or not _IBAN_RE.match(iban):
        logger.warning("LLM: ungültige oder fehlende IBAN '%s'.", iban)
        return None

    try:
        amount = float(data.get("amount") or 0)
    except (ValueError, TypeError):
        amount = 0.0

    return {
        "name": (data.get("name") or "").strip(),
        "iban": iban,
        "bic": (data.get("bic") or "").strip(),
        "amount": amount,
        "reference": (data.get("reference") or "").strip(),
    }
