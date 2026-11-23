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
    """Require the keypair; an optional explicit account remains authoritative."""
    try:
        for name in ('api_key', 'api_secret'):
            value = credentials[name]
            if not isinstance(value, str) or not value or value.strip() != value or any(ord(c) < 33 or ord(c) > 126 for c in value):
                raise ValueError
        account = credentials.get('account_id', '')
        if account != '' and (not isinstance(account, str) or str(UUID(account)) != account.lower() or UUID(account).int == 0):
            raise ValueError
    except (KeyError, TypeError, ValueError, AttributeError):
        raise RealConnectorNotConfiguredError('qfex requires api_key (PUBLIC), api_secret (SECRET); optional account_id must be a UUID') from None
    result = {name: credentials[name] for name in ('api_key', 'api_secret')}
    if account:
        result['account_id'] = account
    return result


def resolve_account(credentials: dict[str, str], payload) -> dict[str, str]:
    """Validate the entire returned scope before selecting a singleton or explicit target."""
    accounts = payload.get('accounts') if isinstance(payload, dict) else None
    if not isinstance(accounts, list) or not accounts:
        raise RealConnectorRequestError('qfex missing account scope')
    identities = set()
    for row in accounts:
        try:
            identity = row['account_id']
            if not isinstance(identity, str) or str(UUID(identity)) != identity.lower() or UUID(identity).int == 0:
                raise ValueError
            if identity.lower() in identities or not isinstance(row.get('is_master'), bool):
                raise ValueError
            _number(row, 'equity')
            _number(row, 'available_balance')
        except (KeyError, TypeError, ValueError, AttributeError):
            raise RealConnectorRequestError('qfex invalid account scope') from None
        identities.add(identity.lower())
    explicit = credentials.get('account_id')
    if explicit:
        if explicit.lower() not in identities:
            raise RealConnectorRequestError('qfex configured account is outside returned scope')
        selected = explicit
    elif len(identities) == 1:
        selected = next(iter(identities))
    else:
        raise RealConnectorRequestError('qfex multiple accounts: choose an explicit account_id; no primary fallback')
    return dict(credentials, account_id=selected)


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
    headers = {'x-qfex-public-key': credentials['api_key'], 'x-qfex-nonce': nonce,
            'x-qfex-timestamp': timestamp, 'x-qfex-hmac-signature': signature}
    if credentials.get('account_id'):
        headers['x-qfex-requested-account-id'] = credentials['account_id']
    return headers


class QfexRealConnector(ExchangeConnector):
    exchange = 'qfex'

    async def _query(self, client: httpx.AsyncClient, path: str, credentials: dict[str, str]):
        if path not in ('/refdata', '/user/positions', '/user/subaccounts/balance'):
            raise RealConnectorRequestError('qfex unsupported read endpoint')
        if path == '/user/positions' and not credentials.get('account_id'):
            raise RealConnectorRequestError('qfex account scope must be resolved before positions')
        try:
            response = await client.get(ORIGIN + path, params={'api_version': API_VERSION},
                                        headers={} if path == '/refdata' else _headers(
                                            {k: v for k, v in credentials.items() if k != 'account_id'}
                                            if path == '/user/subaccounts/balance' else credentials))
            if response.status_code != 200:
                raise RealConnectorRequestError(f'qfex HTTP {response.status_code}')
            return response.json(parse_float=Decimal)
        except (httpx.HTTPError, ValueError):
            raise RealConnectorRequestError('qfex request or JSON response failed') from None

    async def discover_credentials(self, client: httpx.AsyncClient, credentials: dict[str, str]) -> dict[str, str]:
        credentials = validate_credentials(credentials)
        scope = await self._query(client, '/user/subaccounts/balance', credentials)
        return resolve_account(credentials, scope)

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        settings = get_settings()
        credentials = _runtime_credentials(self.exchange)
        if ':' not in self.exchange and not credentials:
            credentials = {'api_key': settings.qfex_api_key, 'api_secret': settings.qfex_api_secret,
                           'account_id': settings.qfex_account_id}
        return await self.fetch_snapshot_with_credentials(credentials)

    async def fetch_snapshot_with_credentials(self, credentials: dict[str, str]) -> AccountSnapshot:
        credentials = validate_credentials(credentials)
        timeout = get_settings().request_timeout_sec
        try:
            async with asyncio.timeout(timeout):
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                    credentials = await self.discover_credentials(client, credentials)
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
            # Exact documented E(P)=M(P) boundary, also used by QFEX's public
            # trading UI (lM in index-Cd1w1ehH.js). All other marks, quantities,
            # cash and leverages are held fixed; this is NOT a native REST field.
            # E(P) = equity + qty*(P-mark)
            # M(P) = maintenance + abs(qty)*2/(3*leverage)*(P-mark)
            # Order margin is released during liquidation, not subtracted again.
            rate = Decimal(2) / (3 * leverage)
            if rate >= 1:
                raise RealConnectorRequestError('qfex unsupported maintenance rate')
            liquidation = mark - (equity - maintenance) / (qty - abs(qty) * rate)
            if not math.isfinite(float(liquidation)):
                raise RealConnectorRequestError('qfex invalid calculated liquidation price')
            # A negative root means no nonnegative crossing under this scenario,
            # not a zero liquidation price and not proof of account-wide safety.
            liq_price = float(liquidation) if liquidation >= 0 else None
            positions.append(Position(exchange=self.exchange, symbol=symbol,
                side='long' if qty > 0 else 'short', size=float(abs(qty)), entry_price=float(entry),
                mark_price=float(mark), leverage=float(leverage), liquidation_price=liq_price))
        if not math.isfinite(float(equity)):
            raise RealConnectorRequestError('qfex invalid equity')
        if not positions and maintenance != 0:
            raise RealConnectorRequestError('qfex nonzero maintenance without positions')
        if positions and maintenance <= 0:
            raise RealConnectorRequestError('qfex missing native position maintenance')
        return AccountSnapshot(exchange=self.exchange, equity_usd=float(equity),
            available_margin_usd=float(available), maintenance_margin_usd=float(maintenance),
            positions=positions, updated_at=utc_now())
