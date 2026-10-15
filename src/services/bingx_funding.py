"""Actual BingX USDT funding ledger; saturated intervals are never treated as complete."""
from __future__ import annotations

import hashlib
import hmac
from asyncio import sleep
from datetime import datetime
from decimal import Decimal
from time import time
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode

from src.services.credential_store import CredentialStore

MAX_REQUESTS = 256
_PAGE_LIMIT = 1000


async def fetch_bingx_funding(
    store: CredentialStore,
    refs: list[str],
    start: datetime,
    end: datetime,
    request_json: Callable[..., Awaitable[Any]],
    api_base: str,
) -> dict[str, Decimal]:
    if api_base.rstrip('/') != 'https://open-api.bingx.com':
        raise ValueError('untrusted BingX origin')
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        raise ValueError('invalid BingX window')
    start_ms, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    totals: dict[str, Decimal] = {}
    seen_accounts: set[str] = set()
    requests = 0
    for ref in refs:
        credentials = store.get_exchange_credentials(ref)
        key, secret = credentials.get('api_key'), credentials.get('api_secret')
        if not isinstance(key, str) or not key.strip() or not isinstance(secret, str) or not secret.strip():
            raise ValueError('missing BingX credentials')
        if key in seen_accounts:
            continue
        seen_accounts.add(key)
        seen: dict[str, dict] = {}
        counted: set[str] = set()
        intervals = [(start_ms, end_ms)]
        while intervals:
            low, high = intervals.pop()
            if requests >= MAX_REQUESTS:
                raise ValueError('BingX request budget exhausted')
            if requests:
                # Conservatively space ledger requests across profiles, not just pages.
                await sleep(0.51)
            params = dict(sorted({
                'incomeType': 'FUNDING_FEE', 'startTime': low, 'endTime': high - 1,
                'limit': _PAGE_LIMIT, 'recvWindow': 5000, 'timestamp': int(time() * 1000),
            }.items()))
            # httpx preserves dict insertion order. The wire MUST match the signed bytes.
            params['signature'] = hmac.new(secret.encode(), urlencode(params).encode(), hashlib.sha256).hexdigest()
            requests += 1
            payload = await request_json(
                'GET', api_base.rstrip('/') + '/openApi/swap/v2/user/income',
                headers={'X-BX-APIKEY': key}, params=params,
            )
            if (
                not isinstance(payload, dict) or type(payload.get('code')) is not int
                or payload['code'] != 0 or not isinstance(payload.get('data'), list)
                or len(payload['data']) > _PAGE_LIMIT
            ):
                raise ValueError('unexpected BingX history')
            rows = payload['data']
            parsed = []
            for item in rows:
                if not isinstance(item, dict):
                    raise ValueError('invalid BingX record')
                record_id, symbol, event_time = item.get('tranId'), item.get('symbol'), item.get('time')
                if (
                    not isinstance(record_id, str) or not record_id.strip()
                    or not isinstance(symbol, str) or not symbol.endswith('-USDT') or len(symbol) <= 5
                    or type(event_time) is not int or event_time <= 0
                    or item.get('incomeType') != 'FUNDING_FEE' or item.get('asset') != 'USDT'
                    or not isinstance(item.get('income'), (str, int, float)) or isinstance(item['income'], bool)
                    or item['income'] == ''
                ):
                    raise ValueError('incomplete BingX funding record')
                amount = Decimal(str(item['income']))
                if not amount.is_finite():
                    raise ValueError('non-finite BingX funding')
                if record_id in seen and seen[record_id] != item:
                    raise ValueError('conflicting BingX transaction')
                seen[record_id] = item
                parsed.append((record_id, symbol, event_time, amount))
            if len(rows) == _PAGE_LIMIT:
                if high - low <= 1:
                    raise ValueError('saturated BingX millisecond')
                middle = (low + high) // 2
                intervals.extend([(middle, high), (low, middle)])
                # Capped parent data must not be added to the uncapped child totals.
                continue
            for record_id, symbol, event_time, amount in parsed:
                if not low <= event_time < high or record_id in counted:
                    continue
                counted.add(record_id)
                totals[symbol] = totals.get(symbol, Decimal('0')) + amount

    # Public naming is best effort and never affects completeness or ledger amounts.
    labels: dict[str, str] = {}
    try:
        metadata = await request_json('GET', api_base.rstrip('/') + '/openApi/swap/v2/quote/contracts')
        if isinstance(metadata, dict) and type(metadata.get('code')) is int and metadata['code'] == 0:
            for contract in metadata.get('data', []):
                if not isinstance(contract, dict):
                    continue
                symbol, label = contract.get('symbol'), contract.get('displayName')
                if isinstance(symbol, str) and isinstance(label, str) and label.endswith('-USDT') and len(label) > 5:
                    labels[symbol] = label
    except Exception:
        labels = {}
    display_totals: dict[str, Decimal] = {}
    for symbol, amount in totals.items():
        label = labels.get(symbol, symbol).removesuffix('-USDT')
        display_totals[label] = display_totals.get(label, Decimal('0')) + amount
    return display_totals
