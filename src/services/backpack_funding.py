"""Backpack account-wide actual funding payments; GET-only and fail closed.

No position snapshot is consulted: closed positions remain in the ledger.
See docs/backpack-funding.md for schema evidence and coverage limitations.
"""
from __future__ import annotations

import re
from asyncio import sleep, timeout, wait_for
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode

from src.connectors.backpack_connector import BackpackRealConnector, validate_keypair
from src.services.credential_store import CredentialStore

ORIGIN = 'https://api.backpack.exchange'
PATH = '/wapi/v1/history/funding'
MAX_REQUESTS = 256
PAGE_LIMIT = 1000
REQUEST_TIMEOUT = 15.0
TOTAL_TIMEOUT = 120.0
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_TIMESTAMP = re.compile(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})?')
_DECIMAL = re.compile(r'-?\d+(?:\.\d+)?')


def _datetime_ns(value: datetime) -> int:
    delta = value.astimezone(timezone.utc) - _EPOCH
    return ((delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds) * 1000


def _event_ns(value: Any) -> int:
    match = _TIMESTAMP.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise ValueError('invalid Backpack funding timestamp')
    seconds, fraction, offset = match.groups()
    # Assume naive-date-time is UTC; OpenAPI does not explicitly prove timezone.
    parsed = datetime.fromisoformat(seconds + ('+00:00' if offset in (None, 'Z') else offset))
    return _datetime_ns(parsed) + int((fraction or '').ljust(9, '0'))


def _decimal(value: Any) -> Decimal:
    if not isinstance(value, str) or len(value) > 1024 or _DECIMAL.fullmatch(value) is None:
        raise ValueError('invalid Backpack funding decimal')
    return Decimal(value)


def _add(left: Decimal, right: Decimal) -> Decimal:
    """Integer coefficients avoid rounding under the caller's Decimal context."""
    def parts(value: Decimal) -> tuple[int, int]:
        sign, digits, exponent = value.as_tuple()
        return (-1 if sign else 1) * int(''.join(map(str, digits))), int(exponent)
    a, ae = parts(left)
    b, be = parts(right)
    exponent = min(ae, be)
    result = a * 10 ** (ae - exponent) + b * 10 ** (be - exponent)
    return Decimal((int(result < 0), tuple(map(int, str(abs(result)))), exponent))


def _record(item: Any) -> tuple[tuple, str, int, Decimal]:
    if not isinstance(item, dict):
        raise ValueError('invalid Backpack funding record')
    user, sub = item.get('userId'), item.get('subaccountId')
    if type(user) is not int or not 0 <= user <= 2147483647:
        raise ValueError('invalid Backpack user ID')
    if sub is not None and (type(sub) is not int or not 0 <= sub <= 65535):
        raise ValueError('invalid Backpack subaccount ID')
    # FundingPayment has no transaction ID. Validate optional future ID fields
    # without ever converting large JSON integers through float.
    if 'id' in item:
        ident = item['id']
        if not ((type(ident) is int and ident >= 0)
                or (isinstance(ident, str) and re.fullmatch(r'[0-9]+', ident))):
            raise ValueError('invalid Backpack funding ID')
    market = item.get('symbol')
    if not isinstance(market, str) or re.fullmatch(r'[A-Z0-9][A-Z0-9.]*_USDC_PERP', market) is None:
        raise ValueError('unsupported Backpack funding market or asset')
    if any(name in item and item[name] != 'USDC' for name in ('asset', 'currency', 'settlementAsset')):
        raise ValueError('unsupported Backpack funding settlement asset')
    event = _event_ns(item.get('intervalEndTimestamp'))
    amount = _decimal(item.get('quantity'))
    _decimal(item.get('fundingRate'))
    return (user, sub, market, event), market.removesuffix('_USDC_PERP'), event, amount


async def fetch_backpack_funding(
    store: CredentialStore,
    refs: list[str],
    start: datetime,
    end: datetime,
    request_json: Callable[..., Awaitable[Any]],
    api_base: str,
) -> dict[str, Decimal]:
    """Return complete signed nominal-USDC amounts by base for [start,end).

    Caller supplies enabled profile refs and a transport raising on HTTP errors.
    All profiles preflight before IO. Any error invalidates the entire result;
    callers must not convert exceptions to an empty successful ledger.
    """
    if api_base not in (ORIGIN, ORIGIN + '/'):
        raise ValueError('untrusted Backpack origin')
    if start.utcoffset() is None or end.utcoffset() is None or start >= end:
        raise ValueError('invalid Backpack funding window')
    lower, upper = _datetime_ns(start), _datetime_ns(end)
    accounts = {}
    for ref in refs:
        try:
            credentials = store.get_exchange_credentials(ref)
            if not isinstance(credentials, dict):
                raise ValueError
            public, key = validate_keypair(credentials)
        except Exception:
            raise ValueError('invalid Backpack funding credentials') from None
        if public in accounts:
            if credentials != accounts[public][0]:
                raise ValueError('conflicting Backpack credential aliases')
            continue
        accounts[public] = (dict(credentials), key)
    totals: dict[str, Decimal] = {}
    # Distinct API keys can expose the same underlying user/subaccount ledger.
    # Keep per-key overlap checks too: a repeated page must still fail closed.
    global_seen: dict[tuple, tuple[Decimal, Decimal]] = {}
    requests = 0
    try:
        async with timeout(TOTAL_TIMEOUT):
            for public, (_, key) in accounts.items():
                offset = 0
                seen = set()
                while True:
                    if requests >= MAX_REQUESTS:
                        raise ValueError('Backpack funding request budget exhausted')
                    if requests:
                        await sleep(0.1)
                    params = {'limit': PAGE_LIMIT, 'offset': offset, 'sortDirection': 'Asc'}
                    query = urlencode(sorted(params.items()))
                    headers = BackpackRealConnector._headers('fundingHistoryQueryAll', params, public, key)
                    requests += 1
                    try:
                        payload = await wait_for(request_json(
                            'GET', ORIGIN + PATH + '?' + query, headers=headers,
                            follow_redirects=False, timeout=REQUEST_TIMEOUT,
                        ), timeout=REQUEST_TIMEOUT)
                    except Exception:
                        raise ValueError('Backpack funding request failed') from None
                    if not isinstance(payload, list) or len(payload) > PAGE_LIMIT:
                        raise ValueError('unexpected Backpack funding history')
                    if not payload:
                        break
                    for item in payload:
                        identity, symbol, event, amount = _record(item)
                        if identity in seen:
                            raise ValueError('Backpack funding pagination overlap or no progress')
                        seen.add(identity)
                        fingerprint = (amount, _decimal(item['fundingRate']))
                        if identity in global_seen:
                            if global_seen[identity] != fingerprint:
                                raise ValueError('conflicting Backpack funding account event')
                            continue
                        global_seen[identity] = fingerprint
                        if lower <= event < upper:
                            totals[symbol] = _add(totals.get(symbol, Decimal('0')), amount)
                    # Even short pages may be server-capped. Only an empty page
                    # establishes completion; offset advances by actual rows.
                    offset += len(payload)
    except TimeoutError:
        raise ValueError('Backpack funding deadline exceeded') from None
    return totals
