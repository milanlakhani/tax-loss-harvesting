from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

_TWO_PLACES = Decimal("0.01")
_SIX_PLACES = Decimal("0.000001")


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _as_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _plain_fixed(number: Decimal) -> str:
    text = format(number, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in {"", "-0", "+0"}:
        return "0"
    return text


def _parse_temporal(value: Any) -> date | datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return value
    if _blank(value):
        return None
    text = str(value).strip()
    if "T" not in text and " " not in text:
        try:
            return date.fromisoformat(text)
        except ValueError:
            pass
    if text.endswith("Z") and "T" in text:
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def format_calendar_date(value: Any) -> str:
    parsed = _parse_temporal(value)
    if parsed is None:
        return "" if _blank(value) else str(value)
    if isinstance(parsed, datetime):
        return _as_utc(parsed).date().isoformat()
    return parsed.isoformat()


def format_timestamp(value: Any) -> str:
    parsed = _parse_temporal(value)
    if parsed is None:
        return "" if _blank(value) else str(value)
    if isinstance(parsed, datetime):
        return f"{_as_utc(parsed).strftime('%Y-%m-%d %H:%M')} UTC"
    return parsed.isoformat()


def format_ordinary_currency(value: Any) -> str:
    number = _as_decimal(value)
    if number is None:
        return "" if _blank(value) else str(value)
    return format(number.quantize(_TWO_PLACES), "f")


def format_equity_price(value: Any) -> str:
    return format_ordinary_currency(value)


def format_crypto_price(value: Any) -> str:
    number = _as_decimal(value)
    if number is None:
        return "" if _blank(value) else str(value)
    return _plain_fixed(number.quantize(_SIX_PLACES))


def format_price(value: Any, asset_type: Any = None) -> str:
    if str(asset_type or "").strip().upper() == "CRYPTO":
        return format_crypto_price(value)
    return format_equity_price(value)


def format_quantity(value: Any) -> str:
    number = _as_decimal(value)
    if number is None:
        return "" if _blank(value) else str(value)
    return _plain_fixed(number)


def format_currency_amount(amount: Any, currency: Any = None) -> str:
    number = format_ordinary_currency(amount)
    code = str(currency or "").strip()
    if not number:
        return code
    if not code:
        return number
    return f"{code} {number}"
