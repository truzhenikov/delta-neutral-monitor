"""Authenticated actual funding ledger for independent Lighter deployments.

Walk the cursor to an explicit end; neither page length nor timestamp ordering
proves completeness. Timestamps in returned records are Unix seconds. Optional
wire time-filter units are deliberately not assumed: filter locally instead.
"""
from __future__ import annotations

import re
from asyncio import sleep
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from typing import Any, Awaitable, Callable

from src.services.credential_store import CredentialStore

MAX_REQUESTS = 256
ORIGINS = {'https://mainnet.zklighter.elliot.ai': 'USDC', 'https://api.rh.lighter.xyz': 'USDG'}
_TOKEN = re.compile(r'ro:([0-9]+):(single|all):([0-9]+):[0-9a-fA-F]+')
_DECIMAL = re.compile(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)')
_CURRENCY_FIELDS = ('currency', 'asset', 'quote_currency', 'settlement_currency')
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class LighterCredentialError(ValueError):
    """A safe, actionable error that never includes credential values."""


def validate_history_token(account_index: Any, token: Any, now: datetime) -> int:
    if not isinstance(account_index, str) or not re.fullmatch(r'[0-9]{1,19}', account_index):
        raise LighterCredentialError('invalid account_index for read_only_token')
    index = int(account_index)
    match = _TOKEN.fullmatch(token) if isinstance(token, str) else None
    if index > 2**63-1 or match is None:
        raise LighterCredentialError('missing or invalid read_only_token (ro token required)')
    # An all-subaccounts token's root is NOT evidence that a different account
    # is its child. Until membership is independently verified, bind to its
    # explicit index only, just as for single-account scope.
    if int(match[1]) != index:
        raise LighterCredentialError('read_only_token account does not match account_index')
    if int(match[3]) <= _epoch_us(now) // 1_000_000:
        raise LighterCredentialError('read_only_token expired; replace it')
    return index


def _epoch_us(value: datetime) -> int:
    delta = value.astimezone(timezone.utc) - _EPOCH
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def _number(value: Any) -> Decimal:
    if not isinstance(value, str) or not _DECIMAL.fullmatch(value):
        raise ValueError('invalid Lighter decimal')
    return Decimal(value)


def _add(left: Decimal, right: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = max(left.adjusted(), right.adjusted(), 0) - min(
            int(left.as_tuple().exponent), int(right.as_tuple().exponent), 0,
        ) + 2
        return left + right


def _envelope(payload: Any, quote: str) -> None:
    if (not isinstance(payload, dict) or type(payload.get('code')) is not int or payload['code'] != 200
            or any(key in payload for key in ('error', 'error_message'))
            or any(payload[key] != quote for key in _CURRENCY_FIELDS if key in payload)):
        raise ValueError('unexpected Lighter response')


def _market_names(payload: Any, quote: str) -> dict[int, str]:
    _envelope(payload, quote)
    rows = payload.get('order_book_details')
    if not isinstance(rows, list):
        raise ValueError('invalid Lighter market metadata')
    names: dict[int, str] = {}
    for item in rows:
        if not isinstance(item, dict):
            raise ValueError('invalid Lighter market metadata')
        market, symbol = item.get('market_id'), item.get('symbol')
        if (type(market) is not int or not 0 <= market <= 32767
                or not isinstance(symbol, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,63}', symbol)
                or any(item[key] != quote for key in _CURRENCY_FIELDS if key in item)):
            raise ValueError('invalid Lighter market metadata')
        if market in names and names[market] != symbol:
            raise ValueError('conflicting Lighter market metadata')
        names[market] = symbol
    return names


async def fetch_lighter_funding(
    store: CredentialStore,
    refs: list[str],
    start: datetime,
    end: datetime,
    request_json: Callable[..., Awaitable[Any]],
    api_base: str,
    *,
    now: datetime | None = None,
) -> dict[str, Decimal]:
    base = api_base.rstrip('/')
    if base not in ORIGINS:
        raise ValueError('untrusted Lighter origin')
    if start.utcoffset() is None or end.utcoffset() is None or start >= end:
        raise ValueError('invalid Lighter window')
    low, high = _epoch_us(start), _epoch_us(end)
    if low < 1_000_000_000_000_000:
        raise ValueError('invalid Lighter epoch')
    effective_now = now or datetime.now(timezone.utc)
    if effective_now.utcoffset() is None:
        raise ValueError('invalid Lighter clock')
    accounts: dict[int, str] = {}
    for ref in refs:
        venue = ref.split(':', 1)[0]
        expected = {'lighter': 'https://mainnet.zklighter.elliot.ai', 'lighter-rh': 'https://api.rh.lighter.xyz'}
        if expected.get(venue) != base:
            raise ValueError('Lighter deployment does not match credential profile')
        credentials = store.get_exchange_credentials(ref)
        token = credentials.get('read_only_token')
        index = validate_history_token(credentials.get('account_index'), token, effective_now)
        assert isinstance(token, str)  # validated above, keep credentials out of diagnostics
        accounts.setdefault(index, token)

    requests = 0

    async def get(path: str, **kwargs: Any) -> Any:
        nonlocal requests
        if requests >= MAX_REQUESTS:
            raise ValueError('Lighter request budget exhausted')
        if requests:
            await sleep(0.51)
        requests += 1
        return await request_json('GET', base + '/api/v1/' + path, follow_redirects=False, **kwargs)

    totals: dict[int, Decimal] = {}
    for index, token in accounts.items():
        cursor = None
        cursors: set[str] = set()
        seen: dict[tuple[int, int], tuple] = {}
        while True:
            params = dict(account_index=index, limit=100, side='all')
            if cursor is not None:
                params['cursor'] = cursor
            payload = await get('positionFunding', params=params, headers={'Authorization': token})
            _envelope(payload, ORIGINS[base])
            rows = payload.get('position_fundings')
            if not isinstance(rows, list) or len(rows) > 100:
                raise ValueError('invalid Lighter funding page')
            for item in rows:
                if not isinstance(item, dict):
                    raise ValueError('invalid Lighter funding record')
                market, event, timestamp = (item.get(key) for key in ('market_id', 'funding_id', 'timestamp'))
                if (type(market) is not int or not 0 <= market <= 32767
                        or type(event) is not int or not 0 <= event <= 2**63-1
                        or type(timestamp) is not int or not 1_000_000_000 <= timestamp < 10_000_000_000
                        or item.get('position_side') not in ('long', 'short')
                        or any(item[key] != ORIGINS[base] for key in _CURRENCY_FIELDS if key in item)):
                    raise ValueError('incomplete Lighter funding record')
                change, discount, rate, size = (_number(item.get(key)) for key in (
                    'change', 'discount', 'rate', 'position_size',
                ))
                key = (market, event)
                record = (timestamp, change, discount, rate, size, item['position_side'])
                if key in seen:
                    if seen[key] != record:
                        raise ValueError('conflicting Lighter funding event')
                    continue
                seen[key] = record
                # Actual signed change is already the account payout. Do not
                # add discount or reconstruct it from rate/size/position side.
                if low <= timestamp * 1_000_000 < high:
                    totals[market] = _add(totals.get(market, Decimal('0')), change)
            cursor = payload.get('next_cursor', '')
            if not isinstance(cursor, str):
                raise ValueError('invalid Lighter cursor')
            if not cursor:
                break
            if cursor in cursors:
                raise ValueError('repeated Lighter cursor')
            cursors.add(cursor)

    names: dict[int, str] = {}
    if totals and requests < MAX_REQUESTS:
        try:
            names = _market_names(await get('orderBookDetails'), ORIGINS[base])
        except Exception:
            # Metadata is presentation only; closed/unlisted markets keep a
            # stable deployment-local market identity, never a guessed ticker.
            pass
    result: dict[str, Decimal] = {}
    for market, amount in totals.items():
        symbol = names.get(market, f'market#{market}')
        result[symbol] = _add(result.get(symbol, Decimal('0')), amount)
    return result
