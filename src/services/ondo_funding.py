"""Actual signed USDC funding transfers from Ondo's account-wide ledger.

There are no transfer IDs. Canonical full-row fingerprints suppress exact overlap;
indistinguishable legitimate transfers cannot be distinguished from duplicates.
An explicit empty nextCursor is terminal even for a full page. Missing cursors,
cycles, no-progress pages and budget exhaustion fail closed, never imply completion.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from asyncio import sleep
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from time import time
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode

from src.services.credential_store import CredentialStore

MAX_REQUESTS = 256
_PAGE_LIMIT = 1000
_PATH = '/v1/perps/funding_fees'
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_RFC3339 = re.compile(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})')


def _datetime_ns(value: datetime) -> int:
    delta = value.astimezone(timezone.utc) - _EPOCH
    return ((delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds) * 1000


def _event_ns(value: Any) -> int:
    match = _RFC3339.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise ValueError('invalid Ondo funding time')
    seconds, fraction, offset = match.groups()
    parsed = datetime.fromisoformat(seconds + ('+00:00' if offset == 'Z' else offset))
    return _datetime_ns(parsed) + int((fraction or '').ljust(9, '0'))


async def fetch_ondo_funding(
    store: CredentialStore,
    refs: list[str],
    start: datetime,
    end: datetime,
    request_json: Callable[..., Awaitable[Any]],
    api_base: str,
) -> dict[str, Decimal]:
    if api_base.rstrip('/') != 'https://api.ondoperps.xyz':
        raise ValueError('untrusted Ondo origin')
    if start.utcoffset() is None or end.utcoffset() is None or start >= end:
        raise ValueError('invalid Ondo window')
    start_ns, end_ns = _datetime_ns(start), _datetime_ns(end)
    # Request a conservative inclusive millisecond envelope, then enforce the
    # half-open window locally at full nanosecond precision (no float timestamps).
    params_base = {'startTime': start_ns // 1_000_000,
                   'endTime': (end_ns + 999_999) // 1_000_000, 'limit': _PAGE_LIMIT}
    totals: dict[str, Decimal] = {}
    seen_accounts: set[str] = set()
    requests = 0
    for ref in refs:
        credentials = store.get_exchange_credentials(ref)
        key, secret = credentials.get('api_key'), credentials.get('api_secret')
        if not isinstance(key, str) or not key.strip() or not isinstance(secret, str) or not secret.strip():
            raise ValueError('missing Ondo credentials')
        if key in seen_accounts:
            continue
        seen_accounts.add(key)
        seen_rows: set[str] = set()
        seen_cursors: set[str] = set()
        cursor = ''
        while True:
            if requests >= MAX_REQUESTS:
                raise ValueError('Ondo request budget exhausted')
            if requests:
                # Pace pages AND profiles; sign only after waiting for the key quota.
                await sleep(1.05)
            params = dict(params_base)
            if cursor:
                params['cursor'] = cursor
            path = _PATH + '?' + urlencode(params)
            timestamp = str(int(time() * 1000))
            signature = hmac.new(secret.encode(), (timestamp + 'GET' + path).encode(), hashlib.sha256).hexdigest()
            requests += 1
            payload = await request_json(
                'GET', api_base.rstrip('/') + path,
                headers={'ONDO-KEY-ID': key, 'ONDO-TIMESTAMP': timestamp, 'ONDO-SIGN': signature},
                follow_redirects=False,
            )
            if (
                not isinstance(payload, dict) or payload.get('success') is not True
                or not isinstance(payload.get('result'), list) or len(payload['result']) > _PAGE_LIMIT
                or not isinstance(payload.get('pageInfo'), dict)
                or not isinstance(payload['pageInfo'].get('nextCursor'), str)
            ):
                raise ValueError('unexpected Ondo funding history')
            next_cursor = payload['pageInfo']['nextCursor']
            if next_cursor and next_cursor in seen_cursors:
                raise ValueError('Ondo cursor cycle')
            new_rows = 0
            for item in payload['result']:
                if not isinstance(item, dict):
                    raise ValueError('invalid Ondo funding record')
                market, raw_amount = item.get('market'), item.get('amount')
                if (
                    not isinstance(market, str) or not market.endswith('-USD.P') or len(market) <= 6
                    or not isinstance(raw_amount, str) or not raw_amount.strip()
                    or any(field in item and item[field] != 'USDC' for field in ('asset', 'currency'))
                ):
                    raise ValueError('incomplete Ondo funding record')
                event_ns = _event_ns(item.get('time'))
                try:
                    amount = Decimal(raw_amount)
                except InvalidOperation as exc:
                    raise ValueError('invalid Ondo amount') from exc
                if not amount.is_finite():
                    raise ValueError('non-finite Ondo amount')
                fingerprint = json.dumps(item, sort_keys=True, separators=(',', ':'), allow_nan=False)
                if fingerprint in seen_rows:
                    continue
                seen_rows.add(fingerprint)
                new_rows += 1
                if start_ns <= event_ns < end_ns:
                    symbol = market.removesuffix('-USD.P')
                    # amount is already signed: positive earned, negative paid.
                    totals[symbol] = totals.get(symbol, Decimal('0')) + amount
            if (payload['result'] or next_cursor) and not new_rows:
                raise ValueError('Ondo pagination made no progress')
            if not next_cursor:
                break
            seen_cursors.add(next_cursor)
            cursor = next_cursor
    return totals
