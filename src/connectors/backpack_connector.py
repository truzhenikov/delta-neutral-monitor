"""Backpack's documented, signed GET-only account surface.

No trading/capital mutation methods; no redirects or configurable remote hosts.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import math
import time
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from src.config import get_settings
from src.connectors.base import ExchangeConnector
from src.connectors.real_connectors import (
    RealConnectorNotConfiguredError,
    RealConnectorRequestError,
    _runtime_credentials,
)
from src.core.models import AccountSnapshot, Position, utc_now

ORIGIN = 'https://api.backpack.exchange'
INSTRUCTIONS = {'/api/v1/capital/collateral': 'collateralQuery', '/api/v1/position': 'positionQuery'}


def validate_keypair(credentials: dict[str, str]) -> tuple[str, Ed25519PrivateKey]:
    """Validate base64 seed/public key without exposing supplied material."""
    try:
        public = base64.b64decode(credentials['api_key'], validate=True)
        seed = base64.b64decode(credentials['api_secret'], validate=True)
        if len(public) != 32 or len(seed) != 32:
            raise ValueError
        key = Ed25519PrivateKey.from_private_bytes(seed)
        if key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw) != public:
            raise ValueError
        return credentials['api_key'], key
    except (KeyError, TypeError, ValueError, binascii.Error):
        raise RealConnectorNotConfiguredError('backpack requires matching base64 ED25519 public key and 32-byte seed') from None


def _number(row: dict, name: str, *, nonnegative: bool = False) -> Decimal:
    try:
        value = row[name]
        if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
            raise ValueError
        parsed = Decimal(str(value))
        if not parsed.is_finite() or not math.isfinite(float(parsed)) or (nonnegative and parsed < 0):
            raise ValueError
        return parsed
    except (KeyError, ValueError, TypeError, InvalidOperation, OverflowError):
        raise RealConnectorRequestError(f'backpack invalid {name}') from None


class BackpackRealConnector(ExchangeConnector):
    exchange = 'backpack'
    request_spacing_sec = 0.1

    @staticmethod
    def _headers(instruction: str, params: dict, public: str, key: Ed25519PrivateKey) -> dict[str, str]:
        timestamp = str(int(time.time() * 1000))
        query = urlencode(sorted(params.items()))
        message = f'instruction={instruction}' + (f'&{query}' if query else '') + f'&timestamp={timestamp}&window=5000'
        return {'X-API-Key': public, 'X-Signature': base64.b64encode(key.sign(message.encode())).decode(),
                'X-Timestamp': timestamp, 'X-Window': '5000'}

    async def _query(self, client: httpx.AsyncClient, path: str, public: str, key: Ed25519PrivateKey) -> Any:
        if path not in INSTRUCTIONS:
            raise RealConnectorRequestError('backpack unsupported read endpoint')
        try:
            response = await client.get(ORIGIN + path, headers=self._headers(INSTRUCTIONS[path], {}, public, key))
            if response.status_code != 200:
                raise RealConnectorRequestError(f'backpack HTTP {response.status_code}')
            return response.json()
        except (httpx.HTTPError, ValueError):
            raise RealConnectorRequestError('backpack request or JSON response failed') from None

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        settings = get_settings()
        if settings.backpack_api_base != ORIGIN:
            raise RealConnectorNotConfiguredError('backpack requires the official API origin')
        credentials = _runtime_credentials(self.exchange)
        # Never borrow the default account's environment keys for a named profile.
        if ':' not in self.exchange and not credentials:
            credentials = {'api_key': settings.backpack_api_key, 'api_secret': settings.backpack_api_secret}
        public, key = validate_keypair(credentials)
        timeout = settings.request_timeout_sec
        try:
            async with asyncio.timeout(timeout):
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                    account = await self._query(client, '/api/v1/capital/collateral', public, key)
                    await asyncio.sleep(self.request_spacing_sec)
                    rows = await self._query(client, '/api/v1/position', public, key)
        except TimeoutError:
            raise RealConnectorRequestError('backpack account deadline exceeded') from None
        return self._snapshot(account, rows)

    def _snapshot(self, account: Any, rows: Any) -> AccountSnapshot:
        if not isinstance(account, dict) or not isinstance(rows, list):
            raise RealConnectorRequestError('backpack malformed account/positions response')
        equity = _number(account, 'netEquity')
        available = _number(account, 'netEquityAvailable')
        exposure = _number(account, 'netExposureFutures', nonnegative=True)
        mmf = _number(account, 'mmf', nonnegative=True)
        # The account MMF denominator includes all exposure. Do not silently omit
        # borrow maintenance until that additional exposure is mapped/tested.
        if _number(account, 'borrowLiability', nonnegative=True) != 0:
            raise RealConnectorRequestError('backpack borrowing exposure is not yet supported')
        if mmf > 1:
            raise RealConnectorRequestError('backpack invalid mmf')
        collateral = account.get('collateral')
        if not isinstance(collateral, list):
            raise RealConnectorRequestError('backpack malformed collateral')
        for asset in collateral:
            if not isinstance(asset, dict) or not isinstance(asset.get('symbol'), str) or not asset['symbol']:
                raise RealConnectorRequestError('backpack malformed collateral asset')
            # Values are venue-supplied USDC notionals, never quantity * $1.
            for field in ('totalQuantity', 'assetMarkPrice', 'balanceNotional', 'collateralValue', 'collateralWeight'):
                _number(asset, field, nonnegative=True)
            if _number(asset, 'totalQuantity') > 0 and _number(asset, 'assetMarkPrice') <= 0:
                raise RealConnectorRequestError('backpack unpriced collateral')
        positions = []
        seen = set()
        for row in rows:
            if not isinstance(row, dict):
                raise RealConnectorRequestError('backpack malformed position')
            qty = _number(row, 'netQuantity')
            if qty == 0:
                continue
            symbol = row.get('symbol')
            if not isinstance(symbol, str) or not symbol.endswith('_USDC_PERP') or symbol in seen:
                raise RealConnectorRequestError('backpack unsupported or duplicate position market')
            seen.add(symbol)
            entry = _number(row, 'entryPrice', nonnegative=True)
            mark = _number(row, 'markPrice', nonnegative=True)
            liq = _number(row, 'estLiquidationPrice', nonnegative=True)
            imf = _number(row, 'imf', nonnegative=True)
            if not all(math.isfinite(float(value)) for value in (abs(qty) * mark, abs(qty) * entry, 1 / imf if imf else Decimal('Infinity'))):
                raise RealConnectorRequestError('backpack position numeric overflow')
            if mark <= 0 or entry <= 0 or not 0 < imf <= 1:
                raise RealConnectorRequestError('backpack invalid position price or imf')
            positions.append(Position(exchange=self.exchange, symbol=symbol, side='long' if qty > 0 else 'short',
                                      size=float(abs(qty)), entry_price=float(entry), mark_price=float(mark),
                                      leverage=float(1 / imf), liquidation_price=float(liq) if liq > 0 else None))
        maintenance = float(mmf * exposure)
        if not math.isfinite(maintenance):
            raise RealConnectorRequestError('backpack invalid maintenance margin')
        return AccountSnapshot(exchange=self.exchange, equity_usd=float(equity), available_margin_usd=float(available),
                               maintenance_margin_usd=maintenance, positions=positions, updated_at=utc_now())
