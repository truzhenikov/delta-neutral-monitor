"""Actual Orderly funding accruals (USDC), positive means received.

No positions, rates, or current cumulative counters are used. Accrued and Settled
are lifecycle states of the same historical fee, not two independent payments.
"""
from __future__ import annotations

import base64
import re
from asyncio import sleep
from datetime import datetime, timezone
from decimal import Decimal, DecimalException, localcontext
from time import time_ns
from typing import Any, Awaitable, Callable, cast
from urllib.parse import urlencode

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.connectors.woofi_connector import _base58_decode
from src.services.credential_store import CredentialStore

_BASE = 'https://api.orderly.org'
_PATH = '/v1/funding_fee/history'
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_SYMBOL = re.compile(r'PERP_([A-Z0-9]+)_USDC')
_PAGE_SIZE = 60
MAX_REQUESTS = 256


async def fetch_woofi_funding(
    store: CredentialStore,
    refs: list[str],
    start: datetime,
    end: datetime,
    request_json: Callable[..., Awaitable[Any]],
    api_base: str,
) -> dict[str, Decimal]:
    """Return exact [start,end) ledger-created-time totals, or raise ValueError.

    Boundaries must be aware and millisecond aligned. The query includes the end
    millisecond conservatively; local filtering excludes it. request_json must
    reject non-2xx responses and decode JSON numbers with parse_float=Decimal;
    binary floats are rejected, never silently repaired. No default-profile or
    settings credentials are consulted. Call once per report for all profiles.
    """
    if api_base != _BASE:
        raise ValueError('untrusted WOOFi API base')
    if (start.utcoffset() is None or end.utcoffset() is None or start >= end
            or start.microsecond % 1000 or end.microsecond % 1000):
        raise ValueError('invalid WOOFi funding window')
    bounds = []
    for boundary in (start, end):
        delta = boundary.astimezone(timezone.utc) - _EPOCH
        bounds.append((delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000)
    low, high = bounds
    accounts: dict[str, tuple[str, Ed25519PrivateKey]] = {}
    for ref in refs:
        try:
            credentials = store.get_exchange_credentials(ref)
            if not isinstance(credentials, dict):
                raise ValueError
            account, key, secret = (credentials.get(name) for name in ('account_id', 'orderly_key', 'orderly_secret'))
            if any(not isinstance(v, str) or not v or v != v.strip() for v in (account, key, secret)):
                raise ValueError
            account, key, secret = cast(str, account), cast(str, key), cast(str, secret)
            signer = Ed25519PrivateKey.from_private_bytes(_base58_decode(secret))
        except Exception:
            raise ValueError('missing or invalid WOOFi credentials') from None
        # Account ID, not API key, is the ledger identity; several keys can
        # legitimately grant access to the very same account-wide history.
        accounts.setdefault(account, (key if key.startswith('ed25519:') else 'ed25519:' + key, signer))

    totals: dict[str, Decimal] = {}
    requests = 0
    for account, (key, signer) in accounts.items():
        page, expected_total = 1, None
        seen: set[tuple[str, int, str]] = set()
        while True:
            if requests >= MAX_REQUESTS:
                raise ValueError('WOOFi funding request budget exhausted')
            if requests:
                # Documented 20 requests / 60 s / user; shared across profiles.
                await sleep(3.05)
            path = _PATH + '?' + urlencode(dict(start_t=low, end_t=high, page=page, size=_PAGE_SIZE))
            timestamp = str(time_ns() // 1_000_000)
            signature = signer.sign((timestamp + 'GET' + path).encode())
            headers = {'Content-Type': 'application/x-www-form-urlencoded',
                       'orderly-account-id': account, 'orderly-key': key,
                       'orderly-timestamp': timestamp,
                       'orderly-signature': base64.urlsafe_b64encode(signature).decode().rstrip('=')}
            requests += 1
            try:
                payload = await request_json('GET', _BASE + path, headers=headers, follow_redirects=False)
            except Exception:
                raise ValueError('WOOFi funding request failed') from None
            if not isinstance(payload, dict) or payload.get('success') is not True:
                raise ValueError('unexpected WOOFi funding response')
            data = payload.get('data')
            if not isinstance(data, dict) or not isinstance(data.get('rows'), list) or not isinstance(data.get('meta'), dict):
                raise ValueError('missing WOOFi funding history')
            rows, meta = data['rows'], data['meta']
            total = meta.get('total')
            if (any(type(meta.get(k)) is not int for k in ('total', 'current_page', 'records_per_page'))
                    or total < 0 or meta['current_page'] != page or meta['records_per_page'] != _PAGE_SIZE
                    or (expected_total is not None and expected_total != total)):
                raise ValueError('inconsistent WOOFi funding pagination')
            expected_total = total
            remaining = total - (page - 1) * _PAGE_SIZE
            if remaining < 0 or len(rows) != min(_PAGE_SIZE, remaining):
                raise ValueError('incomplete WOOFi funding page')
            for item in rows:
                if not isinstance(item, dict):
                    raise ValueError('invalid WOOFi funding record')
                symbol, raw_fee, raw_time = (item.get(k) for k in ('symbol', 'funding_fee', 'created_time'))
                match = _SYMBOL.fullmatch(symbol) if isinstance(symbol, str) else None
                if (not match or type(raw_fee) not in (str, int, Decimal)
                        or type(raw_time) not in (str, int, Decimal)
                        or item.get('status') not in ('Accrued', 'Settled')
                        or item.get('margin_mode') not in ('CROSS', 'ISOLATED')):
                    raise ValueError('incomplete WOOFi funding record')
                for field in ('currency', 'asset', 'settle_currency'):
                    if field in item and item[field] != 'USDC':
                        raise ValueError('contradictory WOOFi funding currency')
                try:
                    fee = Decimal(cast(str | int | Decimal, raw_fee))
                    event_time = Decimal(cast(str | int | Decimal, raw_time))
                except DecimalException:
                    raise ValueError('invalid WOOFi funding decimal') from None
                if (not fee.is_finite() or not event_time.is_finite() or event_time <= 0
                        or event_time != event_time.to_integral_value()):
                    raise ValueError('invalid WOOFi funding amount or time')
                direction = item.get('payment_type')
                if ((fee > 0 and direction != 'Pay') or (fee < 0 and direction != 'Receive')
                        or (fee == 0 and direction not in ('Pay', 'Receive', None))):
                    raise ValueError('inconsistent WOOFi funding direction')
                identity = (cast(str, symbol), int(event_time), item['margin_mode'])
                if identity in seen:
                    # No stable record IDs are exposed: ambiguity must not
                    # silently double count or silently discard real payments.
                    raise ValueError('overlapping WOOFi funding records')
                seen.add(identity)
                if not low <= event_time < high:
                    continue
                amount = fee.copy_negate()  # Unary minus would round under a low caller context.
                previous = totals.get(match[1], Decimal('0'))
                with localcontext() as ctx:
                    ctx.prec = max(28, max(previous.adjusted(), amount.adjusted())
                                   - min(int(previous.as_tuple().exponent), int(amount.as_tuple().exponent)) + 2)
                    try:
                        result = previous + amount
                    except DecimalException:
                        raise ValueError('invalid WOOFi funding total') from None
                if not result.is_finite():
                    raise ValueError('non-finite WOOFi funding total')
                totals[match[1]] = result
            if page * _PAGE_SIZE >= total:
                break
            page += 1
    return totals
