"""Rise public account funding ledger (USDC), including closed positions.

Both settled payments and unsettled hourly accruals are venue-calculated account
amounts, not rate estimates. Unsettled is not realized cash. Attribution follows
API block_time (settlement time when settled, period end otherwise). The native
sign is paid-positive; return received-positive Decimal.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from typing import Any, Awaitable, Callable

from src.services.credential_store import CredentialStore

MAX_PAGES = 256  # Global request budget across all unique enabled accounts.
LIMIT = 1000
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _ns(value: datetime) -> int:
    delta = value.astimezone(timezone.utc) - _EPOCH
    return ((delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds) * 1000


def _integer(value: Any) -> int:
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]+', value):
        raise ValueError('invalid Rise integer string')
    return int(value)


def _decimal(value: Any) -> Decimal:
    if not isinstance(value, str) or not re.fullmatch(r'[+-]?[0-9]+(?:\.[0-9]+)?', value):
        raise ValueError('invalid Rise decimal')
    return Decimal(value)


def _add(left: Decimal, right: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = max(left.adjusted(), right.adjusted(), 0) - min(
            int(left.as_tuple().exponent), int(right.as_tuple().exponent), 0,
        ) + 2
        return left + right


def _data(payload: Any) -> dict:
    if (not isinstance(payload, dict)
            or any(k in payload for k in ('error', 'code', 'message', 'status'))
            or ('success' in payload and payload['success'] is not True)
            or not isinstance(payload.get('data'), dict)):
        raise ValueError('unexpected Rise response')
    data = payload['data']
    if (any(k in data for k in ('error', 'code', 'message', 'status'))
            or ('success' in data and data['success'] is not True)):
        raise ValueError('Rise API error')
    return data


async def fetch_risex_funding(
    store: CredentialStore,
    refs: list[str],
    start: datetime,
    end: datetime,
    request_json: Callable[..., Awaitable[Any]],
    api_base: str,
) -> dict[str, Decimal]:
    if api_base.rstrip('/') != 'https://api.rise.trade':
        raise ValueError('untrusted Rise origin')
    if start.utcoffset() is None or end.utcoffset() is None or start >= end:
        raise ValueError('invalid Rise window')
    low, high = _ns(start), _ns(end)
    if low < 1_000_000_000_000_000_000:
        raise ValueError('invalid Rise epoch')
    accounts = []
    for ref in refs:
        account = store.get_exchange_credentials(ref).get('account')
        if not isinstance(account, str) or not re.fullmatch(r'0x[0-9a-fA-F]{40}', account):
            raise ValueError('invalid Rise public account')
        if account.lower() not in accounts:
            accounts.append(account.lower())
    if not accounts:
        return {}

    base = api_base.rstrip('/')
    metadata = _data(await request_json('GET', base + '/v1/markets', follow_redirects=False))
    if not isinstance(metadata.get('markets'), list) or not metadata['markets']:
        raise ValueError('missing Rise markets')
    symbols = {}
    for market in metadata['markets']:
        if not isinstance(market, dict):
            raise ValueError('invalid Rise market')
        market_id = _integer(market.get('market_id'))
        label = market.get('display_name')
        # Inactive markets remain necessary for closed-position history. Rise
        # appends a deprecation marker to the display name, not to the asset.
        match = re.fullmatch(r'(.+)/USDC(?: \[deprecated-[0-9]+\])?', label) if isinstance(label, str) else None
        if market.get('quote_asset_symbol') != 'USDC' or match is None or match[1].strip() != match[1]:
            raise ValueError('unsupported Rise market currency')
        symbol = match[1]
        if market_id in symbols and symbols[market_id] != symbol:
            raise ValueError('conflicting Rise market metadata')
        symbols[market_id] = symbol

    totals: dict[str, Decimal] = {}
    requests = 0
    for account in accounts:
        seen: dict[tuple, tuple] = {}
        page = 1
        while True:
            if requests >= MAX_PAGES:
                raise ValueError('Rise funding request budget exhausted')
            requests += 1
            data = _data(await request_json(
                'GET', base + '/v1/account/funding-payments', follow_redirects=False,
                params=dict(account=account, start_time=low, end_time=high - 1,
                            page=page, limit=LIMIT, sorted_by='-time'),
            ))
            # Ascending "time" is documented but returned a false empty page in
            # live production probes. Use the verified descending transport.
            returned_account = data.get('account')
            rows, more = data.get('records'), data.get('has_next_page')
            if (not isinstance(returned_account, str) or returned_account.lower() != account
                    or type(data.get('page')) is not int or data['page'] != page
                    or not isinstance(rows, list) or len(rows) > LIMIT or type(more) is not bool):
                raise ValueError('incomplete Rise funding page')
            new = 0
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError('invalid Rise funding record')
                market_id, timestamp = _integer(row.get('market_id')), _integer(row.get('block_time'))
                kind, settled = row.get('entry_type'), row.get('is_settled')
                if (market_id not in symbols or timestamp < 1_000_000_000_000_000_000
                        or kind not in ('funding', 'dividend') or type(settled) is not bool
                        or row.get('side') not in ('long', 'short')):
                    raise ValueError('incomplete Rise funding record')
                amount = _decimal(row.get('funding_payment'))
                size = _decimal(row.get('position_size'))
                block, tx = _integer(row.get('block_number')), row.get('tx_hash')
                if (not isinstance(tx, str) or (settled and (block <= 0 or not re.fullmatch(r'0x[0-9a-fA-F]{64}', tx)))
                        or (not settled and kind == 'funding' and (block != 0 or tx != ''))):
                    raise ValueError('invalid Rise settlement identity')
                rate = _decimal(row.get('funding_rate')) if kind == 'funding' else None
                # No event/log ID is exposed. At most one unambiguous economic
                # row per market/period/kind; conflicts fail closed, never merge
                # or double-count settlement transitions during pagination.
                key = (market_id, timestamp, kind)
                value = (amount, size, rate, settled, block, tx.lower(), row['side'])
                if key in seen:
                    if seen[key] != value:
                        raise ValueError('conflicting Rise funding period')
                    continue
                seen[key] = value
                new += 1
                if kind == 'funding' and low <= timestamp < high:
                    symbol = symbols[market_id]
                    totals[symbol] = _add(totals.get(symbol, Decimal('0')), amount.copy_negate())
            if not new and (rows or more):
                raise ValueError('Rise pagination made no progress')
            if not more:
                break
            page += 1
    return totals
