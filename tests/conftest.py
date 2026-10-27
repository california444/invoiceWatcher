"""
Gemeinsame Fixtures: erzeugt Test-Rechnungen (CII, UBL, PDF) zur Laufzeit,
damit keine Binärdateien im Repo liegen müssen.
"""
import os
import sys
from pathlib import Path

import pymupdf
import pytest

# Module liegen im Repo-Root, nicht in einem Paket
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# invoice_watcher liest Pflicht-Variablen schon beim Import. load_dotenv()
# überschreibt bereits gesetzte Variablen nicht, eine lokale .env bleibt
# also ohne Einfluss auf diese Werte.
for _key, _value in {
    "SMTP_HOST": "smtp.example.invalid",
    "SMTP_USER": "watcher@example.invalid",
    "SMTP_PASSWORD": "test",
    "LLM_API_KEY": "test",
    "LLM_MODEL": "test-model",
}.items():
    os.environ.setdefault(_key, _value)


IBAN = "DE89370400440532013000"
BIC = "COBADEFFXXX"
NAME = "Muster GmbH"
AMOUNT = 119.0
REFERENCE = "RE-2026-042"

EXPECTED = {
    "name": NAME,
    "iban": IBAN,
    "bic": BIC,
    "amount": AMOUNT,
    "reference": REFERENCE,
}


def make_cii(iban: str = "DE89 3704 0044 0532 0130 00", amount: str = "119.00") -> bytes:
    """Minimale CII-Rechnung (ZUGFeRD 2 / Factur-X / XRechnung CII)."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rsm:CrossIndustryInvoice
  xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
  xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
  xmlns:udt="urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100">
  <rsm:ExchangedDocumentContext>
    <ram:GuidelineSpecifiedDocumentContextParameter>
      <ram:ID>urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0</ram:ID>
    </ram:GuidelineSpecifiedDocumentContextParameter>
  </rsm:ExchangedDocumentContext>
  <rsm:SupplyChainTradeTransaction>
    <ram:ApplicableHeaderTradeAgreement>
      <ram:SellerTradeParty><ram:Name>{NAME}</ram:Name></ram:SellerTradeParty>
    </ram:ApplicableHeaderTradeAgreement>
    <ram:ApplicableHeaderTradeSettlement>
      <ram:PaymentReference>{REFERENCE}</ram:PaymentReference>
      <ram:SpecifiedTradeSettlementPaymentMeans>
        <ram:PayeePartyCreditorFinancialAccount>
          <ram:IBANID>{iban}</ram:IBANID>
        </ram:PayeePartyCreditorFinancialAccount>
        <ram:PayeeSpecifiedCreditorFinancialInstitution>
          <ram:BICID>{BIC}</ram:BICID>
        </ram:PayeeSpecifiedCreditorFinancialInstitution>
      </ram:SpecifiedTradeSettlementPaymentMeans>
      <ram:SpecifiedTradeSettlementHeaderMonetarySummation>
        <ram:GrandTotalAmount>{amount}</ram:GrandTotalAmount>
      </ram:SpecifiedTradeSettlementHeaderMonetarySummation>
    </ram:ApplicableHeaderTradeSettlement>
  </rsm:SupplyChainTradeTransaction>
</rsm:CrossIndustryInvoice>""".encode("utf-8")


def make_ubl(iban: str = IBAN) -> bytes:
    """Minimale UBL-Rechnung (XRechnung UBL)."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
  xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
  xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
  <cac:AccountingSupplierParty><cac:Party><cac:PartyName>
    <cbc:Name>{NAME}</cbc:Name>
  </cac:PartyName></cac:Party></cac:AccountingSupplierParty>
  <cac:PaymentMeans>
    <cbc:PaymentID>{REFERENCE}</cbc:PaymentID>
    <cac:PayeeFinancialAccount>
      <cbc:ID>{iban}</cbc:ID>
      <cac:FinancialInstitutionBranch><cbc:ID>{BIC}</cbc:ID></cac:FinancialInstitutionBranch>
    </cac:PayeeFinancialAccount>
  </cac:PaymentMeans>
  <cac:LegalMonetaryTotal>
    <cbc:PayableAmount currencyID="EUR">119.00</cbc:PayableAmount>
  </cac:LegalMonetaryTotal>
</Invoice>""".encode("utf-8")


def make_pdf(attachments: dict[str, bytes] | None = None, pages: int = 1) -> bytes:
    """PDF mit `pages` Seiten und optional eingebetteten Dateien."""
    doc = pymupdf.open()
    for i in range(pages):
        doc.new_page().insert_text((72, 72), f"Rechnung, Seite {i + 1}")
    for filename, content in (attachments or {}).items():
        doc.embfile_add(filename, content)
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture
def cii() -> bytes:
    return make_cii()


@pytest.fixture
def ubl() -> bytes:
    return make_ubl()
