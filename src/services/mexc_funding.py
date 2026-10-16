"""MEXC actual account-wide USDT funding ledger, never position estimates."""
from __future__ import annotations

import hashlib
import hmac
import re
from asyncio import sleep
from datetime import datetime, timezone
from decimal import Decimal, DecimalException, localcontext
from time import time_ns
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode

from src.services.credential_store import CredentialStore

MAX_REQUESTS = 256
_BASE = 'https://contract.mexc.com'
_PATH = '/api/v1/private/position/funding_records'
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_CONTRACT = re.compile(r'([A-Z0-9]+)_USDT')


async def fetch_mexc_funding(
    store: CredentialStore,
    refs: list[str],
    start: datetime,
    end: datetime,
    request_json: Callable[..., Awaitable[Any]],
    api_base: str,
) -> dict[str, Decimal]:
    """Sum signed actual funding in [start,end); all profiles/pages or raise.

    request_json must reject HTTP errors and decode JSON with parse_float=Decimal.
    Float inputs are rejected: lost money/timestamp precision is unrecoverable.
    The inclusive millisecond query envelope is filtered at full source precision.
    No symbol/position filter: closed markets are included. Only USDT-settled
    contracts are supported (nominal USD); other settlement fails closed.
    """
    if api_base != _BASE:
        raise ValueError('untrusted MEXC API base')
    if start.utcoffset() is None or end.utcoffset() is None or start >= end:
        raise ValueError('invalid MEXC funding window')
    micros = []
    for boundary in (start, end):
        delta = boundary.astimezone(timezone.utc) - _EPOCH
        micros.append((delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds)
    # String construction avoids ambient Decimal precision even for boundaries.
    low, high = (Decimal(f'{value // 1000}.{value % 1000:03d}') for value in micros)
    query_low, query_high = micros[0] // 1000, (micros[1] + 999) // 1000
    accounts: dict[str, str] = {}
    for ref in refs:
        if not isinstance(ref, str) or not re.fullmatch(r'mexc(?::[^:\s]+)?', ref):
            raise ValueError('invalid MEXC profile reference')
        try:
            credentials = store.get_exchange_credentials(ref)
        except Exception:
            raise ValueError('missing MEXC credentials') from None
        if not isinstance(credentials, dict):
            raise ValueError('missing MEXC credentials')
        key, secret = credentials.get('api_key'), credentials.get('api_secret')
        if (not isinstance(key, str) or not key.strip()
                or not isinstance(secret, str) or not secret.strip()):
            raise ValueError('missing MEXC credentials')
        if key in accounts and accounts[key] != secret:
            raise ValueError('conflicting MEXC alias credentials')
        accounts[key] = secret

    totals: dict[str, Decimal] = {}
    requests = 0
    for key, secret in accounts.items():
        page = 1
        expected_count = None
        seen: set[str] = set()
        while True:
            if requests >= MAX_REQUESTS:
                raise ValueError('MEXC funding request budget exhausted')
            # Below 20/2s across pages AND profiles. Sign only after waiting.
            if requests:
                await sleep(0.12)
            params = dict(sorted(dict(start_time=query_low, end_time=query_high,
                                      page_num=page, page_size=100).items()))
            query = urlencode(params)
            timestamp = str(time_ns() // 1000000)
            signature = hmac.new(secret.encode(), (key + timestamp + query).encode(), hashlib.sha256).hexdigest()
            headers = {'ApiKey': key, 'Request-Time': timestamp, 'Signature': signature,
                       'Recv-Window': '10', 'Content-Type': 'application/json'}
            requests += 1
            try:
                payload = await request_json('GET', _BASE + _PATH, params=params,
                                             headers=headers, follow_redirects=False)
            except Exception:
                raise ValueError('MEXC funding request failed') from None
            if not isinstance(payload, dict) or payload.get('success') is not True or type(payload.get('code')) is not int or payload['code'] != 0:
                raise ValueError('MEXC funding API rejected request')
            data = payload.get('data')
            if not isinstance(data, dict):
                raise ValueError('unexpected MEXC funding history')
            if any(type(data.get(k)) is not int for k in ('currentPage', 'pageSize', 'totalCount', 'totalPage')):
                raise ValueError('invalid MEXC funding pagination')
            count = data['totalCount']
            pages = (count + 99) // 100
            rows = data.get('resultList')
            if (count < 0 or data['currentPage'] != page or data['pageSize'] != 100
                    or data['totalPage'] != pages or not isinstance(rows, list)
                    or len(rows) != min(100, max(0, count - (page - 1) * 100))
                    or (expected_count is not None and count != expected_count)):
                raise ValueError('inconsistent MEXC funding pagination')
            expected_count = count
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError('invalid MEXC funding record')
                record_id, contract = row.get('id'), row.get('symbol')
                match = _CONTRACT.fullmatch(contract) if isinstance(contract, str) else None
                if type(record_id) not in (str, int) or not str(record_id).strip() or not match:
                    raise ValueError('invalid MEXC funding identity or settlement')
                record_id = str(record_id)
                if record_id in seen:
                    raise ValueError('overlapping MEXC funding record IDs')
                seen.add(record_id)
                for field in ('currency', 'asset', 'settleCoin'):
                    if field in row and row[field] != 'USDT':
                        raise ValueError('contradictory MEXC funding currency')
                values = []
                for field in ('funding', 'settleTime'):
                    raw = row.get(field)
                    if type(raw) not in (str, int, Decimal):
                        raise ValueError('invalid MEXC funding number')
                    try:
                        value = Decimal(raw)
                    except DecimalException:
                        raise ValueError('invalid MEXC funding decimal') from None
                    if not value.is_finite():
                        raise ValueError('non-finite MEXC funding number')
                    values.append(value)
                amount, event_time = values
                if event_time <= 0:
                    raise ValueError('invalid MEXC funding timestamp')
                if not low <= event_time < high:
                    continue
                previous = totals.get(match[1], Decimal(0))
                with localcontext() as context:
                    context.prec = max(28, max(previous.adjusted(), amount.adjusted())
                                       - min(int(previous.as_tuple().exponent), int(amount.as_tuple().exponent)) + 2)
                    try:
                        total = previous + amount
                    except DecimalException:
                        raise ValueError('invalid MEXC funding total') from None
                if not total.is_finite():
                    raise ValueError('non-finite MEXC funding total')
                totals[match[1]] = total
            if page >= pages:
                if len(seen) != count:
                    raise ValueError('incomplete MEXC funding history')
                break
            page += 1
    return totals
