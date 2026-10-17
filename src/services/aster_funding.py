"""Read-only Aster V3 actual funding ledger, complete or fail closed.

USDT/USDC/USD1 income is reported at nominal USD value, preserving the ledger sign.
Capped intervals are split, not counted; only uncapped leaves establish coverage.
"""
from __future__ import annotations

import re
from asyncio import sleep
from datetime import datetime, timezone
from decimal import Decimal, DecimalException, InvalidOperation, localcontext
from typing import Any, Awaitable, Callable

from eth_account import Account

from src.connectors.aster_connector import AsterRealConnector
from src.services.credential_store import CredentialStore

MAX_REQUESTS = 256
_PAGE_LIMIT = 1000
_ORIGIN = 'https://fapi.asterdex.com'
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_ADDRESS = re.compile(r'0x[0-9a-fA-F]{40}')
_SYMBOL = re.compile(r'[A-Z0-9]+')


class _FundingSigner(AsterRealConnector):
    """Reuse the V3 signer without consulting runtime or environment credentials."""

    def __init__(self, credentials: tuple[str, str, str]):
        self._funding_credentials = credentials

    def _credentials(self) -> tuple[str, str, str]:
        return self._funding_credentials

    @classmethod
    def _nonce(cls) -> int:
        # Call on the original class: inherited assignment would fork its counter.
        # This shares uniqueness with account/position requests in this process.
        return AsterRealConnector._nonce()


def _validated_credentials(value: Any) -> tuple[str, str, str]:
    if not isinstance(value, dict):
        raise ValueError('invalid Aster V3 credentials')
    user, signer, key = (value.get(name) for name in ('user_address', 'signer', 'signer_private_key'))
    if (
        not isinstance(user, str) or not user or not isinstance(signer, str) or not signer
        or not isinstance(key, str) or not key
    ):
        raise ValueError('missing Aster V3 credentials')
    if not _ADDRESS.fullmatch(user) or not _ADDRESS.fullmatch(signer):
        raise ValueError('invalid Aster V3 address')
    try:
        matches = Account.from_key(key).address.lower() == signer.lower()
    except Exception:
        raise ValueError('invalid Aster V3 signing key') from None
    if not matches:
        raise ValueError('Aster V3 signer/key mismatch')
    return user, signer, key


def _milliseconds(value: datetime) -> int:
    delta = value.astimezone(timezone.utc) - _EPOCH
    return (delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000


async def fetch_aster_funding(
    store: CredentialStore,
    refs: list[str],
    start: datetime,
    end: datetime,
    request_json: Callable[..., Awaitable[Any]],
    api_base: str,
) -> dict[str, Decimal]:
    """Fetch [start, end) for unique master wallets, never return partial totals.

    Boundaries must be timezone-aware and millisecond aligned. The caller owns
    completed-window selection and must make request_json reject HTTP failures.
    Only the trusted production origin is allowed and redirects are disabled.
    """
    if api_base.rstrip('/') != _ORIGIN:
        raise ValueError('untrusted Aster origin')
    if (
        start.utcoffset() is None or end.utcoffset() is None or start >= end
        or start.microsecond % 1000 or end.microsecond % 1000
    ):
        raise ValueError('invalid Aster window')
    start_ms, end_ms = _milliseconds(start), _milliseconds(end)
    accounts: dict[str, tuple[str, str, str]] = {}
    for ref in refs:
        try:
            credentials = store.get_exchange_credentials(ref)
        except Exception:
            raise ValueError('missing Aster V3 credentials') from None
        validated = _validated_credentials(credentials)
        accounts.setdefault(validated[0].lower(), validated)

    totals: dict[str, Decimal] = {}
    requests = 0
    for credentials in accounts.values():
        signer = _FundingSigner(credentials)
        seen: dict[str, dict] = {}
        counted: set[str] = set()
        intervals = [(start_ms, end_ms)]
        while intervals:
            low, high = intervals.pop()
            if requests >= MAX_REQUESTS:
                raise ValueError('Aster request budget exhausted')
            if requests:
                await sleep(0.51)
            # Sign only after pacing, using the shared connector nonce generator.
            params = signer._signed_params({
                'incomeType': 'FUNDING_FEE', 'startTime': low, 'endTime': high - 1,
                'limit': _PAGE_LIMIT,
            })
            requests += 1
            try:
                payload = await request_json(
                    'GET', _ORIGIN + '/fapi/v3/income', params=params,
                    follow_redirects=False,
                )
            except Exception:
                raise ValueError('Aster funding request failed') from None
            if not isinstance(payload, list) or len(payload) > _PAGE_LIMIT:
                raise ValueError('unexpected Aster funding history')
            parsed = []
            for item in payload:
                if not isinstance(item, dict):
                    raise ValueError('invalid Aster funding record')
                record_id, symbol, event_time = item.get('tranId'), item.get('symbol'), item.get('time')
                asset, income = item.get('asset'), item.get('income')
                # JSON integers are exact in Python; never coerce via float or
                # accept bool (an int subclass). Retain opaque string IDs.
                if type(record_id) is int and record_id > 0:
                    record_id = str(record_id)
                if (
                    not isinstance(record_id, str) or not record_id.strip()
                    or not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol)
                    or asset not in ('USDT', 'USDC', 'USD1') or not symbol.endswith(asset)
                    or len(symbol) <= len(asset) or item.get('incomeType') != 'FUNDING_FEE'
                    or type(event_time) is not int or event_time <= 0
                    or not isinstance(income, str) or not income.strip()
                ):
                    raise ValueError('incomplete Aster funding record')
                try:
                    amount = Decimal(income)
                except InvalidOperation:
                    raise ValueError('invalid Aster funding amount') from None
                if not amount.is_finite():
                    raise ValueError('non-finite Aster funding amount')
                normalized = {**item, 'tranId': record_id}
                if record_id in seen and seen[record_id] != normalized:
                    raise ValueError('conflicting Aster funding transaction')
                seen[record_id] = normalized
                parsed.append((record_id, symbol[:-len(asset)], event_time, amount))
            if len(payload) == _PAGE_LIMIT:
                if high - low <= 1:
                    raise ValueError('saturated Aster millisecond')
                middle = (low + high) // 2
                intervals.extend([(middle, high), (low, middle)])
                continue
            for record_id, symbol, event_time, amount in parsed:
                if not low <= event_time < high or record_id in counted:
                    continue
                counted.add(record_id)
                previous = totals.get(symbol, Decimal('0'))
                # Preserve every ledger decimal digit, independent of the caller's
                # default Decimal context; allow an extra carry digit.
                with localcontext() as context:
                    context.prec = max(
                        28, max(previous.adjusted(), amount.adjusted())
                        - min(int(previous.as_tuple().exponent), int(amount.as_tuple().exponent)) + 2,
                    )
                    try:
                        total = previous + amount
                    except DecimalException:
                        raise ValueError('invalid Aster funding total') from None
                if not total.is_finite():
                    raise ValueError('non-finite Aster funding total')
                totals[symbol] = total
    return totals
