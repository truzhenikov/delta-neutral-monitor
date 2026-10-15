"""Gate USDT actual funding: account-wide ledger, complete or fail closed.

Signed USDT change is nominal USD (not a rate/position estimate). Only documented
USDT settlement is supported here; coin settlement must never be called USD.
"""
from __future__ import annotations

import re
from asyncio import sleep
from datetime import datetime, timezone
from decimal import Decimal, DecimalException, InvalidOperation, localcontext
from typing import Any, Awaitable, Callable

from src.connectors.real_connectors import GateRealConnector
from src.services.credential_store import CredentialStore

MAX_REQUESTS = 256
_PAGE_LIMIT = 100
_BASE = 'https://api.gateio.ws/api/v4'
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_CONTRACT = re.compile(r'([A-Z0-9]+)_USDT')


async def fetch_gate_funding(
    store: CredentialStore,
    refs: list[str],
    start: datetime,
    end: datetime,
    request_json: Callable[..., Awaitable[Any]],
    api_base: str,
    settle_currency: str,
) -> dict[str, Decimal]:
    """Return exact [start,end) totals; require aware whole-second boundaries.

    Gate's inclusive query envelope includes the end second; local Decimal
    timestamp filtering excludes the end and preserves subsecond ledger times.
    Only a short page proves completion. Overlap is unsafe with offset paging.
    The caller must reject unsuccessful HTTP responses in request_json and
    decode fractional JSON numbers as Decimal (not float) for exact attribution.
    """
    if api_base != _BASE:
        raise ValueError('untrusted Gate API base')
    if settle_currency != 'usdt':
        raise ValueError('unsupported Gate funding settlement')
    if (start.utcoffset() is None or end.utcoffset() is None or start >= end
            or start.microsecond or end.microsecond):
        raise ValueError('invalid Gate funding window')
    bounds = []
    for boundary in (start, end):
        delta = boundary.astimezone(timezone.utc) - _EPOCH
        bounds.append(delta.days * 86400 + delta.seconds)
    low, high = bounds
    accounts: dict[str, str] = {}
    for ref in refs:
        try:
            credentials = store.get_exchange_credentials(ref)
        except Exception:
            raise ValueError('missing Gate credentials') from None
        if not isinstance(credentials, dict):
            raise ValueError('missing Gate credentials')
        key, secret = credentials.get('api_key'), credentials.get('api_secret')
        if not isinstance(key, str) or not key.strip() or not isinstance(secret, str) or not secret.strip():
            raise ValueError('missing Gate credentials')
        if key in accounts and accounts[key] != secret:
            raise ValueError('conflicting Gate alias credentials')
        accounts[key] = secret

    totals: dict[str, Decimal] = {}
    requests = 0
    # No constructor/configuration lookup: this helper signs explicit credentials.
    signer = object.__new__(GateRealConnector)
    url = _BASE + '/futures/usdt/account_book'
    for key, secret in accounts.items():
        seen: set[str] = set()
        offset = 0
        while True:
            if requests >= MAX_REQUESTS:
                raise ValueError('Gate funding request budget exhausted')
            if requests:
                await sleep(0.51)
            params = {'type': 'fund', 'from': low, 'to': high, 'limit': _PAGE_LIMIT, 'offset': offset}
            headers = signer._build_gate_headers(key, secret, 'GET', url, params)
            requests += 1
            try:
                payload = await request_json('GET', url, params=params, headers=headers, follow_redirects=False)
            except Exception:
                raise ValueError('Gate funding request failed') from None
            if not isinstance(payload, list) or len(payload) > _PAGE_LIMIT:
                raise ValueError('unexpected Gate funding history')
            for item in payload:
                if not isinstance(item, dict):
                    raise ValueError('invalid Gate funding record')
                record_id, contract = item.get('id'), item.get('contract')
                raw_amount, raw_time = item.get('change'), item.get('time')
                match = _CONTRACT.fullmatch(contract) if isinstance(contract, str) else None
                if (not isinstance(record_id, str) or not record_id.strip() or not match
                        or item.get('type') != 'fund'
                        or not isinstance(raw_amount, str) or not raw_amount.strip()
                        or type(raw_time) not in (str, int, float, Decimal)):
                    raise ValueError('incomplete Gate funding record')
                for field in ('currency', 'asset'):
                    if field in item and item[field] != 'USDT':
                        raise ValueError('contradictory Gate funding currency')
                try:
                    amount, event_time = Decimal(raw_amount), Decimal(str(raw_time))
                except InvalidOperation:
                    raise ValueError('invalid Gate funding decimal') from None
                if not amount.is_finite() or not event_time.is_finite() or event_time <= 0:
                    raise ValueError('invalid Gate funding amount or time')
                if record_id in seen:
                    raise ValueError('overlapping Gate funding record IDs')
                seen.add(record_id)
                if not low <= event_time < high:
                    continue
                symbol = match[1]
                previous = totals.get(symbol, Decimal('0'))
                with localcontext() as context:
                    context.prec = max(28, max(previous.adjusted(), amount.adjusted())
                        - min(int(previous.as_tuple().exponent), int(amount.as_tuple().exponent)) + 2)
                    try:
                        total = previous + amount
                    except DecimalException:
                        raise ValueError('invalid Gate funding total') from None
                if not total.is_finite():
                    raise ValueError('non-finite Gate funding total')
                totals[symbol] = total
            if len(payload) < _PAGE_LIMIT:
                break
            offset += _PAGE_LIMIT
    return totals
