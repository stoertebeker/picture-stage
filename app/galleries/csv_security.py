"""Keep untrusted CSV cells from being interpreted as spreadsheet formulas."""


def spreadsheet_text(value: str) -> str:
    """Prefix hazardous cells while preserving ordinary text verbatim.

    CSV quoting protects delimiters but does not prevent spreadsheet formula
    evaluation. Apply this to strings at the export boundary, not in storage.
    """
    if not value:
        return value
    stripped = value.lstrip()
    if ord(value[0]) < 32 or (stripped and stripped[0] in "=+-@\uff1d\uff0b\uff0d\uff20"):
        return "'" + value
    return value
