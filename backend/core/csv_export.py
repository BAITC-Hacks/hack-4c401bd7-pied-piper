"""Prepare UTF-8 CSV downloads for automatic encoding detection in Excel."""
from codecs import BOM_UTF8


def csv_download_bytes(raw: bytes) -> bytes:
    """Add the UTF-8 signature without changing the stored analytical bundle."""
    return raw if raw.startswith(BOM_UTF8) else BOM_UTF8 + raw
