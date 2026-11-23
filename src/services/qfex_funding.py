"""QFEX settled balance-movement ledger; never infer funding from positions.

The API has no cursor. Split inclusive UTC time ranges whenever the result
hits its cap; saturated one-microsecond ranges fail closed. See qfex-funding.md.
"""
from __future__ import annotations

import re
from asyncio import timeout
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable

from src.connectors.qfex_connector import API_VERSION, ORIGIN, _headers, resolve_account, validate_credentials
from src.services.backpack_funding import _add
from src.services.credential_store import CredentialStore

PAGE_LIMIT = 100
MAX_REQUESTS = 256
TOTAL_TIMEOUT = 120
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _decimal(raw: Any) -> Decimal:
    if isinstance(raw, bool) or not isinstance(raw, (str, int, Decimal)):
        raise ValueError('invalid QFEX ledger decimal; transport must decode Decimal')
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise ValueError('invalid QFEX ledger decimal') from None
    if not value.is_finite() or len(value.as_tuple().digits) > 1024 or abs(value.as_tuple().exponent) > 1024:
        raise ValueError('invalid QFEX ledger decimal')
    return value


def _seconds(value: datetime) -> Decimal:
    delta = value.astimezone(timezone.utc) - EPOCH
    return _add(Decimal(delta.days * 86400 + delta.seconds), Decimal((0, tuple(map(int, f'{delta.microseconds:06d}')), -6)))


def _record(item: Any) -> tuple[str, str, Decimal, Decimal]:
    if not isinstance(item, dict):
        raise ValueError('invalid QFEX execution')
    if item.get('execution_type') != 'funding' or item.get('state') != 'completed':
        raise ValueError('unexpected QFEX funding type or settlement state')
    if item.get('currency') not in ('USD', 'USDC'):
        raise ValueError('unsupported QFEX funding currency')
    symbol = item.get('symbol')
    if not isinstance(symbol, str) or re.fullmatch(r'[A-Z0-9][A-Z0-9._-]*-USD', symbol) is None:
        raise ValueError('unsupported QFEX funding market')
    identity = item.get('id')
    if not isinstance(identity, str) or not identity or not isinstance(item.get('transaction_id'), str):
        raise ValueError('missing QFEX execution identity')
    event, amount = _decimal(item.get('created_at')), _decimal(item.get('amount'))
    if event < 0:
        raise ValueError('invalid QFEX execution timestamp')
    # Native amount is a settled balance movement: credit positive, charge negative.
    return identity, symbol.removesuffix('-USD'), event, amount


async def fetch_qfex_funding(
    store: CredentialStore, refs: list[str], start: datetime, end: datetime,
    request_json: Callable[..., Awaitable[Any]], api_base: str = ORIGIN,
) -> dict[str, Decimal]:
    if api_base.rstrip('/') != ORIGIN:
        raise ValueError('untrusted QFEX origin')
    if start.utcoffset() is None or end.utcoffset() is None or start >= end:
        raise ValueError('invalid QFEX funding window')
    credentials = []
    aliases: dict[tuple[str, str], str] = {}
    for ref in refs:
        c = validate_credentials(store.get_exchange_credentials(ref))
        identity = (c['api_key'], c.get('account_id', ''))
        if identity in aliases:
            if aliases[identity] != c['api_secret']:
                raise ValueError('conflicting QFEX credential aliases')
            continue
        aliases[identity] = c['api_secret']
        credentials.append(c)
    totals: dict[str, Decimal] = {}
    seen: dict[tuple[str, str], tuple] = {}
    requests = 0

    async def get(path, c, params):
        nonlocal requests
        requests += 1
        if requests > MAX_REQUESTS:
            raise ValueError('QFEX history request budget exhausted')
        try:
            return await request_json('GET', ORIGIN + path, headers=_headers(c),
                                      params=dict(params, api_version=API_VERSION),
                                      follow_redirects=False, timeout=20)
        except Exception:
            raise ValueError('QFEX history request failed (check account history permission)') from None

    lower, upper = _seconds(start), _seconds(end)
    async with timeout(TOTAL_TIMEOUT):
        for c in credentials:
            scope = await get('/user/subaccounts/balance', {k: v for k, v in c.items() if k != 'account_id'}, {})
            c = resolve_account(c, scope)
            intervals = [(start.astimezone(timezone.utc), end.astimezone(timezone.utc))]
            while intervals:
                lo, hi = intervals.pop()
                payload = await get('/executions', c, dict(
                    execution_type='funding', sort_dir='asc', limit=PAGE_LIMIT,
                    start_time=lo.isoformat().replace('+00:00', 'Z'),
                    end_time=hi.isoformat().replace('+00:00', 'Z')))
                if not isinstance(payload, dict) or type(payload.get('count')) is not int:
                    raise ValueError('invalid QFEX ledger response')
                data = payload.get('data')
                if data is None and payload['count'] == 0:
                    data = []
                if not isinstance(data, list) or payload['count'] != len(data) or len(data) > PAGE_LIMIT:
                    raise ValueError('incomplete QFEX ledger response')
                parsed = [_record(item) for item in data]
                if any(not (_seconds(lo) <= r[2] <= _seconds(hi)) for r in parsed):
                    raise ValueError('QFEX ledger ignored requested interval')
                if len(data) == PAGE_LIMIT:
                    span = hi - lo
                    micros = (span.days * 86400 + span.seconds) * 1000000 + span.microseconds
                    if micros <= 1:
                        raise ValueError('QFEX ledger saturated at timestamp precision')
                    middle = lo + timedelta(microseconds=micros // 2)
                    intervals.extend([(middle, hi), (lo, middle)])
                    continue  # Never count a capped parent and its children.
                local_seen = set()
                for item, (ident, symbol, event, amount) in zip(data, parsed):
                    if ident in local_seen:
                        raise ValueError('duplicate QFEX funding execution')
                    local_seen.add(ident)
                    identity = (c['account_id'].lower(), ident)
                    fingerprint = (symbol, event, amount, item['currency'], item['state'], item['transaction_id'])
                    if identity in seen:
                        if seen[identity] != fingerprint:
                            raise ValueError('conflicting QFEX funding execution')
                        continue
                    seen[identity] = fingerprint
                    if lower <= event < upper:
                        totals[symbol] = _add(totals.get(symbol, Decimal(0)), amount)
    return totals
