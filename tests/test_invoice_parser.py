import pytest

import invoice_parser
from conftest import EXPECTED, make_cii, make_pdf, make_ubl


# ---------------------------------------------------------------------------
# ZUGFeRD / Factur-X (XML eingebettet in PDF)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("filename", [
    "factur-x.xml",          # Factur-X / ZUGFeRD 2.x
    "zugferd-invoice.xml",   # ZUGFeRD 1.0 / 2.0
    "ZUGFeRD-invoice.xml",
    "xrechnung.xml",         # ZUGFeRD 2.1+ mit XRechnung-Profil
])
def test_parse_pdf_embedded_cii(cii, filename):
    assert invoice_parser.parse_pdf(make_pdf({filename: cii})) == EXPECTED


def test_parse_pdf_without_attachment():
    assert invoice_parser.parse_pdf(make_pdf()) is None


def test_parse_pdf_ignores_non_invoice_xml():
    pdf = make_pdf({"metadata.xml": b"<?xml version='1.0'?><config><a>1</a></config>"})
    assert invoice_parser.parse_pdf(pdf) is None


def test_parse_pdf_without_iban():
    assert invoice_parser.parse_pdf(make_pdf({"factur-x.xml": make_cii(iban="")})) is None


def test_parse_pdf_garbage():
    assert invoice_parser.parse_pdf(b"kein PDF") is None


# ---------------------------------------------------------------------------
# XRechnung als eigenständiger XML-Anhang
# ---------------------------------------------------------------------------

def test_parse_xml_attachment_cii(cii):
    assert invoice_parser.parse_xml_attachment(cii) == EXPECTED


def test_parse_xml_attachment_ubl(ubl):
    assert invoice_parser.parse_xml_attachment(ubl) == EXPECTED


def test_parse_xml_attachment_normalizes_iban():
    result = invoice_parser.parse_xml_attachment(make_ubl(iban="de89 3704 0044 0532 0130 00"))
    assert result["iban"] == EXPECTED["iban"]


def test_parse_xml_attachment_unknown_root():
    assert invoice_parser.parse_xml_attachment(b"<config/>") is None


def test_parse_xml_attachment_invalid_xml():
    assert invoice_parser.parse_xml_attachment(b"<kaputt") is None


@pytest.mark.parametrize("value, expected", [
    ("119.00", 119.0),
    ("119,50", 119.5),
    ("", 0.0),
    ("abc", 0.0),
])
def test_to_float(value, expected):
    assert invoice_parser._to_float(value) == expected
