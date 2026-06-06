"""
invoice_parser.py – Extraktion strukturierter E-Rechnungsdaten

Unterstützte Formate:
  • ZUGFeRD / Factur-X  – XML eingebettet in PDF (CII-Syntax)
  • XRechnung CII       – Standalone-XML-Anhang (Cross Industry Invoice)
  • XRechnung UBL       – Standalone-XML-Anhang (Universal Business Language)
"""
import io
import logging
from typing import Optional

from lxml import etree

logger = logging.getLogger(__name__)

# CII-Namespaces (ZUGFeRD / Factur-X / XRechnung CII)
_NS_CII = {
    "rsm": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
    "ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100",
    "udt": "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100",
}

# UBL-Namespaces (XRechnung UBL)
_NS_UBL = {
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
}


def parse_pdf(pdf_bytes: bytes) -> Optional[dict]:
    """
    Versucht, Rechnungsdaten aus einem ZUGFeRD/Factur-X-PDF zu extrahieren.
    Gibt ein dict {name, iban, bic, amount, reference} zurück oder None.
    """
    try:
        from facturx import get_facturx_xml_from_pdf  # pip install factur-x

        pdf_file = io.BytesIO(pdf_bytes)
        result = get_facturx_xml_from_pdf(pdf_file, check_xsd=False)

        # API gibt (xml_bytes, filename) zurück
        xml_bytes = result[0] if isinstance(result, tuple) else result
        if not xml_bytes:
            return None

        return _parse_cii_xml(xml_bytes)

    except ImportError:
        logger.warning("factur-x nicht installiert – ZUGFeRD/Factur-X-Parsing deaktiviert.")
    except Exception as e:
        logger.debug("Kein ZUGFeRD/Factur-X in PDF gefunden: %s", e)

    return None


def parse_xml_attachment(xml_bytes: bytes) -> Optional[dict]:
    """
    Versucht, Rechnungsdaten aus einem XML-Dateianhang zu extrahieren.
    Erkennt automatisch CII (XRechnung) und UBL.
    """
    try:
        root = etree.fromstring(xml_bytes)
        tag = root.tag

        if "CrossIndustryInvoice" in tag:
            return _parse_cii_xml(xml_bytes)

        if "Invoice" in tag or "CreditNote" in tag:
            return _parse_ubl_xml(root)

        logger.debug("Unbekanntes XML-Root-Element: %s", tag)
    except Exception as e:
        logger.debug("XML-Anhang-Parsing fehlgeschlagen: %s", e)

    return None


# ---------------------------------------------------------------------------
# Interne Parser
# ---------------------------------------------------------------------------

def _parse_cii_xml(xml_bytes: bytes) -> Optional[dict]:
    """Parst CII-XML (ZUGFeRD/Factur-X/XRechnung CII) und gibt Zahlungsfelder zurück."""
    try:
        root = etree.fromstring(xml_bytes)

        def first(xpath: str) -> str:
            results = root.xpath(xpath, namespaces=_NS_CII)
            return results[0].strip() if results else ""

        iban = first(
            ".//ram:SpecifiedTradeSettlementPaymentMeans"
            "/ram:PayeePartyCreditorFinancialAccount/ram:IBANID"
        )
        bic = first(
            ".//ram:SpecifiedTradeSettlementPaymentMeans"
            "/ram:PayeeSpecifiedCreditorFinancialInstitution/ram:BICID"
        )
        # Rechnungssteller als Empfänger
        name = first(".//ram:SellerTradeParty/ram:Name") or first(".//ram:PayeeTradeParty/ram:Name")

        # Gesamtbetrag
        amount_str = first(
            ".//ram:SpecifiedTradeSettlementHeaderMonetarySummation/ram:GrandTotalAmount"
        )

        # Verwendungszweck: strukturierte Referenz bevorzugen
        reference = (
            first(".//ram:ApplicableHeaderTradeSettlement/ram:PaymentReference")
            or first(".//ram:SpecifiedTradePaymentTerms/ram:Description")
        )

        if not iban:
            logger.debug("CII-XML: keine IBAN gefunden")
            return None

        return {
            "name": name,
            "iban": iban.replace(" ", "").upper(),
            "bic": bic,
            "amount": _to_float(amount_str),
            "reference": reference,
        }

    except Exception as e:
        logger.debug("CII-XML-Parse-Fehler: %s", e)
        return None


def _parse_ubl_xml(root: etree._Element) -> Optional[dict]:
    """Parst UBL-Invoice-XML (XRechnung UBL) und gibt Zahlungsfelder zurück."""
    try:
        def first(xpath: str) -> str:
            results = root.xpath(xpath, namespaces=_NS_UBL)
            return results[0].strip() if results else ""

        iban = first(".//cac:PaymentMeans/cac:PayeeFinancialAccount/cbc:ID")
        bic = first(
            ".//cac:PaymentMeans/cac:PayeeFinancialAccount"
            "/cac:FinancialInstitutionBranch/cbc:ID"
        )
        name = first(".//cac:AccountingSupplierParty/cac:Party/cac:PartyName/cbc:Name")
        amount_str = first(".//cac:LegalMonetaryTotal/cbc:PayableAmount")
        reference = (
            first(".//cac:PaymentMeans/cbc:PaymentID")
            or first(".//cbc:PaymentNote")
        )

        if not iban:
            logger.debug("UBL-XML: keine IBAN gefunden")
            return None

        return {
            "name": name,
            "iban": iban.replace(" ", "").upper(),
            "bic": bic,
            "amount": _to_float(amount_str),
            "reference": reference,
        }

    except Exception as e:
        logger.debug("UBL-XML-Parse-Fehler: %s", e)
        return None


def _to_float(value: str) -> float:
    """Konvertiert einen Betragstring sicher in float."""
    try:
        return float(value.replace(",", ".")) if value else 0.0
    except ValueError:
        return 0.0
