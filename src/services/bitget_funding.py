"""Read-only actual Bitget UTA funding ledger wired into the shared report."""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode

from src.connectors.real_connectors import BitgetRealConnector
from src.services.credential_store import CredentialStore

FUNDING_TYPES = (
    'CONTRACT_MAIN_SETTLE_FEE_USER_IN', 'CONTRACT_MAIN_SETTLE_FEE_USER_OUT',
    'MARGIN_SETTLE_FEE_USER_IN', 'MARGIN_SETTLE_FEE_USER_OUT',
)


async def fetch_bitget_funding(
    store: CredentialStore,
    refs: list[str],
    start: datetime,
    end: datetime,
    request_json: Callable[..., Awaitable[Any]],
    api_base: str,
    *,
    account_mode: str = 'uta',
    now: datetime | None = None,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    max_pages: int = 256,
) -> dict[str, Decimal]:
    """Signed nominal USD funding by underlying for completed [start, end).

    Only UTA is supported: classic requests fail explicitly, never fall back.
    Transport must raise HTTP errors and honor follow_redirects=False. Any
    failure raises; callers must discard this entire venue's partial result.
    ``max_pages`` is a global request budget across profiles/products/types.
    """
    if api_base not in ('https://api.bitget.com', 'https://api.bitget.com/'):
        raise ValueError('untrusted Bitget origin')
    if account_mode != 'uta':
        raise ValueError('Bitget funding supports UTA only; classic mode unsupported')
    now = datetime.now(timezone.utc) if now is None else now
    if any(v.tzinfo is None or v.utcoffset() is None for v in (start, end, now)):
        raise ValueError('Bitget funding window requires timezones')
    if (not now - timedelta(days=90) <= start < end <= now
            or end - start > timedelta(days=30)):
        raise ValueError('Bitget funding window outside completed 90-day/30-day limits')
    if start.microsecond % 1000 or end.microsecond % 1000:
        raise ValueError('Bitget funding window requires millisecond alignment')
    if type(max_pages) is not int or not 1 <= max_pages <= 256:
        raise ValueError('invalid Bitget request budget')
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    start_ms = (start - epoch) // timedelta(milliseconds=1)
    end_ms = (end - epoch) // timedelta(milliseconds=1)
    # Validate every requested profile before any authenticated network access.
    # Key aliases are safe only when both signing secret and passphrase agree.
    profiles: dict[str, tuple[str, str]] = {}
    for ref in refs:
        credentials = store.get_exchange_credentials(ref)
        values = [credentials.get(name) for name in ('api_key', 'api_secret', 'api_passphrase')]
        if any(not isinstance(v, str) or not v.strip() for v in values):
            raise ValueError('missing Bitget profile credentials')
        api_key, secret, passphrase = (str(value) for value in values)
        if api_key in profiles and profiles[api_key] != (secret, passphrase):
            raise ValueError('conflicting Bitget profile credentials')
        profiles[api_key] = (secret, passphrase)

    signer = BitgetRealConnector()
    totals: dict[str, Decimal] = {}
    requests = 0
    for api_key, (secret, passphrase) in profiles.items():
        # IDs belong to this authenticated account, not a venue-global namespace.
        seen: dict[str, dict[str, Any]] = {}
        for currency in ('USDT', 'USDC'):
            category = currency + '-FUTURES'
            for funding_type in FUNDING_TYPES:
                cursor = ''
                cursors: set[str] = set()
                while True:
                    if requests >= max_pages:
                        raise ValueError('Bitget funding request budget exhausted')
                    params = dict(category=category, type=funding_type,
                                  startTime=str(start_ms), endTime=str(end_ms - 1), limit='100')
                    if cursor:
                        params['cursor'] = cursor
                    path = '/api/v3/account/financial-records'
                    if requests:
                        await sleep(0.51)
                    # Explicit profile credentials only; timestamp generated after pacing.
                    headers = signer._build_signed_headers(api_key, secret, passphrase,
                                                           'GET', path, params=params)
                    requests += 1
                    try:
                        payload = await request_json('GET', api_base.rstrip('/') + path + '?' + urlencode(params),
                                                     headers=headers, follow_redirects=False)
                    except Exception:
                        raise ValueError('Bitget UTA funding transport failed') from None
                    if not isinstance(payload, dict) or payload.get('code') != '00000':
                        raise ValueError('Bitget UTA funding API error (check account mode/permission)')
                    data = payload.get('data')
                    if (not isinstance(data, dict) or not isinstance(data.get('list'), list)
                            or len(data['list']) > 100 or not isinstance(data.get('cursor'), str)):
                        raise ValueError('invalid Bitget UTA funding page schema')
                    rows = data['list']
                    next_cursor = data['cursor']
                    new_ids = 0
                    for item in rows:
                        if not isinstance(item, dict):
                            raise ValueError('invalid Bitget funding record')
                        event_id = item.get('id')
                        timestamp = item.get('ts')
                        if (not isinstance(event_id, str) or not re.fullmatch(r'[0-9]+', event_id)
                                or not isinstance(timestamp, str) or not re.fullmatch(r'[0-9]+', timestamp)):
                            raise ValueError('invalid Bitget funding ID/timestamp')
                        if item.get('category') != category or item.get('type') != funding_type:
                            raise ValueError('unexpected Bitget funding category/type')
                        if item.get('coin') != currency:
                            raise ValueError('unsupported Bitget funding settlement coin')
                        symbol = item.get('symbol')
                        if (not isinstance(symbol, str) or not symbol.endswith(currency)
                                or not re.fullmatch(r'[A-Z0-9_]+', symbol[:-len(currency)])):
                            raise ValueError('invalid Bitget funding symbol')
                        if ('positionType' in item and item['positionType'] not in ('crossed', 'isolated')):
                            raise ValueError('invalid Bitget funding position type')
                        if 'posSide' in item and item['posSide'] not in ('long', 'short'):
                            raise ValueError('invalid Bitget funding position side')
                        raw_amount = item.get('amount')
                        if (not isinstance(raw_amount, str)
                                or not re.fullmatch(r'-?[0-9]+(?:\.[0-9]+)?', raw_amount)):
                            raise ValueError('invalid Bitget funding amount')
                        amount = Decimal(raw_amount)
                        if ((funding_type.endswith('_IN') and amount < 0)
                                or (funding_type.endswith('_OUT') and amount > 0)):
                            raise ValueError('Bitget funding amount sign conflicts with type')
                        if event_id in seen:
                            if seen[event_id] != item:
                                raise ValueError('conflicting Bitget funding record')
                            continue
                        seen[event_id] = item
                        new_ids += 1
                        if start_ms <= int(timestamp) < end_ms:
                            underlying = symbol[:-len(currency)]
                            previous = totals.get(underlying, Decimal(0))
                            # Exact addition independently of caller Decimal precision.
                            exponent = min(int(previous.as_tuple().exponent), int(amount.as_tuple().exponent))
                            precision = max(previous.adjusted(), amount.adjusted()) - exponent + 3
                            with localcontext() as context:
                                context.prec = max(precision, 1)
                                totals[underlying] = previous + amount
                    if rows and not new_ids:
                        raise ValueError('nonprogress Bitget funding records')
                    if next_cursor:
                        if next_cursor in cursors:
                            raise ValueError('nonprogress Bitget funding cursor')
                        cursors.add(next_cursor)
                        cursor = next_cursor
                    else:
                        if len(rows) == 100:
                            raise ValueError('Bitget full funding page missing continuation cursor')
                        break
    return totals
