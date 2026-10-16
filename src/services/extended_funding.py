"""Actual signed USD funding payments from Extended's authenticated ledger.

Pagination is cursor based, not count based. Do not infer completion from short
pages or stop at a current position/market/time: closed markets belong here too.
"""
from __future__ import annotations

import re
from asyncio import sleep
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from typing import Any, Awaitable, Callable

from src.services.credential_store import CredentialStore

ORIGIN = 'https://api.starknet.extended.exchange'
MAX_REQUESTS = 256
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_DECIMAL = re.compile(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)')
_MARKET = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}-USD')


class ExtendedCredentialError(ValueError):
    """Safe actionable credential diagnostic, never includes the API key."""


def _epoch_us(value: datetime) -> int:
    delta = value.astimezone(timezone.utc) - _EPOCH
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def _number(value: Any) -> Decimal:
    if not isinstance(value, str) or not _DECIMAL.fullmatch(value):
        raise ValueError('invalid Extended decimal')
    return Decimal(value)


def _add(left: Decimal, right: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = max(left.adjusted(), right.adjusted(), 0) - min(
            int(left.as_tuple().exponent), int(right.as_tuple().exponent), 0,
        ) + 2
        return left + right


async def fetch_extended_funding(
    store: CredentialStore,
    refs: list[str],
    start: datetime,
    end: datetime,
    request_json: Callable[..., Awaitable[Any]],
    api_base: str,
) -> dict[str, Decimal]:
    base = api_base.rstrip('/')
    if base != ORIGIN:
        raise ValueError('untrusted Extended origin')
    if start.utcoffset() is None or end.utcoffset() is None or start >= end:
        raise ValueError('invalid Extended window')
    low, high = _epoch_us(start), _epoch_us(end)
    if low < 1_000_000_000_000_000:
        raise ValueError('invalid Extended epoch')

    # Validate every required profile before any authenticated call. Only the
    # API key is needed; Stark signing keys and client IDs never leave storage.
    keys: dict[str, None] = {}
    for ref in refs:
        if ref.split(':', 1)[0] != 'extended':
            raise ValueError('Extended credential profile mismatch')
        key = store.get_exchange_credentials(ref).get('api_key')
        if not isinstance(key, str) or not re.fullmatch(r'[\x21-\x7e]+', key):
            raise ExtendedCredentialError('missing or invalid api_key')
        keys[key] = None

    requests = 0
    totals: dict[str, Decimal] = {}
    # accountId binds event identity even if different API keys alias one account.
    seen: dict[tuple[int, int], tuple] = {}
    for key in keys:
        account = None
        cursor = None
        cursors: set[int] = set()
        profile_events: set[int] = set()
        while True:
            if requests >= MAX_REQUESTS:
                raise ValueError('Extended request budget exhausted')
            if requests:
                await sleep(0.51)
            requests += 1
            params = dict(startTime=low // 1000, limit=100)
            if cursor is not None:
                params['cursor'] = cursor
            payload = await request_json(
                'GET', base + '/api/v1/user/funding/history', params=params,
                headers={'X-Api-Key': key, 'User-Agent': 'delta-neutral-monitor'}, follow_redirects=False,
            )
            if not isinstance(payload, dict) or payload.get('status') != 'OK' or payload.get('error') is not None:
                raise ValueError('unexpected Extended response')
            rows, pagination = payload.get('data'), payload.get('pagination')
            if (not isinstance(rows, list) or len(rows) > 100 or not isinstance(pagination, dict)
                    or type(pagination.get('count')) is not int or pagination['count'] != len(rows)):
                raise ValueError('invalid Extended funding page')
            before = len(profile_events)
            for item in rows:
                if not isinstance(item, dict):
                    raise ValueError('invalid Extended funding record')
                event, identity, position, timestamp = (item.get(field) for field in ('id', 'accountId', 'positionId', 'paidTime'))
                market = item.get('market')
                if (any(type(value) is not int or not 0 <= value <= 2**63-1 for value in (event, identity, position))
                        or type(timestamp) is not int or not 1_000_000_000_000 <= timestamp < 10_000_000_000_000
                        or not isinstance(market, str) or not _MARKET.fullmatch(market)
                        or item.get('side') not in ('LONG', 'SHORT')
                        or any(item[field] != 'USD' for field in ('currency', 'quoteCurrency', 'settlementCurrency') if field in item)):
                    raise ValueError('incomplete Extended funding record')
                if account is None:
                    account = identity
                elif identity != account:
                    raise ValueError('Extended account changed within profile')
                fee, rate, value, mark = (_number(item.get(field)) for field in ('fundingFee', 'fundingRate', 'value', 'markPrice'))
                size = _number(item['size']) if 'size' in item else None
                record = (market, position, timestamp, item['side'], fee, rate, value, mark, size)
                assert isinstance(event, int) and isinstance(identity, int)  # validated above
                event_key = (identity, event)
                profile_events.add(event)
                if event_key in seen:
                    if seen[event_key] != record:
                        raise ValueError('conflicting Extended funding event')
                    continue
                seen[event_key] = record
                if low <= timestamp * 1000 < high:
                    # fundingFee is the signed account payout, not its rate or
                    # position notional. Preserve the opaque market base name.
                    symbol = market[:-4]
                    totals[symbol] = _add(totals.get(symbol, Decimal('0')), fee)
            cursor = pagination.get('cursor')
            if cursor is None:
                break
            if type(cursor) is not int or not 0 <= cursor <= 2**63-1 or cursor in cursors:
                raise ValueError('invalid or repeated Extended cursor')
            if rows and len(profile_events) == before:
                raise ValueError('Extended pagination made no progress')
            cursors.add(cursor)
    return totals
