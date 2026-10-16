"""Read-only USD-M actual funding ledger; complete coverage or fail closed.

USDT/USDC amounts retain the ledger sign and use nominal USD valuation.
No current-position filter is used, so closed-market income is included.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from asyncio import sleep
from datetime import datetime, timezone
from decimal import Decimal, DecimalException, InvalidOperation, localcontext
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode

from src.services.credential_store import CredentialStore

MAX_REQUESTS = 256
_PAGE_LIMIT = 1000
_ORIGIN = 'https://fapi.binance.com'
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_SYMBOL = re.compile(r'[A-Z0-9]+')
_ID = re.compile(r'[1-9][0-9]*')
_MONEY = re.compile(r'[+-]?[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?')


def _milliseconds(value: datetime) -> int:
    delta = value.astimezone(timezone.utc) - _EPOCH
    return (delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000


async def fetch_binance_funding(
    store: CredentialStore,
    refs: list[str],
    start: datetime,
    end: datetime,
    request_json: Callable[..., Awaitable[Any]],
    api_base: str,
) -> dict[str, Decimal]:
    """Fetch [start, end) using only explicitly supplied profile credentials.

    Caller selects a completed window inside vendor retention and supplies a
    transport that rejects HTTP failures. Only the production origin is trusted;
    redirects are disabled. Capped first pages are replaced by disjoint time
    intervals, never counted alongside their children. No partial result escapes.
    """
    if api_base not in (_ORIGIN, _ORIGIN + '/'):
        raise ValueError('untrusted Binance origin')
    if (
        start.utcoffset() is None or end.utcoffset() is None or start >= end
        or start.microsecond % 1000 or end.microsecond % 1000
    ):
        raise ValueError('invalid Binance window')
    start_ms, end_ms = _milliseconds(start), _milliseconds(end)
    accounts: dict[str, str] = {}
    for ref in refs:
        try:
            credentials = store.get_exchange_credentials(ref)
        except Exception:
            raise ValueError('missing Binance credentials') from None
        if not isinstance(credentials, dict):
            raise ValueError('invalid Binance credentials')
        key, secret = credentials.get('api_key'), credentials.get('api_secret')
        if not isinstance(key, str) or not isinstance(secret, str):
            raise ValueError('invalid Binance credentials')
        if any(not isinstance(value, str) or not value or value != value.strip()
               or not value.isascii() or any(char.isspace() or ord(char) < 32 or ord(char) == 127
                                           for char in value)
               for value in (key, secret)):
            raise ValueError('invalid Binance credentials')
        if key in accounts and accounts[key] != secret:
            raise ValueError('conflicting Binance credentials')
        accounts[key] = secret

    totals: dict[str, Decimal] = {}
    requests = 0
    for key, secret in accounts.items():
        seen: dict[str, dict] = {}
        counted: set[str] = set()
        intervals = [(start_ms, end_ms)]
        while intervals:
            low, high = intervals.pop()
            if requests >= MAX_REQUESTS:
                raise ValueError('Binance request budget exhausted')
            if requests:
                await sleep(0.51)
            # Generate the timestamp AFTER waiting. The ordered mapping is exactly
            # the urlencode order signed here and transmitted by httpx.
            params = dict(sorted({
                'incomeType': 'FUNDING_FEE', 'startTime': str(low), 'endTime': str(high - 1),
                'limit': str(_PAGE_LIMIT), 'page': '1', 'recvWindow': '5000',
                'timestamp': str(_milliseconds(datetime.now(timezone.utc))),
            }.items()))
            query = urlencode(params)
            params['signature'] = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
            requests += 1
            try:
                payload = await request_json(
                    'GET', _ORIGIN + '/fapi/v1/income', params=params,
                    headers={'X-MBX-APIKEY': key}, follow_redirects=False,
                )
            except Exception:
                raise ValueError('Binance funding request failed') from None
            if not isinstance(payload, list) or len(payload) > _PAGE_LIMIT:
                raise ValueError('unexpected Binance funding history')
            parsed = []
            for item in payload:
                if not isinstance(item, dict):
                    raise ValueError('invalid Binance funding record')
                record_id, symbol, event_time = item.get('tranId'), item.get('symbol'), item.get('time')
                asset, income = item.get('asset'), item.get('income')
                if (
                    (type(record_id) is not int and not isinstance(record_id, str))
                    or not _ID.fullmatch(str(record_id))
                    or not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol)
                    or asset not in ('USDT', 'USDC') or not symbol.endswith(asset)
                    or len(symbol) <= len(asset) or item.get('incomeType') != 'FUNDING_FEE'
                    or type(event_time) is not int or event_time <= 0
                    or not isinstance(income, str) or not _MONEY.fullmatch(income)
                ):
                    raise ValueError('incomplete Binance funding record')
                try:
                    amount = Decimal(income)
                except InvalidOperation:
                    raise ValueError('invalid Binance funding amount') from None
                if not amount.is_finite():
                    raise ValueError('non-finite Binance funding amount')
                record_id = str(record_id)
                canonical = {**item, 'tranId': record_id}
                if record_id in seen and seen[record_id] != canonical:
                    raise ValueError('conflicting Binance funding transaction')
                seen[record_id] = canonical
                parsed.append((record_id, symbol[:-len(asset)], event_time, amount))
            if len(payload) == _PAGE_LIMIT:
                if high - low <= 1:
                    raise ValueError('saturated Binance millisecond')
                middle = (low + high) // 2
                intervals.extend([(middle, high), (low, middle)])
                continue
            for record_id, symbol, event_time, amount in parsed:
                if not low <= event_time < high or record_id in counted:
                    continue
                counted.add(record_id)
                previous = totals.get(symbol, Decimal('0'))
                with localcontext() as context:
                    context.prec = max(
                        28, max(previous.adjusted(), amount.adjusted())
                        - min(int(previous.as_tuple().exponent), int(amount.as_tuple().exponent)) + 2,
                    )
                    try:
                        total = previous + amount
                    except DecimalException:
                        raise ValueError('invalid Binance funding total') from None
                if not total.is_finite():
                    raise ValueError('non-finite Binance funding total')
                totals[symbol] = total
    return totals
