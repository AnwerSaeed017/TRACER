from email.message import EmailMessage

import pytest

from tests.emails import raw_email
from tests.support import T0
from tracer.modules.ingestion.adapters.email.parser import StandardLibraryEmailParser
from tracer.shared.errors import InvalidValueError

parse = StandardLibraryEmailParser().parse


def test_plain_email() -> None:
    email = parse(raw_email())
    assert email.subject == "SHIP-501 delayed"
    assert "delayed by 7 days" in email.body_text
    assert email.sender == "Logistics <ops@acme-supplier.example>"
    assert email.message_id == "<msg-501@acme-supplier.example>"
    assert email.sent_at == T0
    assert email.attachment_names == ()


def test_html_only_email_is_reduced_to_visible_text() -> None:
    html = (
        "<html><head><style>p{color:red}</style></head><body>"
        "<p>Port closed for <b>48 hours</b></p><script>alert('x')</script>"
        "<p>SHIP-777 waiting</p></body></html>"
    )
    email = parse(raw_email(html=html))
    assert email.body_text == "Port closed for 48 hours\nSHIP-777 waiting"


def test_attachments_are_listed_not_read() -> None:
    email = parse(raw_email(attachment=("bill-of-lading.pdf", b"%PDF-1.7 ...")))
    assert email.attachment_names == ("bill-of-lading.pdf",)
    assert "%PDF" not in email.body_text


def test_encoded_subject_and_non_ascii_body_are_decoded() -> None:
    email = parse(raw_email(subject="Verzögerung: Lieferung SHIP-9", body="Ankunft verschoben ✓"))
    assert email.subject == "Verzögerung: Lieferung SHIP-9"
    assert email.body_text.strip() == "Ankunft verschoben ✓"


def test_a_malformed_date_is_tolerated() -> None:
    message = EmailMessage()
    message["Subject"] = "late"
    message["Date"] = "sometime next week"
    message.set_content("delayed")
    assert parse(message.as_bytes()).sent_at is None


@pytest.mark.parametrize("raw", [b"", b"\r\n\r\n", b"just some bytes without headers"])
def test_not_an_email(raw: bytes) -> None:
    with pytest.raises(InvalidValueError):
        parse(raw)


def test_an_email_with_nothing_readable_is_refused() -> None:
    message = EmailMessage()
    message["From"] = "x@example.com"
    message.set_content("   ")
    with pytest.raises(InvalidValueError, match="neither a subject nor a readable body"):
        parse(message.as_bytes())
