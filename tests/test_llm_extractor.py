import base64
import io
import json
from types import SimpleNamespace

import pytest
from PIL import Image

import llm_extractor
from conftest import make_pdf


class FakeClient:
    """Ersetzt den OpenAI-Client; liefert vorgegebene Antworttexte."""

    def __init__(self, *contents: str):
        self._contents = list(contents)
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        content = self._contents.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def answer(**fields) -> str:
    data = {
        "is_invoice": True,
        "name": "Muster GmbH",
        "iban": "DE89 3704 0044 0532 0130 00",
        "bic": None,
        "amount": 119.0,
        "reference": "RE-2026-042",
    }
    data.update(fields)
    return json.dumps(data)


# ---------------------------------------------------------------------------
# PDF → Bilder (PyMuPDF)
# ---------------------------------------------------------------------------

def test_pdf_to_images_renders_every_page():
    images = llm_extractor._pdf_to_images_b64(make_pdf(pages=3))
    assert len(images) == 3
    img = Image.open(io.BytesIO(base64.b64decode(images[0])))
    assert img.format == "PNG"
    # 150 dpi auf A4 (595 x 842 pt) ergibt grob 1240 x 1754 px
    assert 1200 < img.width < 1300


def test_pdf_to_images_invalid_pdf():
    assert llm_extractor._pdf_to_images_b64(b"kein PDF") == []


# ---------------------------------------------------------------------------
# Antwort der API auswerten
# ---------------------------------------------------------------------------

def test_query_api_valid_answer():
    client = FakeClient(answer())
    assert llm_extractor._query_api(client, "aW1n", "modell") == {
        "name": "Muster GmbH",
        "iban": "DE89370400440532013000",
        "bic": "",
        "amount": 119.0,
        "reference": "RE-2026-042",
    }
    request = client.requests[0]
    assert request["model"] == "modell"
    assert request["response_format"] == {"type": "json_object"}
    image_part = request["messages"][1]["content"][0]
    assert image_part["image_url"]["url"] == "data:image/png;base64,aW1n"


def test_query_api_not_an_invoice():
    assert llm_extractor._query_api(FakeClient(answer(is_invoice=False)), "x", "m") is None


@pytest.mark.parametrize("iban", [None, "", "DE12", "12DE370400440532013000"])
def test_query_api_rejects_invalid_iban(iban):
    assert llm_extractor._query_api(FakeClient(answer(iban=iban)), "x", "m") is None


def test_query_api_invalid_json():
    assert llm_extractor._query_api(FakeClient("kein json"), "x", "m") is None


def test_query_api_amount_not_a_number():
    result = llm_extractor._query_api(FakeClient(answer(amount="viel")), "x", "m")
    assert result["amount"] == 0.0


# ---------------------------------------------------------------------------
# Gesamtablauf mit Seitenlimit
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("max_pages, expected_calls", [(2, 2), (0, 4)])
def test_extract_from_pdf_respects_max_pages(monkeypatch, max_pages, expected_calls):
    client = FakeClient(*[answer(is_invoice=False)] * 4)
    monkeypatch.setattr(llm_extractor, "OpenAI", lambda **_: client)

    result = llm_extractor.extract_from_pdf(make_pdf(pages=4), "http://x", "key", "m", max_pages)

    assert result is None
    assert len(client.requests) == expected_calls


def test_extract_from_pdf_stops_at_first_hit(monkeypatch):
    client = FakeClient(answer(is_invoice=False), answer(), answer())
    monkeypatch.setattr(llm_extractor, "OpenAI", lambda **_: client)

    result = llm_extractor.extract_from_pdf(make_pdf(pages=3), "http://x", "key", "m", 0)

    assert result["iban"] == "DE89370400440532013000"
    assert len(client.requests) == 2
