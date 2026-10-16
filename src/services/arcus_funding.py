"""Public Arcus actual funding payments, complete only below the native page cap.

The API's optional total is the *returned page* count, not matching history.
Funding events occupy whole-millisecond ticks, even though the API uses µs.
"""
from __future__ import annotations

import re
from asyncio import sleep
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any, Awaitable, Callable

from src.services.credential_store import CredentialStore

MAX_REQUESTS = 256
_PAGE_LIMIT = 1000
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_DECIMAL = re.compile(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)')
_CURRENCY_FIELDS = ('currency', 'asset', 'quoteCurrency', 'settleCurrency', 'settlementCurrency')


def _epoch_us(value: datetime) -> int:
    delta = value.astimezone(timezone.utc) - _EPOCH
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def _number(value: Any) -> Decimal:
    if not isinstance(value, str) or not _DECIMAL.fullmatch(value):
        raise ValueError('invalid Arcus decimal')
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError('invalid Arcus decimal') from exc
    if not result.is_finite():
        raise ValueError('non-finite Arcus decimal')
    return result


def _add(left: Decimal, right: Decimal) -> Decimal:
    # Cover the complete integer/fraction span plus a carry, independent of
    # the caller's Decimal context (including deliberately reduced precision).
    with localcontext() as context:
        context.prec = max(left.adjusted(), right.adjusted(), 0) - min(
            int(left.as_tuple().exponent), int(right.as_tuple().exponent), 0,
        ) + 2
        return left + right


async def fetch_arcus_funding(
    store: CredentialStore,
    refs: list[str],
    start: datetime,
    end: datetime,
    request_json: Callable[..., Awaitable[Any]],
    api_base: str,
) -> dict[str, Decimal]:
    if api_base.rstrip('/') != 'https://api.arcus.xyz':
        raise ValueError('untrusted Arcus origin')
    if start.utcoffset() is None or end.utcoffset() is None or start >= end:
        raise ValueError('invalid Arcus window')
    start_us, end_us = _epoch_us(start), _epoch_us(end)
    if start_us < 100_000_000_000_000:
        raise ValueError('invalid Arcus epoch')
    # Convert [start, end) to [ceil(start/ms), ceil(end/ms)) integer ticks.
    # Inclusive wire bounds then name exactly the first and last actual ticks.
    first, stop = (start_us + 999) // 1000, (end_us + 999) // 1000
    accounts: list[tuple[str, int]] = []
    for ref in refs:
        identity = store.get_exchange_credentials(ref)
        address, index = identity.get('address'), identity.get('account_index')
        if not isinstance(address, str) or not re.fullmatch(r'0x[0-9a-fA-F]{40}', address):
            raise ValueError('invalid Arcus public address')
        if type(index) is int and 0 <= index <= 9:
            pass
        elif isinstance(index, str) and re.fullmatch(r'[0-9]', index):
            index = int(index)
        else:
            raise ValueError('missing or invalid Arcus account index')
        account = (address.lower(), index)
        if account not in accounts:
            accounts.append(account)

    totals: dict[str, Decimal] = {}
    requests = 0
    for address, index in accounts:
        seen: dict[tuple[int, int], tuple] = {}
        counted: set[tuple[int, int]] = set()
        intervals = [(first, stop)] if first < stop else []
        while intervals:
            low, high = intervals.pop()
            if requests >= MAX_REQUESTS:
                raise ValueError('Arcus request budget exhausted')
            if requests:
                await sleep(0.51)
            requests += 1
            payload = await request_json(
                'GET', api_base.rstrip('/') + '/v1/funding', follow_redirects=False,
                params={'address': address, 'accountIndex': index, 'from': low * 1000,
                        'to': (high - 1) * 1000, 'limit': _PAGE_LIMIT},
            )
            if (
                not isinstance(payload, dict)
                or any(key in payload for key in ('error', 'code', 'errorType', 'errorSource', 'status'))
                or ('success' in payload and payload['success'] is not True)
                or any(payload[key] not in ('USD', 'USDG') for key in _CURRENCY_FIELDS if key in payload)
                or not isinstance(payload.get('fundingPayments'), list)
            ):
                raise ValueError('unexpected Arcus funding response')
            rows = payload['fundingPayments']
            if len(rows) > _PAGE_LIMIT or ('total' in payload and (
                type(payload['total']) is not int or payload['total'] != len(rows)
            )):
                raise ValueError('invalid Arcus page count')
            parsed = []
            for item in rows:
                if not isinstance(item, dict):
                    raise ValueError('invalid Arcus funding record')
                market, label, event_time = item.get('marketId'), item.get('marketDisplayName'), item.get('time')
                if (
                    type(market) is not int or not 0 <= market <= 65535
                    or not isinstance(label, str) or not label.endswith('-USD')
                    or len(label) <= 4 or label.strip() != label
                    or type(event_time) is not int or event_time < 100_000_000_000_000
                    or event_time % 1000 or not low * 1000 <= event_time <= (high - 1) * 1000
                    or any(item[key] not in ('USD', 'USDG') for key in _CURRENCY_FIELDS if key in item)
                ):
                    raise ValueError('incomplete Arcus funding record')
                amount, rate, size = (_number(item.get(key)) for key in ('payment', 'fundingRate', 'size'))
                key = (market, event_time)
                record = (label, amount, rate, size)
                if key in seen and seen[key] != record:
                    raise ValueError('conflicting Arcus funding event')
                seen[key] = record
                parsed.append((key, label.removesuffix('-USD'), amount))
            if len(rows) == _PAGE_LIMIT:
                if high - low <= 1:
                    raise ValueError('saturated Arcus millisecond')
                middle = (low + high) // 2
                intervals.extend([(middle, high), (low, middle)])
                continue  # Never count a capped parent's rows.
            for key, symbol, amount in parsed:
                if key not in counted:
                    counted.add(key)
                    totals[symbol] = _add(totals.get(symbol, Decimal('0')), amount)
    return totals
