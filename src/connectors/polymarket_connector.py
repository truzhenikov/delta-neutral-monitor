"""Polymarket PERPS only: fixed-origin, GET-only delegated-credential reads.

Amounts in the legacy *_usd model are nominal pUSD, not an FX conversion.
"""
from __future__ import annotations

import asyncio
import math
import re
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from src.config import get_settings
from src.connectors.base import ExchangeConnector
from src.connectors.real_connectors import (
    RealConnectorNotConfiguredError,
    RealConnectorRequestError,
    _runtime_credentials,
)
from src.core.models import AccountSnapshot, Position

ORIGIN = 'https://api.perpetuals.polymarket.com'
PATHS = frozenset(('/v1/account/portfolio', '/v1/info/tickers'))
LABEL = 'Polymarket Perps'


def validate_credentials(credentials: dict[str, str]) -> dict[str, str]:
    """REST reads need only the existing proxy address and secret, never keys."""
    address = credentials.get('proxy_address')
    secret = credentials.get('proxy_secret')
    if (set(credentials) - {'proxy_address', 'proxy_secret'}
            or not isinstance(address, str) or not re.fullmatch(r'0x[0-9a-fA-F]{40}', address)
            or not isinstance(secret, str) or not secret or len(secret) > 4096
            or any(ord(c) < 33 or ord(c) > 126 for c in secret)):
        raise RealConnectorNotConfiguredError('polymarket requires proxy_address and proxy_secret; no private keys')
    return {'polymarket-proxy': address, 'polymarket-secret': secret}


def _number(row: dict, field: str, *, nonnegative: bool = False) -> float:
    try:
        raw = row[field]
        if isinstance(raw, bool) or not isinstance(raw, (str, int, float, Decimal)):
            raise ValueError
        value = Decimal(str(raw))
        result = float(value)
        if not value.is_finite() or not math.isfinite(result) or (nonnegative and value < 0):
            raise ValueError
        return result
    except (KeyError, ValueError, TypeError, InvalidOperation, OverflowError):
        raise RealConnectorRequestError(f'polymarket invalid {field}') from None


def _timestamp(row: dict) -> datetime:
    value = row.get('timestamp')
    if isinstance(value, bool) or not isinstance(value, int):
        raise RealConnectorRequestError('polymarket invalid timestamp')
    now = time.time() * 1000
    if not now - 120000 <= value <= now + 30000:
        raise RealConnectorRequestError('polymarket stale or future timestamp')
    return datetime.fromtimestamp(value / 1000, timezone.utc)


def _identity(row: dict) -> tuple[int, str]:
    iid, symbol = row.get('instrument_id'), row.get('symbol')
    if (isinstance(iid, bool) or not isinstance(iid, int) or iid <= 0
            or not isinstance(symbol, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}', symbol)):
        raise RealConnectorRequestError('polymarket invalid instrument identity')
    return iid, symbol


class PolymarketRealConnector(ExchangeConnector):
    exchange = 'polymarket'
    label = LABEL

    async def _query(self, client: httpx.AsyncClient, path: str, headers: dict) -> Any:
        if path not in PATHS:
            raise RealConnectorRequestError('polymarket unsupported read endpoint')
        try:
            response = await client.get(ORIGIN + path, headers=headers, follow_redirects=False)
            if response.status_code != 200:
                raise RealConnectorRequestError(f'polymarket HTTP {response.status_code}')
            return response.json()
        except (httpx.HTTPError, ValueError):
            raise RealConnectorRequestError('polymarket request or JSON response failed') from None

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        settings = get_settings()
        credentials = _runtime_credentials(self.exchange)
        if ':' not in self.exchange and not credentials:
            credentials = {'proxy_address': settings.polymarket_proxy_address,
                           'proxy_secret': settings.polymarket_proxy_secret}
        headers = validate_credentials(credentials)
        timeout = settings.request_timeout_sec
        if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
            raise RealConnectorNotConfiguredError('polymarket invalid request timeout')
        try:
            async with asyncio.timeout(timeout):
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                    account = await self._query(client, '/v1/account/portfolio', headers)
                    # Public price reads never receive private account headers.
                    quotes = await self._query(client, '/v1/info/tickers', {})
                return self._snapshot(account, quotes)
        except TimeoutError:
            raise RealConnectorRequestError('polymarket account deadline exceeded') from None

    def _snapshot(self, account: Any, quotes: Any) -> AccountSnapshot:
        if (not isinstance(account, dict) or not isinstance(account.get('margin'), dict)
                or not isinstance(account.get('positions'), list) or not isinstance(quotes, list)
                or not isinstance(account.get('in_liquidation'), bool)):
            raise RealConnectorRequestError('polymarket malformed portfolio')
        if account['in_liquidation']:
            raise RealConnectorRequestError('polymarket account in liquidation')
        updated_at = _timestamp(account)
        margin = account['margin']
        equity = _number(margin, 'total_account_value')
        available = _number(margin, 'available_order_margin')
        maintenance = _number(margin, 'total_maintenance_margin', nonnegative=True)
        for field in ('total_initial_margin', 'total_position_value'):
            _number(margin, field, nonnegative=True)
        _number(account, 'withdrawable', nonnegative=True)
        marks = {}
        symbols = set()
        for row in quotes:
            if not isinstance(row, dict):
                raise RealConnectorRequestError('polymarket malformed ticker')
            iid, symbol = _identity(row)
            if iid in marks or symbol in symbols:
                raise RealConnectorRequestError('polymarket duplicate ticker')
            symbols.add(symbol)
            _timestamp(row)
            mark = _number(row, 'mark_price', nonnegative=True)
            if mark <= 0:
                raise RealConnectorRequestError('polymarket invalid mark price')
            marks[iid] = symbol, mark
        positions = []
        seen_ids, seen_symbols = set(), set()
        for row in account['positions']:
            if not isinstance(row, dict):
                raise RealConnectorRequestError('polymarket malformed position')
            iid, symbol = _identity(row)
            if iid in seen_ids or symbol in seen_symbols:
                raise RealConnectorRequestError('polymarket duplicate position')
            seen_ids.add(iid)
            seen_symbols.add(symbol)
            qty = _number(row, 'size')
            entry = _number(row, 'entry_price', nonnegative=True)
            leverage = _number(row, 'leverage', nonnegative=True)
            for field in ('initial_margin', 'maintenance_margin', 'position_value'):
                _number(row, field, nonnegative=True)
            for field in ('unrealized_pnl', 'cumulative_funding'):
                _number(row, field)
            if not isinstance(row.get('cross'), bool):
                raise RealConnectorRequestError('polymarket invalid margin mode')
            liq = None if row.get('liquidation_price') is None else _number(row, 'liquidation_price', nonnegative=True)
            if qty == 0:
                continue
            if iid not in marks or marks[iid][0] != symbol or entry <= 0 or leverage <= 0:
                raise RealConnectorRequestError('polymarket missing mark or invalid position')
            mark = marks[iid][1]
            if not all(math.isfinite(v) for v in (abs(qty) * mark, abs(qty) * entry, (mark - entry) * qty)):
                raise RealConnectorRequestError('polymarket position numeric overflow')
            positions.append(Position(exchange=self.exchange, symbol=symbol,
                side='long' if qty > 0 else 'short', size=abs(qty), entry_price=entry,
                mark_price=mark, leverage=leverage, liquidation_price=liq if liq else None))
        # Account value already includes unrealized PnL and funding effects.
        return AccountSnapshot(exchange=self.exchange, equity_usd=equity,
            available_margin_usd=available, maintenance_margin_usd=maintenance,
            positions=positions, updated_at=updated_at)
