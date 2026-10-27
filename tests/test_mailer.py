from email import message_from_bytes

import mailer


def test_send_girocode_builds_message(monkeypatch):
    sent = {}

    def fake_send(host, port, user, password, sender, recipient, msg):
        sent.update(host=host, port=port, sender=sender, recipient=recipient,
                    msg=message_from_bytes(msg.as_bytes()))

    monkeypatch.setattr(mailer, "_send", fake_send)

    mailer.send_girocode(
        smtp_host="smtp.example.invalid",
        smtp_port=587,
        smtp_user="watcher@example.invalid",
        smtp_password="geheim",
        smtp_from="Rechnungen <rechnung@example.invalid>",
        recipient="kunde@example.invalid",
        subject="Rechnung RE-2026-042",
        payment_data={"name": "Muster GmbH", "iban": "DE89370400440532013000",
                      "bic": "", "amount": 119.0, "reference": "RE-2026-042"},
        qr_png=b"\x89PNG\r\n\x1a\nfake",
        reply_to_message_id="<abc@example.invalid>",
    )

    msg = sent["msg"]
    assert sent["recipient"] == "kunde@example.invalid"
    assert sent["sender"] == "Rechnungen <rechnung@example.invalid>"
    assert msg["Subject"] == "Rechnung RE-2026-042"
    assert msg["In-Reply-To"] == msg["References"] == "<abc@example.invalid>"

    text, image = msg.get_payload()
    body = text.get_payload(decode=True).decode("utf-8")
    assert "IBAN: DE89370400440532013000" in body
    assert "BIC: -" in body
    assert "Betrag: EUR 119.00" in body
    assert image.get_content_type() == "image/png"
    assert image.get_filename() == "girocode.png"
    assert image.get_payload(decode=True) == b"\x89PNG\r\n\x1a\nfake"


def test_send_girocode_without_reply_headers(monkeypatch):
    sent = {}
    monkeypatch.setattr(mailer, "_send", lambda *args: sent.update(msg=args[-1], sender=args[4]))

    mailer.send_girocode("h", 465, "watcher@example.invalid", "pw", "kunde@example.invalid",
                         "Betreff", {"iban": "DE89370400440532013000"}, b"png")

    assert sent["sender"] == "watcher@example.invalid"
    assert "In-Reply-To" not in sent["msg"]
