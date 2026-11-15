"""QFEX pinned REST contract; signed account GETs only (no WebSocket needed).

See docs/qfex.md for financial derivations and unverified private-live gates.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import math
import secrets
import time
from decimal import Decimal, InvalidOperation, localcontext
from uuid import UUID

import httpx

from src.config import get_settings
from src.connectors.base import ExchangeConnector
from src.connectors.real_connectors import (
    RealConnectorNotConfiguredError,
    RealConnectorRequestError,
    _runtime_credentials,
)
from src.core.models import AccountSnapshot, Position, utc_now

ORIGIN = 'https://api.qfex.com'
API_VERSION = '2026-10-29'


def validate_credentials(credentials: dict[str, str]) -> dict[str, str]:
    """Require an explicit target, including for a primary account; never guess."""
    try:
        for name in ('api_key', 'api_secret', 'account_id'):
            value = credentials[name]
            if not isinstance(value, str) or not value or value.strip() != value or any(ord(c) < 33 or ord(c) > 126 for c in value):
                raise ValueError
        account = credentials['account_id']
        if str(UUID(account)) != account.lower():
            raise ValueError
    except (KeyError, TypeError, ValueError, AttributeError):
        raise RealConnectorNotConfiguredError('qfex requires api_key (PUBLIC), api_secret (SECRET), and account_id (UUID)') from None
    return {name: credentials[name] for name in ('api_key', 'api_secret', 'account_id')}


def _number(row: dict, field: str, *, nonnegative: bool = False) -> Decimal:
    try:
        raw = row[field]
        if isinstance(raw, bool) or not isinstance(raw, (str, int, float, Decimal)):
            raise ValueError
        value = Decimal(str(raw))
        if not value.is_finite() or not math.isfinite(float(value)) or (nonnegative and value < 0):
            raise ValueError
        return value
    except (KeyError, ValueError, TypeError, InvalidOperation, OverflowError):
        raise RealConnectorRequestError(f'qfex invalid {field}') from None


def _headers(credentials: dict[str, str]) -> dict[str, str]:
    nonce = secrets.token_hex(16)
    timestamp = str(int(time.time()))
    signature = hmac.new(credentials['api_secret'].encode(), f'{nonce}:{timestamp}'.encode(), hashlib.sha256).hexdigest()
    return {'x-qfex-public-key': credentials['api_key'], 'x-qfex-nonce': nonce,
            'x-qfex-timestamp': timestamp, 'x-qfex-hmac-signature': signature,
            'x-qfex-requested-account-id': credentials['account_id']}


class QfexRealConnector(ExchangeConnector):
    exchange = 'qfex'

    async def _query(self, client: httpx.AsyncClient, path: str, credentials: dict[str, str]):
        if path not in ('/refdata', '/user/positions'):
            raise RealConnectorRequestError('qfex unsupported read endpoint')
        try:
            response = await client.get(ORIGIN + path, params={'api_version': API_VERSION},
                                        headers={} if path == '/refdata' else _headers(credentials))
            if response.status_code != 200:
                raise RealConnectorRequestError(f'qfex HTTP {response.status_code}')
            return response.json(parse_float=Decimal)
        except (httpx.HTTPError, ValueError):
            raise RealConnectorRequestError('qfex request or JSON response failed') from None

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        settings = get_settings()
        credentials = _runtime_credentials(self.exchange)
        if ':' not in self.exchange and not credentials:
            credentials = {'api_key': settings.qfex_api_key, 'api_secret': settings.qfex_api_secret,
                           'account_id': settings.qfex_account_id}
        credentials = validate_credentials(credentials)
        timeout = settings.request_timeout_sec
        try:
            async with asyncio.timeout(timeout):
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                    refdata = await self._query(client, '/refdata', credentials)
                    account = await self._query(client, '/user/positions', credentials)
        except TimeoutError:
            raise RealConnectorRequestError('qfex account deadline exceeded') from None
        # Keep calculations independent of the caller's Decimal context.
        with localcontext() as ctx:
            ctx.prec = 50
            return self._snapshot(account, refdata)

    def _snapshot(self, account, refdata) -> AccountSnapshot:
        if not isinstance(account, dict) or not isinstance(account.get('balance'), dict):
            raise RealConnectorRequestError('qfex malformed account')
        balance = account['balance']
        rows = account.get('positions')
        # The published contract explicitly permits a null positions array.
        if 'positions' not in account or (rows is not None and not isinstance(rows, list)):
            raise RealConnectorRequestError('qfex malformed positions')
        if not isinstance(refdata, dict) or not isinstance(refdata.get('data'), list):
            raise RealConnectorRequestError('qfex malformed reference data')
        markets = {}
        for market in refdata['data']:
            if not isinstance(market, dict) or not isinstance(market.get('symbol'), str) or market['symbol'] in markets:
                raise RealConnectorRequestError('qfex duplicate or malformed reference market')
            markets[market['symbol']] = market
        available = _number(balance, 'available_balance')
        maintenance = _number(balance, 'position_margin', nonnegative=True)
        order_margin = _number(balance, 'order_margin', nonnegative=True)
        if 'isolated_equity' in balance and _number(balance, 'isolated_equity') != 0:
            raise RealConnectorRequestError('qfex isolated equity is not supported')
        # Native cross-margin identity: available = equity - position_margin - order_margin.
        # Do not add unrealised_pnl again, nor rebuild cash from incomplete fee fields.
        equity = available + maintenance + order_margin
        positions = []
        seen = set()
        for row in rows or []:
            if not isinstance(row, dict):
                raise RealConnectorRequestError('qfex malformed position')
            symbol = row.get('symbol')
            if not isinstance(symbol, str) or not symbol or symbol in seen:
                raise RealConnectorRequestError('qfex duplicate or malformed position symbol')
            seen.add(symbol)
            if row.get('margin_mode', 'cross') != 'cross':
                raise RealConnectorRequestError('qfex unsupported margin mode')
            for field in ('isolated_collateral', 'isolated_available'):
                if field in row and _number(row, field) != 0:
                    raise RealConnectorRequestError('qfex isolated position is not supported')
            qty = _number(row, 'position')
            if qty == 0:
                continue
            market = markets.get(symbol, {})
            if (market.get('quote_asset') != 'USD' or market.get('margin_asset') != 'USDC'
                    or not isinstance(market.get('base_asset'), str) or not market['base_asset']
                    or symbol != market['base_asset'] + '-USD' or market.get('isolated_only') is not False):
                raise RealConnectorRequestError('qfex unsupported position contract')
            entry = _number(row, 'average_price', nonnegative=True)
            leverage = _number(row, 'leverage', nonnegative=True)
            upnl = _number(row, 'unrealised_pnl')
            # Docs define upnl = signed base quantity * (mark - entry), with no
            # exchange contract multiplier. This is the account valuation mark,
            # not /md/contracts.last_price or index_price.
            mark = entry + upnl / qty
            if entry <= 0 or mark <= 0 or leverage <= 0 or not all(
                math.isfinite(float(v)) for v in (mark, abs(qty) * mark, equity)
            ):
                raise RealConnectorRequestError('qfex invalid position valuation')
            positions.append(Position(exchange=self.exchange, symbol=symbol,
                side='long' if qty > 0 else 'short', size=float(abs(qty)), entry_price=float(entry),
                mark_price=float(mark), leverage=float(leverage), liquidation_price=None))
        if not math.isfinite(float(equity)):
            raise RealConnectorRequestError('qfex invalid equity')
        if not positions and maintenance != 0:
            raise RealConnectorRequestError('qfex nonzero maintenance without positions')
        if positions and maintenance <= 0:
            raise RealConnectorRequestError('qfex missing native position maintenance')
        return AccountSnapshot(exchange=self.exchange, equity_usd=float(equity),
            available_margin_usd=float(available), maintenance_margin_usd=float(maintenance),
            positions=positions, updated_at=utc_now())
