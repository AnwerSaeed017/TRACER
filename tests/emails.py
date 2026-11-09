"""Build realistic raw emails for tests with the standard library (never hand-written MIME)."""

from email.message import EmailMessage
from email.utils import format_datetime

from tests.support import T0


def raw_email(
    *,
    subject: str = "SHIP-501 delayed",
    body: str = "Shipment SHIP-501 from SUP-001 is delayed by 7 days. Order ORD-101 affected.",
    html: str | None = None,
    attachment: tuple[str, bytes] | None = None,
    sender: str = "Logistics <ops@acme-supplier.example>",
) -> bytes:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = "disruptions@tracer.example"
    message["Subject"] = subject
    message["Date"] = format_datetime(T0)
    message["Message-ID"] = "<msg-501@acme-supplier.example>"
    if html is None:
        message.set_content(body)
    else:
        message.add_alternative(html, subtype="html")
    if attachment is not None:
        name, data = attachment
        message.add_attachment(data, maintype="application", subtype="pdf", filename=name)
    return message.as_bytes()
