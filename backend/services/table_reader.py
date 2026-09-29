"""
One place that turns an uploaded .xlsx / .xls / .csv into a DataFrame.

Everything is read as TEXT (dtype=str): SKUs like "3202" must stay "3202",
never become 3202.0, and marketplace order exports are often CSV, not xlsx.
Numbers (quantities, counts) are parsed explicitly with to_int() below.
"""
from io import BytesIO

import pandas as pd


def read_table(file_bytes: bytes, filename: str = "") -> pd.DataFrame:
    name = (filename or "").lower()
    try:
        if name.endswith(".csv"):
            return pd.read_csv(BytesIO(file_bytes), dtype=str, keep_default_na=True)
        return pd.read_excel(BytesIO(file_bytes), dtype=str)
    except Exception as exc:  # corrupt zip, wrong extension, empty file, unsupported .xls ...
        raise ValueError(
            f"Could not read this file as Excel/CSV ({type(exc).__name__}). "
            "Save it as .xlsx or .csv and try again."
        ) from exc


def to_int(value):
    """'5', '5.0', 5, 5.0 -> 5.   Blank / junk -> None."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return int(float(str(value).strip()))
    except (ValueError, TypeError):
        return None


def clean_code(value) -> str | None:
    """Normalise a code cell: strip, uppercase. Blank -> None."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip().upper()
    return text or None
