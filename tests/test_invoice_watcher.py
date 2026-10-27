import os
from email.message import EmailMessage

import pytest

import invoice_watcher
from conftest import EXPECTED, make_cii, make_pdf
from invoice_watcher import ImapAccount

ACCOUNT = ImapAccount(host="imap.example.invalid", port=993, user="watcher@example.invalid",
                      password="pw", mailbox="INBOX")


def make_mail(*, pdfs=(), xmls=(), to="rechnung@example.invalid", cc=None,
              sender="Muster GmbH <buchhaltung@muster.invalid>") -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Subject"] = "Ihre Rechnung"
    msg["Message-ID"] = "<rechnung-1@muster.invalid>"
    msg.set_content("Anbei die Rechnung.")
    for i, pdf in enumerate(pdfs):
        msg.add_attachment(pdf, maintype="application", subtype="pdf", filename=f"rechnung{i}.pdf")
    for i, xml in enumerate(xmls):
        msg.add_attachment(xml, maintype="application", subtype="xml", filename=f"rechnung{i}.xml")
    return msg


@pytest.fixture
def no_llm(monkeypatch):
    """Schlägt fehl, sobald der LLM-Fallback aufgerufen wird."""
    def fail(*args, **kwargs):
        pytest.fail("LLM-Fallback aufgerufen, obwohl strukturierte Daten vorliegen")
    monkeypatch.setattr(invoice_watcher, "extract_from_pdf", fail)


@pytest.fixture
def sent_mails(monkeypatch):
    sent = []
    monkeypatch.setattr(invoice_watcher, "send_girocode", lambda **kwargs: sent.append(kwargs))
    return sent


# ---------------------------------------------------------------------------
# Anhänge und Empfängerfilter
# ---------------------------------------------------------------------------

def test_extract_attachments(cii):
    pdf = make_pdf()
    pdfs, xmls = invoice_watcher._extract_attachments(make_mail(pdfs=[pdf], xmls=[cii]))
    assert pdfs == [pdf]
    assert xmls == [cii]


def test_extract_attachments_ignores_body():
    assert invoice_watcher._extract_attachments(make_mail()) == ([], [])


@pytest.mark.parametrize("to, cc, target, expected", [
    ("rechnung@example.invalid", None, "rechnung@example.invalid", True),
    ("Rechnung <RECHNUNG@example.invalid>", None, " rechnung@example.invalid ", True),
    ("andere@example.invalid", "rechnung@example.invalid", "rechnung@example.invalid", True),
    ("andere@example.invalid", None, "rechnung@example.invalid", False),
])
def test_matches_target_recipient(to, cc, target, expected):
    assert invoice_watcher._matches_target_recipient(make_mail(to=to, cc=cc), target) is expected


# ---------------------------------------------------------------------------
# Reihenfolge der Quellen: XRechnung-XML → ZUGFeRD → LLM
# ---------------------------------------------------------------------------

def test_payment_data_from_xml_attachment(no_llm, cii):
    assert invoice_watcher._extract_payment_data([make_pdf()], [cii]) == (EXPECTED, "XRechnung")


def test_payment_data_from_zugferd(no_llm, cii):
    pdf = make_pdf({"factur-x.xml": cii})
    assert invoice_watcher._extract_payment_data([pdf], []) == (EXPECTED, "ZUGFeRD/Factur-X")


def test_payment_data_falls_back_to_llm(monkeypatch):
    calls = []

    def fake_llm(pdf_bytes, base_url, api_key, model, max_pages):
        calls.append(model)
        return EXPECTED

    monkeypatch.setattr(invoice_watcher, "extract_from_pdf", fake_llm)
    data, source = invoice_watcher._extract_payment_data([make_pdf()], [])
    assert data == EXPECTED
    assert source == f"LLM ({invoice_watcher.LLM_MODEL})"
    assert len(calls) == 1


def test_payment_data_nothing_found(monkeypatch):
    monkeypatch.setattr(invoice_watcher, "extract_from_pdf", lambda *a: None)
    assert invoice_watcher._extract_payment_data([make_pdf()], []) == (None, "")


# ---------------------------------------------------------------------------
# Kompletter Ablauf einer Rechnungsmail
# ---------------------------------------------------------------------------

def test_handle_message_zugferd_end_to_end(no_llm, sent_mails, cii):
    raw = make_mail(pdfs=[make_pdf({"factur-x.xml": cii})]).as_bytes()

    assert invoice_watcher._handle_message(1, raw, ACCOUNT) is True

    [mail] = sent_mails
    assert mail["recipient"] == "buchhaltung@muster.invalid"
    assert mail["subject"] == "Ihre Rechnung"
    assert mail["source"] == "ZUGFeRD/Factur-X"
    assert mail["payment_data"] == EXPECTED
    assert mail["reply_to_message_id"] == "<rechnung-1@muster.invalid>"
    assert mail["qr_png"].startswith(b"\x89PNG")


def test_handle_message_skips_other_recipients(no_llm, sent_mails, cii):
    account = ImapAccount(**{**ACCOUNT.__dict__, "target_recipient": "nur-das@example.invalid"})
    raw = make_mail(pdfs=[make_pdf({"factur-x.xml": cii})]).as_bytes()

    assert invoice_watcher._handle_message(1, raw, account) is False
    assert sent_mails == []


def test_handle_message_without_attachments(no_llm, sent_mails):
    assert invoice_watcher._handle_message(1, make_mail().as_bytes(), ACCOUNT) is True
    assert sent_mails == []


def test_handle_message_without_iban(no_llm, sent_mails, monkeypatch):
    monkeypatch.setattr(invoice_watcher, "extract_from_pdf", lambda *a: None)
    raw = make_mail(pdfs=[make_pdf({"factur-x.xml": make_cii(iban="")})]).as_bytes()

    assert invoice_watcher._handle_message(1, raw, ACCOUNT) is True
    assert sent_mails == []


# ---------------------------------------------------------------------------
# Kontenkonfiguration
# ---------------------------------------------------------------------------

@pytest.fixture
def clean_imap_env(monkeypatch):
    for key in list(os.environ):
        if key.startswith("IMAP_"):
            monkeypatch.delenv(key)
    return monkeypatch


def test_load_accounts_numbered(clean_imap_env):
    for i in (0, 1):
        clean_imap_env.setenv(f"IMAP_HOST_{i}", f"imap{i}.example.invalid")
        clean_imap_env.setenv(f"IMAP_USER_{i}", f"user{i}")
        clean_imap_env.setenv(f"IMAP_PASSWORD_{i}", "pw")
    clean_imap_env.setenv("IMAP_PORT_1", "143")
    clean_imap_env.setenv("IMAP_TARGET_RECIPIENT_1", "rechnung@example.invalid")

    a, b = invoice_watcher.load_accounts()
    assert (a.host, a.port, a.mailbox, a.target_recipient) == ("imap0.example.invalid", 993, "INBOX", None)
    assert (b.host, b.port, b.target_recipient) == ("imap1.example.invalid", 143, "rechnung@example.invalid")


def test_load_accounts_single_fallback(clean_imap_env):
    clean_imap_env.setenv("IMAP_HOST", "imap.example.invalid")
    clean_imap_env.setenv("IMAP_USER", "user")
    clean_imap_env.setenv("IMAP_PASSWORD", "pw")

    [account] = invoice_watcher.load_accounts()
    assert account.host == "imap.example.invalid"


def test_load_accounts_none_configured(clean_imap_env):
    with pytest.raises(RuntimeError):
        invoice_watcher.load_accounts()
