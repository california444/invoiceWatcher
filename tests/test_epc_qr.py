import io

import pytest
import qrcode
from PIL import Image

from epc_qr import PaymentData, generate_epc_qr


@pytest.fixture
def captured_payload(monkeypatch):
    """Fängt den an den QR-Code übergebenen Payload ab."""
    captured = []
    original = qrcode.QRCode.add_data

    def add_data(self, data, *args, **kwargs):
        captured.append(data.decode("utf-8") if isinstance(data, bytes) else data)
        return original(self, data, *args, **kwargs)

    monkeypatch.setattr(qrcode.QRCode, "add_data", add_data)
    return captured


def test_generates_valid_png():
    png = generate_epc_qr(PaymentData(name="Muster GmbH", iban="DE89370400440532013000"))
    img = Image.open(io.BytesIO(png))
    assert img.format == "PNG"
    assert img.width == img.height > 0


def test_payload_format(captured_payload):
    generate_epc_qr(PaymentData(
        name="Muster GmbH",
        iban="de89 3704 0044 0532 0130 00",
        bic="COBADEFFXXX",
        amount=119.0,
        reference="RE-2026-042",
    ))
    assert captured_payload[0].split("\n") == [
        "BCD", "002", "1", "SCT",
        "COBADEFFXXX",
        "Muster GmbH",
        "DE89370400440532013000",
        "EUR119.00",
        "",
        "",
        "RE-2026-042",
    ]


def test_payload_without_amount(captured_payload):
    generate_epc_qr(PaymentData(name="Muster GmbH", iban="DE89370400440532013000"))
    assert captured_payload[0].split("\n")[7] == ""


def test_payload_truncates_fields(captured_payload):
    generate_epc_qr(PaymentData(
        name="N" * 100,
        iban="DE89370400440532013000",
        bic="B" * 20,
        reference="R" * 200,
        purpose="GDDSXX",
    ))
    lines = captured_payload[0].split("\n")
    assert len(lines[4]) == 11
    assert len(lines[5]) == 70
    assert len(lines[8]) == 4
    assert len(lines[10]) == 140
