"""GET-only Polymarket PERPS ledger, received-positive nominal USD accounting.

No position snapshots, signer/private keys, SDK sessions or credential creation.
See docs/polymarket-funding.md for contracts and live-verification limitations.
"""
from __future__ import annotations

import re
from asyncio import get_running_loop, timeout, wait_for
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode

from src.services.credential_store import CredentialStore

ORIGIN = 'https://api.perpetuals.polymarket.com'
MAX_REQUESTS = 256
PAGE_LIMIT = 100
REQUEST_TIMEOUT = 15.0
TOTAL_TIMEOUT = 120.0
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_ADDRESS = re.compile(r'0x[0-9a-fA-F]{40}')
_DECIMAL = re.compile(r'-?[0-9]+(?:\.[0-9]+)?')
_LABEL = re.compile(r'[A-Z][A-Z0-9._-]{0,63}')


def _milliseconds(value: datetime) -> int:
    delta = value.astimezone(timezone.utc) - _EPOCH
    return (delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000


def _address(value: Any) -> str:
    if not isinstance(value, str) or _ADDRESS.fullmatch(value) is None:
        raise ValueError('invalid Polymarket account identity')
    return value.lower()


def _decimal(value: Any) -> Decimal:
    if not isinstance(value, str) or len(value) > 1024 or _DECIMAL.fullmatch(value) is None:
        raise ValueError('invalid Polymarket funding decimal')
    return Decimal(value)


def _add(left: Decimal, right: Decimal) -> Decimal:
    """Exact coefficient addition, independent of caller precision/traps."""
    def parts(value: Decimal) -> tuple[int, int]:
        sign, digits, exponent = value.as_tuple()
        return (-1 if sign else 1) * int(''.join(map(str, digits))), int(exponent)

    a, ae = parts(left)
    b, be = parts(right)
    exponent = min(ae, be)
    result = a * 10 ** (ae - exponent) + b * 10 ** (be - exponent)
    return Decimal((int(result < 0), tuple(map(int, str(abs(result)))), exponent))


def _record(item: Any) -> tuple[int, int, int, Decimal, Decimal, Decimal]:
    if not isinstance(item, dict):
        raise ValueError('invalid Polymarket funding record')
    ident, market, event = item.get('id'), item.get('instrument_id'), item.get('timestamp')
    if type(ident) is not int or ident < 0:
        raise ValueError('invalid Polymarket funding ID')
    if type(market) is not int or not 0 <= market <= 4294967295:
        raise ValueError('invalid Polymarket instrument ID')
    if type(event) is not int or not 0 <= event < 253402300800000:
        raise ValueError('invalid Polymarket funding timestamp')
    if item.get('funding_asset') != 'pUSD':
        raise ValueError('unsupported Polymarket funding settlement asset')
    return (ident, market, event, _decimal(item.get('funding')),
            _decimal(item.get('funding_rate')), _decimal(item.get('size')))


def _labels(payload: Any) -> dict[int, str]:
    """Display-only metadata: missing/unsafe/ambiguous entries retain raw IDs."""
    if not isinstance(payload, list):
        return {}
    labels: dict[int, str] = {}
    ambiguous = set()
    for item in payload:
        if not isinstance(item, dict):
            continue
        market = item.get('instrument_id')
        if type(market) is not int or not 0 <= market <= 4294967295:
            continue
        if item.get('quote_asset') != 'pUSD' or item.get('instrument_type') != 'perpetual':
            continue
        display = item.get('display_symbol') or item.get('symbol')
        label = display.removesuffix('-USD') if isinstance(display, str) else None
        if label is None or _LABEL.fullmatch(label) is None:
            continue
        if market in labels and labels[market] != label:
            ambiguous.add(market)
        labels[market] = label
    return {market: label for market, label in labels.items() if market not in ambiguous}


async def fetch_polymarket_funding(
    store: CredentialStore,
    refs: list[str],
    start: datetime,
    end: datetime,
    request_json: Callable[..., Awaitable[Any]],
    api_base: str,
) -> dict[str, Decimal]:
    """Return complete received-positive USD totals (pUSD settlement) for [start,end).

    All refs preflight before IO. Any required account/page failure raises a
    sanitized ValueError and invalidates the entire venue, never a partial sum.
    Caller transport MUST raise on unsuccessful HTTP statuses (including 3xx)
    and honor follow_redirects=False. Only public display metadata is optional.
    """
    if api_base not in (ORIGIN, ORIGIN + '/'):
        raise ValueError('untrusted Polymarket origin')
    if (not isinstance(start, datetime) or not isinstance(end, datetime)
            or start.utcoffset() is None or end.utcoffset() is None or start >= end
            or start.microsecond % 1000 or end.microsecond % 1000):
        raise ValueError('invalid Polymarket funding window')
    lower, upper = _milliseconds(start), _milliseconds(end)
    if lower < 0:
        raise ValueError('invalid Polymarket funding window')
    accounts: dict[str, tuple[str, str]] = {}
    for ref in refs:
        try:
            credentials = store.get_exchange_credentials(ref)
            if not isinstance(credentials, dict):
                raise ValueError
            proxy, secret = credentials.get('proxy_address'), credentials.get('proxy_secret')
            if not isinstance(proxy, str):
                raise ValueError
            address = _address(proxy)
            if (not isinstance(secret, str) or not 1 <= len(secret) <= 4096
                    or any(not 33 <= ord(char) <= 126 for char in secret)):
                raise ValueError
        except Exception:
            raise ValueError('invalid Polymarket funding credentials') from None
        if address in accounts and accounts[address][1] != secret:
            raise ValueError('conflicting Polymarket credential aliases')
        accounts.setdefault(address, (proxy, secret))

    totals: dict[int, Decimal] = {}
    seen: dict[tuple[str, int, int], tuple[int, Decimal, Decimal, Decimal]] = {}
    requests = 0
    deadline = get_running_loop().time() + TOTAL_TIMEOUT

    async def get(path: str, headers: dict[str, str]) -> Any:
        nonlocal requests
        if requests >= MAX_REQUESTS:
            raise ValueError('Polymarket funding request budget exhausted')
        remaining = deadline - get_running_loop().time()
        if remaining <= 0:
            raise ValueError('Polymarket funding deadline exceeded')
        requests += 1
        try:
            return await wait_for(request_json(
                'GET', ORIGIN + path, headers=headers, follow_redirects=False,
                timeout=min(REQUEST_TIMEOUT, remaining),
            ), timeout=min(REQUEST_TIMEOUT, remaining))
        except Exception:
            raise ValueError('Polymarket funding request failed') from None

    try:
        async with timeout(TOTAL_TIMEOUT):
            for proxy, secret in accounts.values():
                headers = {'polymarket-proxy': proxy, 'polymarket-secret': secret}
                identity = await get('/v1/account/credentials', headers)
                if not isinstance(identity, dict):
                    raise ValueError('invalid Polymarket account identity')
                owner = _address(identity.get('address'))
                keys = identity.get('keys')
                if not isinstance(keys, list) or not keys:
                    raise ValueError('invalid Polymarket account identity')
                bound_proxies = set()
                for key in keys:
                    if (not isinstance(key, dict) or type(key.get('expiry')) is not int
                            or key['expiry'] < 0):
                        raise ValueError('invalid Polymarket account identity')
                    bound_proxies.add(_address(key.get('proxy')))
                if _address(proxy) not in bound_proxies:
                    raise ValueError('invalid Polymarket account identity')
                observed: dict[tuple[int, int], tuple[int, Decimal, Decimal, Decimal]] = {}
                complete: set[tuple[int, int]] = set()
                # Inclusive bounds are documented in the official TypeScript SDK.
                # Split capped ranges, never skip a timestamp with unresolved ties.
                pending = [(lower, upper)]
                while pending:
                    lo, hi = pending.pop()
                    payload = await get('/v1/account/funding?' + urlencode({
                        'start_timestamp': lo, 'end_timestamp': hi,
                    }), headers)
                    if (not isinstance(payload, dict) or set(payload) != {'data', 'more'}
                            or type(payload.get('more')) is not bool
                            or not isinstance(payload.get('data'), list)
                            or len(payload['data']) > PAGE_LIMIT):
                        raise ValueError('unexpected Polymarket funding history')
                    records = [_record(item) for item in payload['data']]
                    identities = [(market, ident) for ident, market, *_ in records]
                    if len(set(identities)) != len(identities):
                        raise ValueError('duplicate Polymarket funding page records')
                    for ident, market, event, amount, rate, size in records:
                        if (lo, hi) != (lower, upper) and not lo <= event <= hi:
                            raise ValueError('Polymarket funding pagination range mismatch')
                        local_key = (market, ident)
                        fingerprint = (event, amount, rate, size)
                        previous = observed.get(local_key, seen.get((owner, market, ident)))
                        if previous is not None and previous != fingerprint:
                            raise ValueError('conflicting Polymarket funding account event')
                        observed[local_key] = fingerprint
                    if payload['more']:
                        if not records or lo == hi:
                            raise ValueError('unresolved Polymarket funding pagination saturation')
                        middle = (lo + hi) // 2
                        pending.extend([(middle + 1, hi), (lo, middle)])
                        continue
                    # Capped parents are observed but never booked: only complete
                    # leaves enter the account-scoped economic-event deduplicator.
                    for ident, market, event, amount, rate, size in records:
                        complete.add((market, ident))
                        key = (owner, market, ident)
                        fingerprint = (event, amount, rate, size)
                        if key in seen:
                            if seen[key] != fingerprint:
                                raise ValueError('conflicting Polymarket funding account event')
                            continue
                        seen[key] = fingerprint
                        if lower <= event < upper:
                            # OpenAPI fund = "Funding paid in USD"; negate the
                            # paid-positive ledger to our received-positive report.
                            # See the funding guide's positive-rate/long pays table.
                            totals[market] = _add(totals.get(market, Decimal(0)), amount.copy_negate())
                if not observed.keys() <= complete:
                    raise ValueError('Polymarket funding pagination missing observed events')
    except TimeoutError:
        raise ValueError('Polymarket funding deadline exceeded') from None

    labels = {}
    if totals and requests < MAX_REQUESTS:
        try:
            labels = _labels(await get('/v1/info/instruments', {}))
        except Exception:
            pass  # Optional public metadata must never discard valid financial data.
    result: dict[str, Decimal] = {}
    for market, amount in totals.items():
        label = labels.get(market, str(market))
        result[label] = _add(result.get(label, Decimal(0)), amount)
    return result
