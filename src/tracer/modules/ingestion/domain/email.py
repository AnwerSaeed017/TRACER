"""An email reduced to the parts extraction may look at.

Everything here is **untrusted data**: written by whoever sent the email. It is read, matched
and quoted, never followed. No field of an email can grant a permission or trigger an action.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Final

MAX_BODY_CHARACTERS: Final = 100_000


@dataclass(frozen=True, slots=True)
class ParsedEmail:
    message_id: str | None
    sender: str | None  # the From address as written; not proof of who sent it
    subject: str
    sent_at: datetime | None
    body_text: str  # plain text: the text/plain part, or HTML reduced to text
    attachment_names: tuple[str, ...]  # listed for context, never opened
