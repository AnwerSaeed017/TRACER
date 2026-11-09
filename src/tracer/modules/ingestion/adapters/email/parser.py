"""Parse raw MIME with the standard library's modern email API (``email.policy.default``).

Decoding (quoted-printable, base64, RFC 2047 encoded headers like ``=?utf-8?...?=``, charsets)
is done by the library, not by hand. Body: the first ``text/plain`` part; if there is none,
the first ``text/html`` part reduced to its visible text. Attachments are listed by name and
never opened.
"""

from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Final, cast

from tracer.modules.ingestion.domain.email import MAX_BODY_CHARACTERS, ParsedEmail
from tracer.shared.errors import InvalidValueError

_MAX_SUBJECT_CHARACTERS: Final = 1000
_MAX_HEADER_CHARACTERS: Final = 500
_SKIPPED_HTML_ELEMENTS: Final = frozenset({"script", "style", "head"})


class _VisibleText(HTMLParser):
    """Collect the text a person would see; drop scripts, styles and tags."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skipping = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIPPED_HTML_ELEMENTS:
            self._skipping += 1
        elif tag in {"br", "p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIPPED_HTML_ELEMENTS and self._skipping:
            self._skipping -= 1

    def handle_data(self, data: str) -> None:
        if not self._skipping:
            self.parts.append(data)


def _html_to_text(html: str) -> str:
    extractor = _VisibleText()
    extractor.feed(html)
    lines = (line.strip() for line in "".join(extractor.parts).splitlines())
    return "\n".join(line for line in lines if line)


def _header(message: EmailMessage, name: str, limit: int) -> str | None:
    value = message.get(name)
    if value is None:
        return None
    text = " ".join(str(value).split())  # unfold and collapse whitespace
    return text[:limit] or None


def _sent_at(message: EmailMessage) -> datetime | None:
    raw = message.get("Date")
    if raw is None:
        return None
    try:
        sent = parsedate_to_datetime(str(raw))
    except (TypeError, ValueError):
        return None  # a malformed Date header is common and not fatal
    return sent.astimezone(UTC) if sent.tzinfo else None


def _content(part: Message) -> str:
    return str(cast(EmailMessage, part).get_content())


class StandardLibraryEmailParser:
    def parse(self, raw: bytes) -> ParsedEmail:
        message = BytesParser(policy=policy.default).parsebytes(raw)
        if not message.keys():
            raise InvalidValueError("not an email: no headers found")

        plain: str | None = None
        html: str | None = None
        attachments: list[str] = []
        for part in message.walk():
            if part.is_multipart():
                continue
            if part.get_content_disposition() == "attachment":
                attachments.append(part.get_filename() or "(unnamed)")
                continue
            content_type = part.get_content_type()
            try:
                if content_type == "text/plain" and plain is None:
                    plain = _content(part)
                elif content_type == "text/html" and html is None:
                    html = _content(part)
            except (LookupError, ValueError):  # unknown charset or broken encoding
                continue

        body = plain if plain is not None else _html_to_text(html) if html is not None else ""
        subject = _header(message, "Subject", _MAX_SUBJECT_CHARACTERS) or ""
        if not subject and not body.strip():
            raise InvalidValueError("the email has neither a subject nor a readable body")
        return ParsedEmail(
            message_id=_header(message, "Message-ID", _MAX_HEADER_CHARACTERS),
            sender=_header(message, "From", _MAX_HEADER_CHARACTERS),
            subject=subject,
            sent_at=_sent_at(message),
            body_text=body[:MAX_BODY_CHARACTERS],
            attachment_names=tuple(attachments),
        )
