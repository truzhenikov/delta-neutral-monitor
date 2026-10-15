"""Read-only OKX funding bills, with fail-closed seven-day coverage.

USDT/USDC amounts are nominal USD, not FX-converted. No positions, orders,
archive reconstruction, or balance changes are used to infer funding.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode

from src.connectors.real_connectors import OkxRealConnector
from src.services.credential_store import CredentialStore


async def fetch_okx_funding(
    store: CredentialStore,
    refs: list[str],
    start: datetime,
    end: datetime,
    request_json: Callable[..., Awaitable[Any]],
    api_base: str,
    *,
    now: datetime | None = None,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    max_pages: int = 256,
) -> dict[str, Decimal]:
    """Return signed funding by underlying for the completed [start, end) window.

    The injected transport must honor ``follow_redirects=False`` and raise on
    HTTP errors. Any failure raises; callers must discard the entire venue's
    totals and sanitize transport exceptions. ``now`` is a coverage-test seam,
    not the signing clock: the existing connector signs each request freshly.
    """
    if api_base.rstrip('/') != 'https://www.okx.com':
        raise ValueError('untrusted OKX origin')
    now = datetime.now(timezone.utc) if now is None else now
    if any(value.tzinfo is None or value.utcoffset() is None for value in (start, end, now)):
        raise ValueError('OKX funding window requires timezones')
    if not now - timedelta(days=7) <= start < end <= now:
        raise ValueError('OKX funding window outside completed seven-day history')
    if type(max_pages) is not int or not 1 <= max_pages <= 256:
        raise ValueError('invalid OKX page cap')
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    start_ms = (start - epoch) // timedelta(milliseconds=1)
    end_ms = (end - epoch) // timedelta(milliseconds=1)
    signer = OkxRealConnector()
    totals: dict[str, Decimal] = {}
    seen: dict[int, dict[str, Any]] = {}
    keys: set[str] = set()
    requested = False
    for ref in refs:
        credentials = store.get_exchange_credentials(ref)
        values = [credentials.get(name) for name in ('api_key', 'api_secret', 'api_passphrase')]
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError('missing OKX credentials')
        api_key, secret, passphrase = (str(value) for value in values)
        if api_key in keys:
            continue
        keys.add(api_key)
        cursor: int | None = None
        account_ids: set[int] = set()
        for _ in range(max_pages):
            params = {'instType': 'SWAP', 'type': '8', 'begin': str(start_ms),
                      'end': str(end_ms - 1), 'limit': '100'}
            if cursor is not None:
                params['after'] = str(cursor)
            path = '/api/v5/account/bills?' + urlencode(params)
            if requested:
                await sleep(0.2)  # at most five requests/second, including profile changes
            headers = signer._build_okx_headers(api_key, secret, passphrase, 'GET', path)
            requested = True
            payload = await request_json('GET', api_base.rstrip('/') + path,
                                         headers=headers, follow_redirects=False)
            if (not isinstance(payload, dict) or payload.get('code') != '0'
                    or not isinstance(payload.get('data'), list) or len(payload['data']) > 100):
                raise ValueError('unexpected OKX funding history response')
            rows = payload['data']
            previous = cursor
            for item in rows:
                if not isinstance(item, dict):
                    raise ValueError('invalid OKX funding bill')
                raw_id = item.get('billId')
                if not isinstance(raw_id, str) or not re.fullmatch(r'[0-9]+', raw_id):
                    raise ValueError('invalid OKX bill ID')
                bill_id = int(raw_id)
                if bill_id in account_ids or (previous is not None and bill_id >= previous):
                    raise ValueError('overlapping or unordered OKX funding pages')
                previous = bill_id
                account_ids.add(bill_id)
                timestamp = item.get('ts')
                if not isinstance(timestamp, str) or not re.fullmatch(r'[0-9]+', timestamp):
                    raise ValueError('invalid OKX bill timestamp')
                if item.get('type') != '8' or item.get('instType') != 'SWAP' or item.get('subType') not in ('173', '174'):
                    raise ValueError('unexpected OKX funding bill type')
                currency = item.get('ccy')
                if currency not in ('USDT', 'USDC'):
                    raise ValueError('unsupported OKX funding settlement currency')
                instrument = item.get('instId')
                suffix = '-' + currency + '-SWAP'
                if not isinstance(instrument, str) or not instrument.endswith(suffix) or not instrument[:-len(suffix)]:
                    raise ValueError('invalid OKX funding instrument')
                symbol = instrument[:-len(suffix)]
                raw_amount = item.get('pnl')
                if not isinstance(raw_amount, str) or not raw_amount.strip():
                    raise ValueError('missing OKX funding pnl')
                try:
                    amount = Decimal(raw_amount)
                except InvalidOperation:
                    raise ValueError('invalid OKX funding pnl') from None
                if not amount.is_finite():
                    raise ValueError('non-finite OKX funding pnl')
                if (item['subType'] == '173' and amount > 0) or (item['subType'] == '174' and amount < 0):
                    raise ValueError('OKX funding sign conflicts with subtype')
                if bill_id in seen:
                    if seen[bill_id] != item:
                        raise ValueError('conflicting OKX funding bill')
                    continue
                seen[bill_id] = item
                if start_ms <= int(timestamp) < end_ms:
                    totals[symbol] = totals.get(symbol, Decimal('0')) + amount
            if len(rows) < 100:
                break
            # Every ID was strictly descending; the last is the minimum billId.
            cursor = previous
        else:
            raise ValueError('OKX funding page limit exceeded')
    return totals
