"""Single-mailbox validation shared by signup and SMTP delivery."""

from email import policy
from email.headerregistry import Address, AddressHeader, BaseHeader
from email.parser import HeaderParser
from typing import cast

from pydantic import EmailStr, TypeAdapter, ValidationError

_email_adapter = TypeAdapter(EmailStr)


def normalize_mailbox(value: str) -> str:
    """Normalize one valid mailbox, rejecting recipient lists and malformed input."""
    return _email_adapter.validate_python(value)


def normalize_delivery_mailbox(value: str) -> str:
    """Allow existing local addresses while rejecting SMTP recipient lists.

    Signup uses the stricter API policy. Older accounts can contain local-only
    domains and display names, so parse those as one complete mailbox.
    """
    try:
        return normalize_mailbox(value)
    except ValidationError:
        if "\r" in value or "\n" in value:
            raise ValueError("Mailbox contains a line break") from None
        parsed = HeaderParser(policy=policy.default).parsestr(f"To: {value}\n\n")
        header = cast(AddressHeader, parsed["To"])
        defects = cast(BaseHeader, header).defects
        if defects or len(header.addresses) != 1 or any(group.display_name for group in header.groups):
            raise ValueError("Expected one mailbox") from None
        return Address(addr_spec=header.addresses[0].addr_spec).addr_spec
