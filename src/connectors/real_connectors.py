from __future__ import annotations

import base64
import collections
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx

from src.config import get_settings
from src.connectors.base import ExchangeConnector
from src.core.models import AccountSnapshot, Position, utc_now
from src.services.credential_store import CredentialStore


class RealConnectorNotConfiguredError(RuntimeError):
    pass


class RealConnectorRequestError(RuntimeError):
    pass


def _runtime_credentials(exchange: str) -> dict[str, str]:
    settings = get_settings()
    store = CredentialStore(Path(settings.credential_store_path))
    return store.get_exchange_credentials(exchange)


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, str):
        if value.strip() == "":
            return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_liq_price(value: Any) -> float | None:
    val = _safe_float(value, default=0.0)
    return val if val > 0 else None


def _nado_subaccount_hex(wallet_address: str, subaccount_name: str = "default") -> str:
    wallet = wallet_address.strip().lower()
    if len(wallet) != 42 or not wallet.startswith("0x"):
        raise ValueError("Nado wallet address must be a 20-byte 0x-prefixed EVM address")
    try:
        wallet_bytes = bytes.fromhex(wallet[2:])
    except ValueError as exc:
        raise ValueError("Nado wallet address must contain hexadecimal characters only") from exc

    name_bytes = subaccount_name.strip().encode("utf-8")
    if not name_bytes or len(name_bytes) > 12:
        raise ValueError("Nado subaccount name must contain between 1 and 12 UTF-8 bytes")
    return "0x" + (wallet_bytes + name_bytes.ljust(12, b"\x00")).hex()


def _nado_decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(0)


def _hyperliquid_build_spot_graph(
    spot_meta: dict[str, Any], all_mids: dict[str, Any]
) -> dict[int, list[tuple[int, float]]]:
    graph: dict[int, list[tuple[int, float]]] = {}
    for market in spot_meta.get("universe") or []:
        pair_tokens = market.get("tokens") or []
        if len(pair_tokens) != 2:
            continue

        pair_index = market.get("index")
        if pair_index is None:
            continue

        mid = _safe_float(all_mids.get(f"@{pair_index}"), default=0.0)
        if mid <= 0:
            continue

        base_token = _safe_float(pair_tokens[0], default=-1)
        quote_token = _safe_float(pair_tokens[1], default=-1)
        if base_token < 0 or quote_token < 0:
            continue

        base_token_int = int(base_token)
        quote_token_int = int(quote_token)
        graph.setdefault(base_token_int, []).append((quote_token_int, mid))
        graph.setdefault(quote_token_int, []).append((base_token_int, 1.0 / mid))

    return graph


def _hyperliquid_find_token_usd_price(
    token: int,
    graph: dict[int, list[tuple[int, float]]],
    usd_token: int = 0,
) -> float | None:
    if token == usd_token:
        return 1.0

    queue: collections.deque[tuple[int, float]] = collections.deque([(token, 1.0)])
    seen = {token}
    while queue:
        current_token, current_value = queue.popleft()
        for next_token, conversion_rate in graph.get(current_token, []):
            if next_token in seen:
                continue

            next_value = current_value * conversion_rate
            if next_token == usd_token:
                return next_value

            seen.add(next_token)
            queue.append((next_token, next_value))

    return None


def _hyperliquid_spot_portfolio_value(
    spot_state: dict[str, Any],
    spot_meta: dict[str, Any],
    all_mids: dict[str, Any],
) -> float:
    graph = _hyperliquid_build_spot_graph(spot_meta, all_mids)
    total_value = 0.0
    for balance in spot_state.get("balances") or []:
        total = _safe_float(balance.get("total"))
        if total <= 0:
            continue

        token = int(_safe_float(balance.get("token"), default=-1))
        if token < 0:
            continue

        token_price = _hyperliquid_find_token_usd_price(token, graph)
        if token_price is None:
            token_price = _safe_float(balance.get("entryNtl"), default=0.0) / total if total > 0 else 0.0

        total_value += total * token_price

    return total_value


class _BaseRealConnector(ExchangeConnector):
    async def _get(
        self,
        base_url: str,
        path: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        settings = get_settings()
        timeout = httpx.Timeout(settings.request_timeout_sec)
        url = f"{base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(url, params=params, headers=headers)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as exc:
            raise RealConnectorRequestError(
                f"{self.exchange} http status error: {exc.response.status_code} {exc.response.text[:300]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise RealConnectorRequestError(f"{self.exchange} http error: {exc}") from exc

    async def _post(
        self,
        base_url: str,
        path: str,
        body: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        settings = get_settings()
        timeout = httpx.Timeout(settings.request_timeout_sec)
        url = f"{base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=body, headers=headers)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as exc:
            raise RealConnectorRequestError(
                f"{self.exchange} http status error: {exc.response.status_code} {exc.response.text[:300]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise RealConnectorRequestError(f"{self.exchange} http error: {exc}") from exc

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        raise RealConnectorNotConfiguredError(
            f"{self.exchange}: real API integration is scaffolded but not configured yet"
        )


class BinanceRealConnector(_BaseRealConnector):
    exchange = "binance"

    def _build_signed_params(self, params: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, str]]:
        settings = get_settings()
        credentials = _runtime_credentials(self.exchange)
        api_key = credentials.get("api_key") or settings.binance_api_key
        api_secret = credentials.get("api_secret") or settings.binance_api_secret
        if not (api_key and api_secret):
            raise RealConnectorNotConfiguredError(
                "binance credentials are not configured (BINANCE_API_KEY/SECRET)"
            )

        signed_params: dict[str, Any] = {
            "recvWindow": 5000,
            "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
        }
        if params:
            signed_params.update(params)
        query = urlencode(sorted(signed_params.items()))
        signature = hmac.new(api_secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()
        signed_params["signature"] = signature
        headers = {"X-MBX-APIKEY": api_key}
        return signed_params, headers

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        settings = get_settings()
        params, headers = self._build_signed_params()
        payload = await self._get(
            base_url=settings.binance_api_base,
            path="/fapi/v2/account",
            params=params,
            headers=headers,
        )

        if not isinstance(payload, dict) or not payload:
            raise RealConnectorRequestError(f"binance account error: {payload}")

        raw_positions = payload.get("positions") or []
        positions: list[Position] = []
        maintenance_margin = _safe_float(payload.get("totalMaintMargin"))
        if maintenance_margin <= 0:
            maintenance_margin = 0.0

        for row in raw_positions:
            position_amt = _safe_float(row.get("positionAmt"))
            if position_amt == 0:
                continue
            side_raw = str(row.get("positionSide") or "").upper()
            if side_raw == "SHORT" or position_amt < 0:
                side = "short"
            else:
                side = "long"
            mark_price = _safe_float(row.get("markPrice"))
            entry_price = _safe_float(row.get("entryPrice"), default=mark_price)
            leverage = _safe_float(row.get("leverage"), default=1.0)
            positions.append(
                Position(
                    exchange=self.exchange,
                    symbol=str(row.get("symbol") or "UNKNOWN"),
                    side=side,
                    size=abs(position_amt),
                    entry_price=entry_price,
                    mark_price=mark_price,
                    leverage=leverage if leverage > 0 else 1.0,
                    liquidation_price=_safe_liq_price(row.get("liquidationPrice")),
                )
            )
            if maintenance_margin <= 0:
                maintenance_margin += _safe_float(row.get("maintMargin"))

        equity = _safe_float(payload.get("totalMarginBalance"))
        if equity <= 0:
            equity = _safe_float(payload.get("totalWalletBalance")) + _safe_float(payload.get("totalUnrealizedProfit"))
        available = _safe_float(payload.get("availableBalance"))
        if available <= 0:
            available = _safe_float(payload.get("maxWithdrawAmount"))

        return AccountSnapshot(
            exchange=self.exchange,
            equity_usd=equity,
            available_margin_usd=available,
            maintenance_margin_usd=maintenance_margin,
            positions=positions,
            updated_at=utc_now(),
        )


class BitgetRealConnector(_BaseRealConnector):
    exchange = "bitget"

    def _build_signed_headers(
        self,
        api_key: str,
        api_secret: str,
        passphrase: str,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        timestamp_ms = str(int(datetime.now(timezone.utc).timestamp() * 1000))
        query = urlencode(params or {})
        request_path = path + (f"?{query}" if query else "")
        body_raw = json.dumps(body, separators=(",", ":"), ensure_ascii=False) if body else ""
        pre_hash = f"{timestamp_ms}{method.upper()}{request_path}{body_raw}"
        sign = base64.b64encode(
            hmac.new(api_secret.encode("utf-8"), pre_hash.encode("utf-8"), hashlib.sha256).digest()
        ).decode("utf-8")

        return {
            "ACCESS-KEY": api_key,
            "ACCESS-SIGN": sign,
            "ACCESS-TIMESTAMP": timestamp_ms,
            "ACCESS-PASSPHRASE": passphrase,
            "Content-Type": "application/json",
            "locale": "en-US",
        }

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        settings = get_settings()
        credentials = _runtime_credentials(self.exchange)
        api_key = credentials.get("api_key") or settings.bitget_api_key
        api_secret = credentials.get("api_secret") or settings.bitget_api_secret
        api_passphrase = credentials.get("api_passphrase") or settings.bitget_api_passphrase
        if not (api_key and api_secret and api_passphrase):
            raise RealConnectorNotConfiguredError(
                "bitget credentials are not configured (BITGET_API_KEY/SECRET/PASSPHRASE)"
            )

        params = {"productType": settings.bitget_product_type}
        account_path = "/api/v2/mix/account/accounts"
        account_headers = self._build_signed_headers(
            api_key=api_key,
            api_secret=api_secret,
            passphrase=api_passphrase,
            method="GET",
            path=account_path,
            params=params,
        )
        account_payload = await self._get(
            base_url=settings.bitget_api_base,
            path=account_path,
            params=params,
            headers=account_headers,
        )

        if account_payload.get("code") != "00000":
            raise RealConnectorRequestError(f"bitget account error: {account_payload}")

        accounts_data = account_payload.get("data") or []
        acc = None
        for row in accounts_data:
            if str(row.get("marginCoin", "")).upper() == settings.bitget_margin_coin.upper():
                acc = row
                break
        if acc is None and accounts_data:
            acc = accounts_data[0]
        if acc is None:
            raise RealConnectorRequestError("bitget returned empty account data")

        pos_params = {
            "productType": settings.bitget_product_type,
            "marginCoin": settings.bitget_margin_coin.upper(),
        }
        pos_path = "/api/v2/mix/position/all-position"
        pos_headers = self._build_signed_headers(
            api_key=api_key,
            api_secret=api_secret,
            passphrase=api_passphrase,
            method="GET",
            path=pos_path,
            params=pos_params,
        )
        pos_payload = await self._get(
            base_url=settings.bitget_api_base,
            path=pos_path,
            params=pos_params,
            headers=pos_headers,
        )
        if pos_payload.get("code") != "00000":
            raise RealConnectorRequestError(f"bitget positions error: {pos_payload}")

        positions: list[Position] = []
        maintenance_margin = 0.0

        for row in pos_payload.get("data") or []:
            size = _safe_float(row.get("total"))
            if size <= 0:
                continue
            side_raw = str(row.get("holdSide", "")).lower()
            side = "short" if side_raw == "short" else "long"
            mark = _safe_float(row.get("markPrice"))
            entry = _safe_float(row.get("openPriceAvg"), default=mark)
            leverage = _safe_float(row.get("leverage"), default=1.0)
            liq_price = _safe_liq_price(row.get("liquidationPrice"))

            positions.append(
                Position(
                    exchange=self.exchange,
                    symbol=str(row.get("symbol", "UNKNOWN")),
                    side=side,
                    size=abs(size),
                    entry_price=entry,
                    mark_price=mark,
                    leverage=leverage if leverage > 0 else 1.0,
                    liquidation_price=liq_price,
                )
            )

            margin_size = _safe_float(row.get("marginSize"))
            mmr = _safe_float(row.get("keepMarginRate"))
            if margin_size > 0 and mmr > 0:
                maintenance_margin += margin_size * mmr

        equity = _safe_float(acc.get("usdtEquity"))
        if equity <= 0:
            equity = _safe_float(acc.get("accountEquity"))
        if equity <= 0:
            equity = _safe_float(acc.get("equity"))
        if equity <= 0:
            equity = _safe_float(acc.get("available")) + _safe_float(acc.get("locked"))

        available = _safe_float(acc.get("available"))
        if available <= 0:
            available = _safe_float(acc.get("maxOpenPosAvailable"))

        return AccountSnapshot(
            exchange=self.exchange,
            equity_usd=equity,
            available_margin_usd=available,
            maintenance_margin_usd=maintenance_margin,
            positions=positions,
            updated_at=utc_now(),
        )


class BingxRealConnector(_BaseRealConnector):
    exchange = "bingx"

    def _build_signed_params(self, params: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, str]]:
        settings = get_settings()
        credentials = _runtime_credentials(self.exchange)
        api_key = credentials.get("api_key") or settings.bingx_api_key
        api_secret = credentials.get("api_secret") or settings.bingx_api_secret
        if not (api_key and api_secret):
            raise RealConnectorNotConfiguredError(
                "bingx credentials are not configured (BINGX_API_KEY/SECRET)"
            )

        signed_params = {"recvWindow": 5000, "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000)}
        if params:
            signed_params.update(params)
        query = urlencode(sorted(signed_params.items()))
        signature = hmac.new(
            api_secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        signed_params["signature"] = signature
        headers = {"X-BX-APIKEY": api_key}
        return signed_params, headers

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        settings = get_settings()
        balance_params, balance_headers = self._build_signed_params()
        balance_payload = await self._get(
            base_url=settings.bingx_api_base,
            path="/openApi/swap/v2/user/balance",
            params=balance_params,
            headers=balance_headers,
        )
        if int(balance_payload.get("code", -1)) != 0:
            raise RealConnectorRequestError(f"bingx balance error: {balance_payload}")

        position_params, position_headers = self._build_signed_params()
        position_payload = await self._get(
            base_url=settings.bingx_api_base,
            path="/openApi/swap/v2/user/positions",
            params=position_params,
            headers=position_headers,
        )
        if int(position_payload.get("code", -1)) != 0:
            raise RealConnectorRequestError(f"bingx positions error: {position_payload}")

        balance_data = (balance_payload.get("data") or {}).get("balance") or {}
        raw_positions = position_payload.get("data") or []

        positions: list[Position] = []
        for row in raw_positions:
            size = _safe_float(row.get("positionAmt"))
            if size <= 0:
                continue
            side = "short" if str(row.get("positionSide", "")).upper() == "SHORT" else "long"
            positions.append(
                Position(
                    exchange=self.exchange,
                    symbol=str(row.get("symbol", "UNKNOWN")),
                    side=side,
                    size=size,
                    entry_price=_safe_float(row.get("avgPrice")),
                    mark_price=_safe_float(row.get("markPrice")),
                    leverage=_safe_float(row.get("leverage"), default=1.0),
                    liquidation_price=_safe_liq_price(row.get("liquidationPrice")),
                )
            )

        equity = _safe_float(balance_data.get("equity"))
        available = _safe_float(balance_data.get("availableMargin"))
        maintenance = _safe_float(balance_data.get("usedMargin"))

        return AccountSnapshot(
            exchange=self.exchange,
            equity_usd=equity,
            available_margin_usd=available,
            maintenance_margin_usd=maintenance,
            positions=positions,
            updated_at=utc_now(),
        )


class PhemexRealConnector(_BaseRealConnector):
    exchange = "phemex"

    def _build_signed_headers(self, path: str, query_string: str = "", body_raw: str = "") -> dict[str, str]:
        settings = get_settings()
        credentials = _runtime_credentials(self.exchange)
        api_key = credentials.get("api_key") or settings.phemex_api_key
        api_secret = credentials.get("api_secret") or settings.phemex_api_secret
        if not (api_key and api_secret):
            raise RealConnectorNotConfiguredError("phemex credentials are not configured (PHEMEX_API_KEY/SECRET)")

        expiry = str(int(time.time()) + 60)
        signature_payload = f"{path}{query_string}{expiry}{body_raw}"
        signature = hmac.new(api_secret.encode("utf-8"), signature_payload.encode("utf-8"), hashlib.sha256).hexdigest()
        return {
            "x-phemex-access-token": api_key,
            "x-phemex-request-expiry": expiry,
            "x-phemex-request-signature": signature,
            "Accept": "application/json",
        }

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        settings = get_settings()
        currency = (settings.phemex_margin_currency or "USDT").strip().upper()
        params = {"currency": currency}
        query_string = urlencode(params)
        payload = await self._get(
            base_url=settings.phemex_api_base,
            path="/g-accounts/accountPositions",
            params=params,
            headers=self._build_signed_headers("/g-accounts/accountPositions", query_string),
        )
        if int(payload.get("code", -1)) != 0:
            raise RealConnectorRequestError(f"phemex accountPositions error: {payload}")

        data = payload.get("data") or {}
        account = data.get("account") or {}
        raw_positions = data.get("positions") or []

        positions: list[Position] = []
        maintenance_margin = 0.0
        for row in raw_positions:
            size = abs(_safe_float(row.get("size")))
            if size <= 0:
                continue

            side_raw = str(row.get("posSide") or row.get("side") or "").strip().lower()
            side = "short" if side_raw in {"short", "sell"} else "long"
            mark_price = _safe_float(row.get("markPriceRp") or row.get("markPrice"))
            entry_price = _safe_float(row.get("avgEntryPriceRp") or row.get("avgEntryPrice"), default=mark_price)
            leverage = _safe_float(row.get("leverageRr") or row.get("leverage"), default=1.0)
            if leverage <= 0:
                leverage = 1.0

            maintenance_margin += _safe_float(
                row.get("maintMarginRv")
                or row.get("maintMarginReqRv")
                or row.get("positionMarginRv")
            )

            positions.append(
                Position(
                    exchange=self.exchange,
                    symbol=str(row.get("symbol") or "UNKNOWN"),
                    side=side,
                    size=size,
                    entry_price=entry_price,
                    mark_price=mark_price,
                    leverage=leverage,
                    liquidation_price=_safe_liq_price(row.get("liquidationPriceRp") or row.get("liquidationPrice")),
                )
            )

        unrealized_pnl = sum(position.pnl_usd for position in positions)
        account_balance = _safe_float(account.get("accountBalanceRv"))
        equity = _safe_float(
            account.get("totalEquityRv")
            or account.get("equityRv")
            or account.get("totalBalanceRv")
        )
        if equity <= 0 and account_balance > 0:
            equity = account_balance + unrealized_pnl
        available = _safe_float(
            account.get("availableBalanceRv")
            or account.get("totalAvailableBalanceRv")
            or account.get("freeBalanceRv")
        )
        if available <= 0 and equity > 0:
            available = max(equity - _safe_float(account.get("totalUsedBalanceRv")), 0.0)

        account_maintenance = _safe_float(
            account.get("maintMarginRv")
            or account.get("totalMaintMarginReqRv")
            or account.get("totalMaintenanceMarginRv")
        )
        if account_maintenance > 0:
            maintenance_margin = account_maintenance

        return AccountSnapshot(
            exchange=self.exchange,
            equity_usd=equity,
            available_margin_usd=available,
            maintenance_margin_usd=maintenance_margin,
            positions=positions,
            updated_at=utc_now(),
        )


class AdenRealConnector(_BaseRealConnector):
    exchange = "aden"

    def _build_signed_headers(
        self,
        api_key: str,
        api_secret: str,
        method: str,
        path: str,
        query_string: str = "",
        body_raw: str = "",
    ) -> dict[str, str]:
        settings = get_settings()
        timestamp = str(int(datetime.now(timezone.utc).timestamp()))
        payload_hash = hashlib.sha512(body_raw.encode("utf-8")).hexdigest()
        sign_string = "\n".join([method.upper(), f"{settings.aden_api_prefix}{path}", query_string, payload_hash, timestamp])
        signature = hmac.new(api_secret.encode("utf-8"), sign_string.encode("utf-8"), hashlib.sha512).hexdigest()
        return {
            "KEY": api_key,
            "Timestamp": timestamp,
            "SIGN": signature,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        settings = get_settings()
        credentials = _runtime_credentials(self.exchange)
        api_key = credentials.get("api_key") or settings.aden_api_key
        api_secret = credentials.get("api_secret") or settings.aden_api_secret
        if not (api_key and api_secret):
            raise RealConnectorNotConfiguredError("aden credentials are not configured (ADEN_API_KEY/SECRET)")

        account_path = f"{settings.aden_api_prefix}/dex_futures/usdt/accounts"
        account_headers = self._build_signed_headers(
            api_key=api_key,
            api_secret=api_secret,
            method="GET",
            path="/dex_futures/usdt/accounts",
        )
        account_payload = await self._get(
            base_url=settings.aden_api_base,
            path=account_path,
            headers=account_headers,
        )
        if not isinstance(account_payload, dict) or not account_payload:
            raise RealConnectorRequestError(f"aden account error: {account_payload}")

        positions_path = f"{settings.aden_api_prefix}/dex_futures/usdt/positions"
        positions_headers = self._build_signed_headers(
            api_key=api_key,
            api_secret=api_secret,
            method="GET",
            path="/dex_futures/usdt/positions",
        )
        positions_payload = await self._get(
            base_url=settings.aden_api_base,
            path=positions_path,
            headers=positions_headers,
        )
        if not isinstance(positions_payload, list):
            raise RealConnectorRequestError(f"aden positions error: {positions_payload}")

        positions: list[Position] = []
        for row in positions_payload:
            size_signed = _safe_float(row.get("size"))
            if size_signed == 0:
                continue

            mark_price = _safe_float(row.get("mark_price") or row.get("markPrice") or row.get("last_price"))
            notional_value = _safe_float(row.get("value"))
            normalized_size = abs(size_signed)
            if notional_value > 0 and mark_price > 0:
                normalized_size = notional_value / mark_price

            leverage = _safe_float(row.get("leverage"), default=0.0)
            if leverage <= 0:
                leverage = _safe_float(row.get("lever"), default=1.0)

            positions.append(
                Position(
                    exchange=self.exchange,
                    symbol=str(row.get("contract") or row.get("symbol") or "UNKNOWN"),
                    side="long" if size_signed > 0 else "short",
                    size=normalized_size,
                    entry_price=_safe_float(row.get("entry_price") or row.get("avg_entry_price")),
                    mark_price=mark_price,
                    leverage=leverage,
                    liquidation_price=_safe_liq_price(row.get("liq_price") or row.get("liquidation_price")),
                )
            )

        return AccountSnapshot(
            exchange=self.exchange,
            equity_usd=_safe_float(
                account_payload.get("cross_margin_balance")
                or account_payload.get("margin_balance")
                or account_payload.get("total")
                or account_payload.get("total_margin_balance")
            ),
            available_margin_usd=_safe_float(account_payload.get("available") or account_payload.get("cross_available")),
            maintenance_margin_usd=_safe_float(
                account_payload.get("maintenance_margin") or account_payload.get("cross_maintenance_margin")
            ),
            positions=positions,
            updated_at=utc_now(),
        )


class MexcRealConnector(_BaseRealConnector):
    exchange = "mexc"

    def _build_signed_headers(self, params: dict[str, Any] | None = None) -> dict[str, str]:
        settings = get_settings()
        credentials = _runtime_credentials(self.exchange)
        api_key = credentials.get("api_key") or settings.mexc_api_key
        api_secret = credentials.get("api_secret") or settings.mexc_api_secret
        if not (api_key and api_secret):
            raise RealConnectorNotConfiguredError("mexc credentials are not configured (MEXC_API_KEY/SECRET)")

        request_time = str(int(datetime.now(timezone.utc).timestamp() * 1000))
        param_string = urlencode(sorted((params or {}).items()))
        sign_target = f"{api_key}{request_time}{param_string}"
        signature = hmac.new(api_secret.encode("utf-8"), sign_target.encode("utf-8"), hashlib.sha256).hexdigest()
        return {
            "ApiKey": api_key,
            "Request-Time": request_time,
            "Signature": signature,
            "Recv-Window": "5000",
            "Content-Type": "application/json",
        }

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        settings = get_settings()
        account_payload = await self._get(
            base_url=settings.mexc_api_base,
            path="/api/v1/private/account/assets",
            headers=self._build_signed_headers(),
        )
        if not isinstance(account_payload, dict) or account_payload.get("success") is not True:
            raise RealConnectorRequestError(f"mexc account error: {account_payload}")

        positions_payload = await self._get(
            base_url=settings.mexc_api_base,
            path="/api/v1/private/position/open_positions",
            headers=self._build_signed_headers(),
        )
        if not isinstance(positions_payload, dict) or positions_payload.get("success") is not True:
            raise RealConnectorRequestError(f"mexc positions error: {positions_payload}")

        assets = account_payload.get("data") or []
        account = next((row for row in assets if str(row.get("currency") or "").upper() == "USDT"), None)
        if account is None and assets:
            account = assets[0]
        if account is None:
            raise RealConnectorRequestError("mexc returned empty account assets")

        raw_positions = positions_payload.get("data") or []
        positions: list[Position] = []
        for row in raw_positions:
            size = _safe_float(row.get("holdVol"))
            if size <= 0:
                continue
            side = "short" if int(_safe_float(row.get("positionType"))) == 2 else "long"
            mark_price = _safe_float(row.get("markPrice") or row.get("fairPrice") or row.get("holdAvgPrice"))
            if mark_price <= 0:
                entry_price = _safe_float(row.get("holdAvgPrice") or row.get("openAvgPrice"))
                unrealized = _safe_float(row.get("unRealizedPnl"))
                if size > 0:
                    direction = -1 if side == "short" else 1
                    mark_price = entry_price + (unrealized / (size * direction))
            else:
                entry_price = _safe_float(row.get("holdAvgPrice") or row.get("openAvgPrice"), default=mark_price)
            if mark_price <= 0:
                mark_price = entry_price
            if entry_price <= 0:
                entry_price = mark_price

            positions.append(
                Position(
                    exchange=self.exchange,
                    symbol=str(row.get("symbol") or "UNKNOWN"),
                    side=side,
                    size=size,
                    entry_price=entry_price,
                    mark_price=mark_price,
                    leverage=_safe_float(row.get("leverage"), default=1.0) or 1.0,
                    liquidation_price=_safe_liq_price(row.get("liquidatePrice")),
                )
            )

        return AccountSnapshot(
            exchange=self.exchange,
            equity_usd=_safe_float(account.get("equity")),
            available_margin_usd=_safe_float(account.get("availableBalance") or account.get("availableOpen")),
            maintenance_margin_usd=_safe_float(account.get("positionMargin") or account.get("frozenBalance")),
            positions=positions,
            updated_at=utc_now(),
        )


class GateRealConnector(_BaseRealConnector):
    exchange = "gate"

    def _build_gate_headers(
        self,
        api_key: str,
        api_secret: str,
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        query_string = urlencode(params or {})
        body_raw = json.dumps(body, separators=(",", ":"), ensure_ascii=False) if body else ""
        hashed_payload = hashlib.sha512(body_raw.encode("utf-8")).hexdigest()
        timestamp = str(time.time())
        sign_target = urlparse(url).path
        sign_payload = "\n".join(
            [
                method.upper(),
                sign_target,
                query_string,
                hashed_payload,
                timestamp,
            ]
        )
        sign = hmac.new(api_secret.encode("utf-8"), sign_payload.encode("utf-8"), hashlib.sha512).hexdigest()
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "KEY": api_key,
            "Timestamp": timestamp,
            "SIGN": sign,
        }

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        settings = get_settings()
        credentials = _runtime_credentials(self.exchange)
        api_key = credentials.get("api_key") or settings.gate_api_key
        api_secret = credentials.get("api_secret") or settings.gate_api_secret
        if not (api_key and api_secret):
            raise RealConnectorNotConfiguredError("gate credentials are not configured (GATE_API_KEY/SECRET)")

        settle = (settings.gate_settle_currency or "usdt").strip().lower()
        account_path = f"/futures/{settle}/accounts"
        account_url = f"{settings.gate_api_base}{account_path}"
        account_headers = self._build_gate_headers(
            api_key=api_key,
            api_secret=api_secret,
            method="GET",
            url=account_url,
        )
        account_payload = await self._get(
            base_url=settings.gate_api_base,
            path=account_path,
            headers=account_headers,
        )
        if not isinstance(account_payload, dict) or not account_payload:
            raise RealConnectorRequestError(f"gate account error: {account_payload}")

        positions_path = f"/futures/{settle}/positions"
        positions_url = f"{settings.gate_api_base}{positions_path}"
        positions_headers = self._build_gate_headers(
            api_key=api_key,
            api_secret=api_secret,
            method="GET",
            url=positions_url,
        )
        positions_payload = await self._get(
            base_url=settings.gate_api_base,
            path=positions_path,
            headers=positions_headers,
        )
        if not isinstance(positions_payload, list):
            raise RealConnectorRequestError(f"gate positions error: {positions_payload}")

        positions: list[Position] = []
        for row in positions_payload:
            size_signed = _safe_float(row.get("size"))
            if size_signed == 0:
                continue

            side = "long" if size_signed > 0 else "short"
            mark_price = _safe_float(row.get("mark_price"))
            entry_price = _safe_float(row.get("entry_price"), default=mark_price)
            leverage = _safe_float(row.get("leverage"), default=1.0)
            notional_value = abs(_safe_float(row.get("value")))
            normalized_size = abs(size_signed)
            if notional_value > 0 and mark_price > 0:
                normalized_size = notional_value / mark_price

            positions.append(
                Position(
                    exchange=self.exchange,
                    symbol=str(row.get("contract") or "UNKNOWN"),
                    side=side,
                    size=normalized_size,
                    entry_price=entry_price,
                    mark_price=mark_price,
                    leverage=leverage if leverage > 0 else 1.0,
                    liquidation_price=_safe_liq_price(row.get("liq_price")),
                )
            )

        maintenance_margin = _safe_float(account_payload.get("maintenance_margin"))
        if maintenance_margin <= 0:
            maintenance_margin = _safe_float(account_payload.get("cross_maintenance_margin"))
        if maintenance_margin <= 0:
            maintenance_margin = sum(_safe_float(row.get("maintenance_margin")) for row in positions_payload)

        available = _safe_float(account_payload.get("available"))
        if available <= 0:
            available = _safe_float(account_payload.get("cross_available"))

        equity = _safe_float(account_payload.get("total"))
        if equity <= 0:
            equity = _safe_float(account_payload.get("cross_margin_balance"))

        return AccountSnapshot(
            exchange=self.exchange,
            equity_usd=equity,
            available_margin_usd=available,
            maintenance_margin_usd=maintenance_margin,
            positions=positions,
            updated_at=utc_now(),
        )


class PacificaRealConnector(_BaseRealConnector):
    exchange = "pacifica"

    @staticmethod
    def _unwrap_success(payload: dict[str, Any], context: str) -> Any:
        if not isinstance(payload, dict):
            raise RealConnectorRequestError(f"pacifica {context} error: {payload}")
        if payload.get("success") is False:
            raise RealConnectorRequestError(f"pacifica {context} error: {payload}")
        return payload.get("data")

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        settings = get_settings()
        credentials = _runtime_credentials(self.exchange)
        api_key = credentials.get("api_key") or settings.pacifica_api_key
        account = (credentials.get("account") or settings.pacifica_account).strip()
        if not (api_key and account):
            raise RealConnectorNotConfiguredError(
                "pacifica credentials are not configured (PACIFICA_API_KEY/PACIFICA_ACCOUNT)"
            )

        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        account_params = {"account": account}

        account_payload = await self._get(
            base_url=settings.pacifica_api_base,
            path="/api/v1/account",
            params=account_params,
            headers=headers,
        )
        positions_payload = await self._get(
            base_url=settings.pacifica_api_base,
            path="/api/v1/positions",
            params=account_params,
            headers=headers,
        )
        settings_payload = await self._get(
            base_url=settings.pacifica_api_base,
            path="/api/v1/account/settings",
            params=account_params,
            headers=headers,
        )
        prices_payload = await self._get(
            base_url=settings.pacifica_api_base,
            path="/api/v1/info/prices",
            headers=headers,
        )
        market_info_payload = await self._get(
            base_url=settings.pacifica_api_base,
            path="/api/v1/info",
            headers=headers,
        )

        account_data = self._unwrap_success(account_payload, "account") or {}
        positions_data = self._unwrap_success(positions_payload, "positions") or []
        account_settings = self._unwrap_success(settings_payload, "account settings") or {}
        prices_data = self._unwrap_success(prices_payload, "prices") or []
        market_info_data = self._unwrap_success(market_info_payload, "market info") or []

        leverage_by_symbol = {
            str(item.get("symbol") or ""): _safe_float(item.get("leverage"), default=0.0)
            for item in (account_settings.get("margin_settings") or [])
            if str(item.get("symbol") or "")
        }
        mark_by_symbol = {
            str(item.get("symbol") or ""): _safe_float(item.get("mark"), default=0.0)
            for item in prices_data
            if str(item.get("symbol") or "")
        }
        max_leverage_by_symbol = {
            str(item.get("symbol") or ""): _safe_float(item.get("max_leverage"), default=0.0)
            for item in market_info_data
            if str(item.get("symbol") or "")
        }

        positions: list[Position] = []
        for row in positions_data:
            size = abs(_safe_float(row.get("amount")))
            if size <= 0:
                continue

            symbol = str(row.get("symbol") or "UNKNOWN")
            mark_price = mark_by_symbol.get(symbol) or _safe_float(row.get("mark_price"))
            entry_price = _safe_float(row.get("entry_price"), default=mark_price)
            leverage = leverage_by_symbol.get(symbol) or max_leverage_by_symbol.get(symbol) or 1.0
            side = "short" if str(row.get("side", "")).lower() == "ask" else "long"

            positions.append(
                Position(
                    exchange=self.exchange,
                    symbol=symbol,
                    side=side,
                    size=size,
                    entry_price=entry_price,
                    mark_price=mark_price,
                    leverage=leverage if leverage > 0 else 1.0,
                    liquidation_price=_safe_liq_price(row.get("liquidation_price")),
                )
            )

        return AccountSnapshot(
            exchange=self.exchange,
            equity_usd=_safe_float(account_data.get("account_equity")),
            available_margin_usd=_safe_float(account_data.get("available_to_spend")),
            maintenance_margin_usd=_safe_float(account_data.get("cross_mmr")),
            positions=positions,
            updated_at=utc_now(),
        )


class HyperliquidRealConnector(_BaseRealConnector):
    exchange = "hyperliquid"

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        settings = get_settings()
        credentials = _runtime_credentials(self.exchange)
        user = (credentials.get("user_address") or settings.hyperliquid_user_address).strip()
        dex_raw = credentials.get("dex") or settings.hyperliquid_dex
        dexes = [part.strip() for part in dex_raw.split(",") if part.strip()]
        if not dexes:
            dexes = [""]
        if not user:
            raise RealConnectorNotConfiguredError(
                "hyperliquid user address is not configured (HYPERLIQUID_USER_ADDRESS)"
            )

        positions: list[Position] = []
        perps_equity = 0.0
        available = 0.0
        maintenance_margin = 0.0

        for dex in dexes:
            state_body = {"type": "clearinghouseState", "user": user}
            if dex:
                state_body["dex"] = dex

            state_payload = await self._post(
                base_url=settings.hyperliquid_api_base,
                path="/info",
                body=state_body,
                headers={"Content-Type": "application/json"},
            )
            meta_body = {"type": "metaAndAssetCtxs"}
            if dex:
                meta_body["dex"] = dex
            meta_ctx_payload = await self._post(
                base_url=settings.hyperliquid_api_base,
                path="/info",
                body=meta_body,
                headers={"Content-Type": "application/json"},
            )

            universe = []
            ctxs = []
            if isinstance(meta_ctx_payload, list) and len(meta_ctx_payload) >= 2:
                meta = meta_ctx_payload[0] or {}
                universe = meta.get("universe") or []
                ctxs = meta_ctx_payload[1] or []

            coin_to_mark: dict[str, float] = {}
            for idx, item in enumerate(universe):
                if idx >= len(ctxs):
                    break
                coin = str(item.get("name", ""))
                if not coin:
                    continue
                coin_to_mark[coin] = _safe_float(ctxs[idx].get("markPx"))

            raw_positions = state_payload.get("assetPositions") or []
            dex_estimated_maintenance_margin = 0.0
            for row in raw_positions:
                position_data = row.get("position") or {}
                coin = str(position_data.get("coin", ""))
                size_signed = _safe_float(position_data.get("szi"))
                if coin == "" or size_signed == 0:
                    continue

                side = "long" if size_signed > 0 else "short"
                size = abs(size_signed)
                mark_price = coin_to_mark.get(coin) or _safe_float(position_data.get("markPx"))
                entry_price = _safe_float(position_data.get("entryPx"), default=mark_price)
                leverage = _safe_float((position_data.get("leverage") or {}).get("value"), default=1.0)
                liq_price = _safe_liq_price(position_data.get("liquidationPx"))
                pos_notional = abs(size_signed * mark_price)
                effective_leverage = leverage if leverage > 0 else 1.0
                dex_estimated_maintenance_margin += (pos_notional / effective_leverage) * 0.05

                positions.append(
                    Position(
                        exchange=self.exchange,
                        symbol=f"{coin}-PERP",
                        side=side,
                        size=size,
                        entry_price=entry_price,
                        mark_price=mark_price,
                        leverage=leverage if leverage > 0 else 1.0,
                        liquidation_price=liq_price,
                    )
                )

            margin_summary = state_payload.get("marginSummary") or {}
            dex_equity = _safe_float(margin_summary.get("accountValue"))
            dex_maintenance_margin = _safe_float(
                state_payload.get("crossMaintenanceMarginUsed"), default=dex_estimated_maintenance_margin
            )

            perps_equity += dex_equity
            maintenance_margin += dex_maintenance_margin
            if "withdrawable" in state_payload:
                available += _safe_float(state_payload.get("withdrawable"))
            else:
                available += max(dex_equity - dex_maintenance_margin, 0.0)

        spot_state = await self._post(
            base_url=settings.hyperliquid_api_base,
            path="/info",
            body={"type": "spotClearinghouseState", "user": user},
            headers={"Content-Type": "application/json"},
        )
        spot_meta = await self._post(
            base_url=settings.hyperliquid_api_base,
            path="/info",
            body={"type": "spotMeta"},
            headers={"Content-Type": "application/json"},
        )
        all_mids = await self._post(
            base_url=settings.hyperliquid_api_base,
            path="/info",
            body={"type": "allMids"},
            headers={"Content-Type": "application/json"},
        )

        spot_portfolio_value = _hyperliquid_spot_portfolio_value(spot_state, spot_meta, all_mids)
        equity = spot_portfolio_value if spot_portfolio_value > 0 else perps_equity

        return AccountSnapshot(
            exchange=self.exchange,
            equity_usd=equity,
            available_margin_usd=available,
            maintenance_margin_usd=maintenance_margin,
            positions=positions,
            updated_at=utc_now(),
        )


class TxflowRealConnector(_BaseRealConnector):
    exchange = "txflow"

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        settings = get_settings()
        credentials = _runtime_credentials(self.exchange)
        user = (credentials.get("user_address") or settings.txflow_user_address).strip()
        if not user:
            raise RealConnectorNotConfiguredError(
                "txflow user address is not configured (TXFLOW_USER_ADDRESS)"
            )

        state_payload = await self._post(
            base_url=settings.txflow_api_base,
            path="/info",
            body={"type": "clearinghouseState", "user": user},
            headers={"Content-Type": "application/json"},
        )

        margin_summary = state_payload.get("crossMarginSummary") or state_payload.get("marginSummary") or {}
        positions: list[Position] = []
        for row in state_payload.get("assetPositions") or []:
            position_data = row.get("position") or {}
            symbol = str(position_data.get("coin") or "").strip()
            size_signed = _safe_float(position_data.get("szi"))
            if not symbol or size_signed == 0:
                continue

            side = "long" if size_signed > 0 else "short"
            size = abs(size_signed)
            mark_price = _safe_float(position_data.get("markPx"))
            entry_price = _safe_float(position_data.get("entryPx"), default=mark_price)
            leverage_data = position_data.get("leverage") or {}
            leverage = _safe_float(leverage_data.get("value"), default=1.0)

            positions.append(
                Position(
                    exchange=self.exchange,
                    symbol=symbol,
                    side=side,
                    size=size,
                    entry_price=entry_price,
                    mark_price=mark_price,
                    leverage=leverage if leverage > 0 else 1.0,
                    liquidation_price=_safe_liq_price(position_data.get("liquidationPx")),
                )
            )

        equity = _safe_float(margin_summary.get("accountValue"))
        available = _safe_float(state_payload.get("withdrawable"))
        maintenance_margin = _safe_float(
            state_payload.get("crossMaintenanceMarginUsed"),
            default=_safe_float(margin_summary.get("totalMarginUsed")),
        )

        return AccountSnapshot(
            exchange=self.exchange,
            equity_usd=equity,
            available_margin_usd=available,
            maintenance_margin_usd=maintenance_margin,
            positions=positions,
            updated_at=utc_now(),
        )


class OndoRealConnector(_BaseRealConnector):
    exchange = "ondo"

    def _build_auth_headers(self, method: str, path: str, body: str = "") -> dict[str, str]:
        settings = get_settings()
        credentials = _runtime_credentials(self.exchange)
        api_key = credentials.get("api_key") or settings.ondo_api_key
        api_secret = credentials.get("api_secret") or settings.ondo_api_secret
        if not (api_key and api_secret):
            raise RealConnectorNotConfiguredError(
                "ondo credentials are not configured (ONDO_API_KEY/ONDO_API_SECRET)"
            )

        timestamp_ms = str(int(time.time() * 1000))
        payload = f"{timestamp_ms}{method.upper()}{path}{body}"
        signature = hmac.new(api_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
        return {
            "ONDO-KEY-ID": api_key,
            "ONDO-TIMESTAMP": timestamp_ms,
            "ONDO-SIGN": signature,
            "Accept": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
        }

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        settings = get_settings()
        balance_payload = await self._get(
            base_url=settings.ondo_api_base,
            path="/v1/perps/balance",
            headers=self._build_auth_headers("GET", "/v1/perps/balance"),
        )
        if balance_payload.get("success") is not True:
            raise RealConnectorRequestError(f"ondo balance error: {balance_payload}")

        positions_payload = await self._get(
            base_url=settings.ondo_api_base,
            path="/v1/perps/positions",
            headers=self._build_auth_headers("GET", "/v1/perps/positions"),
        )
        if positions_payload.get("success") is not True:
            raise RealConnectorRequestError(f"ondo positions error: {positions_payload}")

        balance_data = balance_payload.get("result") or {}
        raw_positions = positions_payload.get("result") or []

        positions: list[Position] = []
        for row in raw_positions:
            side = str(row.get("direction") or "").strip().lower()
            size = abs(_safe_float(row.get("netQuantity")))
            if side not in {"long", "short"} or size <= 0:
                continue

            positions.append(
                Position(
                    exchange=self.exchange,
                    symbol=str(row.get("market") or "UNKNOWN"),
                    side=side,
                    size=size,
                    entry_price=_safe_float(row.get("averageEntryPrice")),
                    mark_price=_safe_float(row.get("markPrice")),
                    leverage=_safe_float(row.get("leverage"), default=1.0),
                    liquidation_price=_safe_liq_price(row.get("liquidationPrice")),
                )
            )

        return AccountSnapshot(
            exchange=self.exchange,
            equity_usd=_safe_float(balance_data.get("marginBalance")),
            available_margin_usd=_safe_float(balance_data.get("availableMargin")),
            maintenance_margin_usd=_safe_float(balance_data.get("totalMaintenanceMargin")),
            positions=positions,
            updated_at=utc_now(),
        )


class RisexRealConnector(_BaseRealConnector):
    exchange = "risex"

    @staticmethod
    def _default_headers() -> dict[str, str]:
        return {
            "Accept": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
        }

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        settings = get_settings()
        credentials = _runtime_credentials(self.exchange)
        account = (credentials.get("account") or settings.risex_account).strip()
        if not account:
            raise RealConnectorNotConfiguredError(
                "risex account is not configured (RISEX_ACCOUNT)"
            )

        headers = self._default_headers()
        markets_payload = await self._get(
            base_url=settings.risex_api_base,
            path="/v1/markets",
            headers=headers,
        )
        portfolio_payload = await self._get(
            base_url=settings.risex_api_base,
            path="/v1/portfolio/details",
            params={"account": account},
            headers=headers,
        )

        markets_data = ((markets_payload or {}).get("data") or {}).get("markets") or []
        portfolio_data = (portfolio_payload or {}).get("data") or {}
        summary = portfolio_data.get("summary") or {}
        raw_positions = portfolio_data.get("positions") or []

        market_names: dict[str, str] = {}
        for row in markets_data:
            market_id = str(row.get("market_id") or "").strip()
            display_name = str(row.get("display_name") or row.get("base_asset_symbol") or "").strip()
            if market_id and display_name:
                market_names[market_id] = display_name

        positions: list[Position] = []
        for row in raw_positions:
            raw_size = _safe_float(row.get("size"))
            size = abs(raw_size)
            if size <= 0:
                continue

            side_value = str(row.get("side") or "").strip().upper()
            if side_value in {"0", "BUY", "LONG"}:
                side = "long"
            elif side_value in {"1", "SELL", "SHORT"}:
                side = "short"
            else:
                side = "short" if raw_size < 0 else "long"

            market_id = str(row.get("market_id") or "").strip()
            symbol = str(row.get("market_name") or "").strip() or market_names.get(market_id) or "UNKNOWN"
            positions.append(
                Position(
                    exchange=self.exchange,
                    symbol=symbol,
                    side=side,
                    size=size,
                    entry_price=_safe_float(row.get("avg_entry_price")),
                    mark_price=_safe_float(row.get("mark_price")),
                    leverage=_safe_float(row.get("leverage"), default=1.0),
                    liquidation_price=_safe_liq_price(row.get("liquidation_price")),
                )
            )

        return AccountSnapshot(
            exchange=self.exchange,
            equity_usd=_safe_float(summary.get("total_account_value")),
            available_margin_usd=_safe_float(summary.get("free_collateral")),
            maintenance_margin_usd=_safe_float(summary.get("total_maintenance_margin")),
            positions=positions,
            updated_at=utc_now(),
        )


class VariationalRealConnector(_BaseRealConnector):
    exchange = "variational"

    @staticmethod
    def _extract_address_from_token(vr_token: str) -> str:
        try:
            payload = vr_token.split(".")[1]
            padding = "=" * (-len(payload) % 4)
            decoded = json.loads(base64.urlsafe_b64decode(payload + padding).decode("utf-8"))
            return str(decoded.get("address") or "").strip().lower()
        except Exception:
            return ""

    @classmethod
    def _default_headers(cls, vr_token: str) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            "Origin": "https://omni.variational.io",
            "Referer": "https://omni.variational.io/",
            "Cookie": f"vr-token={vr_token}",
        }
        address = cls._extract_address_from_token(vr_token)
        if address:
            headers["vr-connected-address"] = address
        return headers

    @staticmethod
    def _instrument_symbol(row: dict, info: dict) -> str:
        instrument = info.get("instrument") or row.get("instrument") or {}
        if isinstance(instrument, dict):
            underlying = str(instrument.get("underlying") or "").strip().upper()
            instrument_type = str(instrument.get("instrument_type") or "").strip().lower()
            if underlying and instrument_type == "perpetual_future":
                return f"{underlying}-PERP"
            if underlying:
                return underlying
        if isinstance(instrument, str) and instrument.strip():
            return instrument.strip()
        return "UNKNOWN"

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        settings = get_settings()
        credentials = _runtime_credentials(self.exchange)
        vr_token = (credentials.get("vr_token") or settings.variational_vr_token).strip()
        if not vr_token:
            raise RealConnectorNotConfiguredError(
                "variational vr-token is not configured (VARIATIONAL_VR_TOKEN)"
            )

        headers = self._default_headers(vr_token)
        portfolio = await self._get(
            base_url=settings.variational_api_base,
            path="/api/portfolio",
            params={"compute_margin": "true"},
            headers=headers,
        )
        raw_positions = await self._get(
            base_url=settings.variational_api_base,
            path="/api/positions",
            headers=headers,
        )

        portfolio = portfolio if isinstance(portfolio, dict) else {}
        margin_usage = portfolio.get("margin_usage") or {}
        positions_payload = raw_positions if isinstance(raw_positions, list) else []

        equity = _safe_float(portfolio.get("balance")) + _safe_float(portfolio.get("upnl"))
        initial_margin = _safe_float(margin_usage.get("initial_margin"))
        maintenance_margin = _safe_float(margin_usage.get("maintenance_margin"))
        available_margin = max(0.0, equity - initial_margin)

        positions: list[Position] = []
        for row in positions_payload:
            if not isinstance(row, dict):
                continue
            info = row.get("position_info") or {}
            raw_qty = _safe_float(info.get("qty"))
            size = abs(raw_qty)
            if size <= 0:
                continue

            side = "short" if raw_qty < 0 else "long"
            mark_price = _safe_float((row.get("price_info") or {}).get("price") or row.get("mark_px"))
            notional_value = abs(_safe_float(row.get("value")))
            leverage = notional_value / equity if equity > 0 else 0.0

            positions.append(
                Position(
                    exchange=self.exchange,
                    symbol=self._instrument_symbol(row, info),
                    side=side,
                    size=size,
                    entry_price=_safe_float(info.get("avg_entry_price") or row.get("average_open_price")),
                    mark_price=mark_price,
                    leverage=leverage,
                    liquidation_price=_safe_liq_price(
                        row.get("estimated_liquidation_price") or info.get("liquidation_px") or row.get("liquidation_px")
                    ),
                )
            )

        return AccountSnapshot(
            exchange=self.exchange,
            equity_usd=equity,
            available_margin_usd=available_margin,
            maintenance_margin_usd=maintenance_margin,
            positions=positions,
            updated_at=utc_now(),
        )


class ExtendedRealConnector(_BaseRealConnector):
    exchange = "extended"

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        settings = get_settings()
        credentials = _runtime_credentials(self.exchange)
        api_key = credentials.get("api_key") or settings.extended_api_key
        if not api_key:
            raise RealConnectorNotConfiguredError(
                "extended credentials are not configured (EXTENDED_API_KEY)"
            )

        headers = {"X-Api-Key": api_key}

        account_payload = await self._get(
            base_url=settings.extended_api_base,
            path="/api/v1/user/account/info",
            headers=headers,
        )
        if account_payload.get("status") != "OK":
            raise RealConnectorRequestError(f"extended account error: {account_payload}")

        balance_payload = await self._get(
            base_url=settings.extended_api_base,
            path="/api/v1/user/balance",
            headers=headers,
        )
        if balance_payload.get("status") != "OK":
            raise RealConnectorRequestError(f"extended balance error: {balance_payload}")

        spot_balances_payload = await self._get(
            base_url=settings.extended_api_base,
            path="/api/v1/user/spot/balances",
            headers=headers,
        )
        if spot_balances_payload.get("status") != "OK":
            raise RealConnectorRequestError(f"extended spot balances error: {spot_balances_payload}")

        positions_payload = await self._get(
            base_url=settings.extended_api_base,
            path="/api/v1/user/positions",
            headers=headers,
        )
        if positions_payload.get("status") != "OK":
            raise RealConnectorRequestError(f"extended positions error: {positions_payload}")

        account_data = account_payload.get("data") or {}
        balance_data = balance_payload.get("data") or {}
        spot_balances = spot_balances_payload.get("data") or []
        raw_positions = positions_payload.get("data") or []

        positions: list[Position] = []
        for row in raw_positions:
            size = _safe_float(row.get("size"))
            if size <= 0:
                continue

            side = "short" if str(row.get("side", "")).upper() == "SHORT" else "long"
            positions.append(
                Position(
                    exchange=self.exchange,
                    symbol=str(row.get("market", "UNKNOWN")),
                    side=side,
                    size=size,
                    entry_price=_safe_float(row.get("openPrice")),
                    mark_price=_safe_float(row.get("markPrice")),
                    leverage=_safe_float(row.get("leverage"), default=1.0),
                    liquidation_price=_safe_liq_price(row.get("liquidationPrice")),
                )
            )

        portfolio_value = sum(_safe_float(row.get("notionalValue")) for row in spot_balances)
        equity = portfolio_value + _safe_float(balance_data.get("unrealisedPnl"))
        available = _safe_float(balance_data.get("availableForTrade"))
        maintenance = _safe_float(balance_data.get("equity")) * _safe_float(balance_data.get("marginRatio"))

        # Keep account payload consumed/validated even though the current snapshot
        # mapping is driven by balance + positions.
        _ = account_data

        return AccountSnapshot(
            exchange=self.exchange,
            equity_usd=equity,
            available_margin_usd=available,
            maintenance_margin_usd=maintenance,
            positions=positions,
            updated_at=utc_now(),
        )


class OkxRealConnector(_BaseRealConnector):
    exchange = "okx"

    def _okx_timestamp(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def _sign(self, secret: str, payload: str) -> str:
        return base64.b64encode(
            hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
        ).decode("utf-8")

    def _build_okx_headers(
        self,
        api_key: str,
        api_secret: str,
        passphrase: str,
        method: str,
        path_with_query: str,
        body: str = "",
    ) -> dict[str, str]:
        ts = self._okx_timestamp()
        pre_hash = f"{ts}{method.upper()}{path_with_query}{body}"
        sign = self._sign(api_secret, pre_hash)
        return {
            "OK-ACCESS-KEY": api_key,
            "OK-ACCESS-SIGN": sign,
            "OK-ACCESS-TIMESTAMP": ts,
            "OK-ACCESS-PASSPHRASE": passphrase,
            "Content-Type": "application/json",
        }

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        settings = get_settings()
        credentials = _runtime_credentials(self.exchange)
        api_key = credentials.get("api_key") or settings.okx_api_key
        api_secret = credentials.get("api_secret") or settings.okx_api_secret
        api_passphrase = credentials.get("api_passphrase") or settings.okx_api_passphrase
        if not (api_key and api_secret and api_passphrase):
            raise RealConnectorNotConfiguredError(
                "okx credentials are not configured (OKX_API_KEY/SECRET/PASSPHRASE)"
            )

        balance_path = "/api/v5/account/balance"
        balance_headers = self._build_okx_headers(
            api_key=api_key,
            api_secret=api_secret,
            passphrase=api_passphrase,
            method="GET",
            path_with_query=balance_path,
        )
        balance_payload = await self._get(
            base_url=settings.okx_api_base,
            path=balance_path,
            headers=balance_headers,
        )

        if str(balance_payload.get("code")) != "0":
            raise RealConnectorRequestError(f"okx balance error: {balance_payload}")

        account_data = (balance_payload.get("data") or [{}])[0]

        pos_params = {"instType": "SWAP"}
        pos_query = urlencode(pos_params)
        pos_path = "/api/v5/account/positions"
        pos_path_with_query = f"{pos_path}?{pos_query}"
        pos_headers = self._build_okx_headers(
            api_key=api_key,
            api_secret=api_secret,
            passphrase=api_passphrase,
            method="GET",
            path_with_query=pos_path_with_query,
        )
        pos_payload = await self._get(
            base_url=settings.okx_api_base,
            path=pos_path,
            params=pos_params,
            headers=pos_headers,
        )
        if str(pos_payload.get("code")) != "0":
            raise RealConnectorRequestError(f"okx positions error: {pos_payload}")

        positions: list[Position] = []
        for row in pos_payload.get("data") or []:
            pos_raw = _safe_float(row.get("pos"))
            if pos_raw == 0:
                continue

            pos_side = str(row.get("posSide", "net")).lower()
            if pos_side == "long":
                side = "long"
                size = abs(pos_raw)
            elif pos_side == "short":
                side = "short"
                size = abs(pos_raw)
            else:
                side = "long" if pos_raw > 0 else "short"
                size = abs(pos_raw)

            mark = _safe_float(row.get("markPx"))
            entry = _safe_float(row.get("avgPx"), default=mark)
            leverage = _safe_float(row.get("lever"), default=1.0)

            positions.append(
                Position(
                    exchange=self.exchange,
                    symbol=str(row.get("instId", "UNKNOWN")),
                    side=side,
                    size=size,
                    entry_price=entry,
                    mark_price=mark,
                    leverage=leverage if leverage > 0 else 1.0,
                    liquidation_price=_safe_liq_price(row.get("liqPx")),
                )
            )

        equity = _safe_float(account_data.get("totalEq"))
        available = _safe_float(account_data.get("availEq"))
        if available <= 0:
            details = account_data.get("details") or []
            available = sum(_safe_float(item.get("availEq")) for item in details)

        maintenance = _safe_float(account_data.get("mmr"))

        return AccountSnapshot(
            exchange=self.exchange,
            equity_usd=equity,
            available_margin_usd=available,
            maintenance_margin_usd=maintenance,
            positions=positions,
            updated_at=utc_now(),
        )


class KucoinRealConnector(_BaseRealConnector):
    exchange = "kucoin"

    def _sign(self, secret: str, payload: str) -> str:
        return base64.b64encode(
            hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
        ).decode("utf-8")

    def _build_kucoin_headers(
        self,
        api_key: str,
        api_secret: str,
        passphrase: str,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        timestamp_ms = str(int(datetime.now(timezone.utc).timestamp() * 1000))
        query = urlencode(params or {})
        endpoint = path + (f"?{query}" if query else "")
        pre_hash = f"{timestamp_ms}{method.upper()}{endpoint}"
        signed_passphrase = self._sign(api_secret, passphrase)
        signature = self._sign(api_secret, pre_hash)
        return {
            "KC-API-KEY": api_key,
            "KC-API-SIGN": signature,
            "KC-API-TIMESTAMP": timestamp_ms,
            "KC-API-PASSPHRASE": signed_passphrase,
            "KC-API-KEY-VERSION": "2",
            "Content-Type": "application/json",
        }

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        settings = get_settings()
        credentials = _runtime_credentials(self.exchange)
        api_key = credentials.get("api_key") or settings.kucoin_api_key
        api_secret = credentials.get("api_secret") or settings.kucoin_api_secret
        api_passphrase = credentials.get("api_passphrase") or settings.kucoin_api_passphrase
        if not (api_key and api_secret and api_passphrase):
            raise RealConnectorNotConfiguredError(
                "kucoin credentials are not configured (KUCOIN_API_KEY/SECRET/PASSPHRASE)"
            )

        account_params = {"currency": "USDT"}
        account_path = "/api/v1/account-overview"
        account_headers = self._build_kucoin_headers(
            api_key=api_key,
            api_secret=api_secret,
            passphrase=api_passphrase,
            method="GET",
            path=account_path,
            params=account_params,
        )
        account_payload = await self._get(
            base_url=settings.kucoin_api_base,
            path=account_path,
            params=account_params,
            headers=account_headers,
        )
        if str(account_payload.get("code")) != "200000":
            raise RealConnectorRequestError(f"kucoin account error: {account_payload}")

        positions_path = "/api/v1/positions"
        positions_headers = self._build_kucoin_headers(
            api_key=api_key,
            api_secret=api_secret,
            passphrase=api_passphrase,
            method="GET",
            path=positions_path,
        )
        positions_payload = await self._get(
            base_url=settings.kucoin_api_base,
            path=positions_path,
            headers=positions_headers,
        )
        if str(positions_payload.get("code")) != "200000":
            raise RealConnectorRequestError(f"kucoin positions error: {positions_payload}")

        contracts_path = "/api/v1/contracts/active"
        contracts_payload = await self._get(
            base_url=settings.kucoin_api_base,
            path=contracts_path,
        )
        if str(contracts_payload.get("code")) != "200000":
            raise RealConnectorRequestError(f"kucoin contracts error: {contracts_payload}")

        account_data = account_payload.get("data") or {}
        raw_positions = positions_payload.get("data") or []
        contracts = contracts_payload.get("data") or []
        symbol_to_multiplier = {
            str(row.get("symbol", "")): _safe_float(row.get("multiplier"), default=1.0)
            for row in contracts
            if str(row.get("symbol", ""))
        }

        positions: list[Position] = []
        for row in raw_positions:
            qty_signed = _safe_float(row.get("currentQty"))
            if qty_signed == 0:
                continue

            symbol = str(row.get("symbol", "UNKNOWN"))
            multiplier = symbol_to_multiplier.get(symbol, 1.0)
            side = "long" if qty_signed > 0 else "short"
            size = abs(qty_signed) * multiplier
            mark = _safe_float(row.get("markPrice"))
            entry = _safe_float(row.get("avgEntryPrice"), default=mark)
            leverage = _safe_float(row.get("realLeverage"), default=1.0)

            positions.append(
                Position(
                    exchange=self.exchange,
                    symbol=symbol,
                    side=side,
                    size=size,
                    entry_price=entry,
                    mark_price=mark,
                    leverage=leverage if leverage > 0 else 1.0,
                    liquidation_price=_safe_liq_price(row.get("liquidationPrice")),
                )
            )

        equity = _safe_float(account_data.get("accountEquity"))
        if equity <= 0:
            equity = _safe_float(account_data.get("marginBalance"))

        available = _safe_float(account_data.get("availableBalance"))
        if available <= 0:
            available = _safe_float(account_data.get("availableFunds"))

        maintenance = _safe_float(account_data.get("maintMarginReq"))
        if maintenance <= 0:
            maintenance = _safe_float(account_data.get("positionMargin")) + _safe_float(
                account_data.get("orderMargin")
            )

        return AccountSnapshot(
            exchange=self.exchange,
            equity_usd=equity,
            available_margin_usd=available,
            maintenance_margin_usd=maintenance,
            positions=positions,
            updated_at=utc_now(),
        )


class VestRealConnector(_BaseRealConnector):
    exchange = "vest"

    def _build_headers(self, api_key: str, account_group: str) -> dict[str, str]:
        return {
            "X-API-KEY": api_key,
            "xrestservermm": f"restserver{account_group}",
            "Accept": "application/json",
        }

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        settings = get_settings()
        credentials = _runtime_credentials(self.exchange)
        api_key = credentials.get("api_key") or settings.vest_api_key
        account_group = credentials.get("account_group") or settings.vest_account_group
        if not (api_key and account_group):
            raise RealConnectorNotConfiguredError(
                "vest credentials are not configured (VEST_API_KEY/VEST_ACCOUNT_GROUP)"
            )

        payload = await self._get(
            base_url=settings.vest_api_base,
            path="/account",
            params={"time": int(datetime.now(timezone.utc).timestamp() * 1000)},
            headers=self._build_headers(api_key=api_key, account_group=account_group),
        )
        if not isinstance(payload, dict) or not payload:
            raise RealConnectorRequestError(f"vest account error: {payload}")

        leverage_by_symbol = {
            str(row.get("symbol", "")): _safe_float(row.get("value"), default=0.0)
            for row in (payload.get("leverages") or [])
            if str(row.get("symbol", ""))
        }

        positions: list[Position] = []
        for row in payload.get("positions") or []:
            size_signed = _safe_float(row.get("size"))
            if size_signed == 0:
                continue

            symbol = str(row.get("symbol") or "UNKNOWN")
            side = "long" if bool(row.get("isLong", size_signed > 0)) else "short"
            size = abs(size_signed)
            mark_price = _safe_float(row.get("markPrice"))
            entry_price = _safe_float(row.get("entryPrice"), default=mark_price)
            leverage = leverage_by_symbol.get(symbol, 0.0)
            if leverage <= 0:
                init_margin_ratio = _safe_float(row.get("initMarginRatio"), default=0.0)
                leverage = 1.0 / init_margin_ratio if init_margin_ratio > 0 else 1.0

            positions.append(
                Position(
                    exchange=self.exchange,
                    symbol=symbol,
                    side=side,
                    size=size,
                    entry_price=entry_price,
                    mark_price=mark_price,
                    leverage=leverage if leverage > 0 else 1.0,
                    liquidation_price=_safe_liq_price(row.get("liqPrice")),
                )
            )

        return AccountSnapshot(
            exchange=self.exchange,
            equity_usd=_safe_float(payload.get("totalAccountValue")),
            available_margin_usd=_safe_float(payload.get("withdrawable")),
            maintenance_margin_usd=_safe_float(payload.get("totalMaintMargin")),
            positions=positions,
            updated_at=utc_now(),
        )


class LighterRealConnector(_BaseRealConnector):
    exchange = "lighter"
    api_base_setting = "lighter_api_base"
    account_index_setting = "lighter_account_index"
    l1_address_setting = "lighter_l1_address"
    account_index_env = "LIGHTER_ACCOUNT_INDEX"
    l1_address_env = "LIGHTER_L1_ADDRESS"

    async def _resolve_account_index(self, base_url: str, l1_address: str) -> str:
        payload = await self._get(
            base_url=base_url,
            path="/api/v1/accountsByL1Address",
            params={"l1_address": l1_address},
            headers={"accept": "application/json"},
        )
        sub_accounts = payload.get("sub_accounts") or []
        if not isinstance(sub_accounts, list) or not sub_accounts:
            raise RealConnectorRequestError(f"lighter account lookup returned no accounts for {l1_address}")
        if len(sub_accounts) != 1:
            indexes = ", ".join(str(item.get("index")) for item in sub_accounts if item.get("index") is not None)
            raise RealConnectorNotConfiguredError(
                "lighter account_index is required when one wallet has multiple accounts"
                + (f" (available indexes: {indexes})" if indexes else "")
            )
        index = sub_accounts[0].get("index")
        if index is None:
            raise RealConnectorRequestError(f"lighter account lookup missing index for {l1_address}")
        return str(index)

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        settings = get_settings()
        credentials = _runtime_credentials(self.exchange)
        base_url = str(getattr(settings, self.api_base_setting))
        account_index = credentials.get("account_index") or str(getattr(settings, self.account_index_setting))
        l1_address = credentials.get("l1_address") or str(getattr(settings, self.l1_address_setting))
        if not account_index and not l1_address:
            raise RealConnectorNotConfiguredError(
                f"{self.exchange} credentials are not configured "
                f"({self.account_index_env} or {self.l1_address_env})"
            )
        if not account_index and l1_address:
            account_index = await self._resolve_account_index(base_url, l1_address)

        payload = await self._get(
            base_url=base_url,
            path="/api/v1/account",
            params={"by": "index", "value": str(account_index), "active_only": True},
            headers={"accept": "application/json"},
        )
        accounts = payload.get("accounts") or []
        if not isinstance(accounts, list) or not accounts:
            raise RealConnectorRequestError(f"lighter account error: {payload}")
        account = accounts[0]

        positions: list[Position] = []
        for row in account.get("positions") or []:
            size = abs(_safe_float(row.get("position")))
            if size <= 0:
                continue
            sign = int(_safe_float(row.get("sign"), default=0.0))
            side = "short" if sign < 0 else "long"
            entry_price = _safe_float(row.get("avg_entry_price"))
            position_value = abs(_safe_float(row.get("position_value")))
            mark_price = position_value / size if size > 0 and position_value > 0 else entry_price
            allocated_margin = _safe_float(row.get("allocated_margin"))
            if allocated_margin > 0 and position_value > 0:
                leverage = position_value / allocated_margin
            else:
                initial_margin_fraction = _safe_float(row.get("initial_margin_fraction"))
                leverage = 100.0 / initial_margin_fraction if initial_margin_fraction > 0 else 1.0

            positions.append(
                Position(
                    exchange=self.exchange,
                    symbol=str(row.get("symbol") or "UNKNOWN"),
                    side=side,
                    size=size,
                    entry_price=entry_price,
                    mark_price=mark_price if mark_price > 0 else entry_price,
                    leverage=leverage if leverage > 0 else 1.0,
                    liquidation_price=_safe_liq_price(row.get("liquidation_price")),
                )
            )

        equity = _safe_float(account.get("total_asset_value"))
        if equity <= 0:
            equity = _safe_float(account.get("collateral")) + sum(position.pnl_usd for position in positions)

        maintenance = _safe_float(account.get("cross_maintenance_margin_requirement"))
        if maintenance <= 0:
            maintenance = sum(
                _safe_float(row.get("allocated_margin"))
                for row in account.get("positions") or []
                if _safe_float(row.get("position")) != 0
            )

        return AccountSnapshot(
            exchange=self.exchange,
            equity_usd=equity,
            available_margin_usd=_safe_float(account.get("available_balance")),
            maintenance_margin_usd=maintenance,
            positions=positions,
            updated_at=utc_now(),
        )


class LighterRHRealConnector(LighterRealConnector):
    exchange = "lighter-rh"
    api_base_setting = "lighter_rh_api_base"
    account_index_setting = "lighter_rh_account_index"
    l1_address_setting = "lighter_rh_l1_address"
    account_index_env = "LIGHTER_RH_ACCOUNT_INDEX"
    l1_address_env = "LIGHTER_RH_L1_ADDRESS"


def _nado_estimated_liquidation_price(
    amount: float,
    oracle_price: float,
    maintenance_health: float,
    long_weight_maintenance: float,
    short_weight_maintenance: float,
) -> float | None:
    """Mirror Nado web's estimated liquidation-price calculation."""
    if amount == 0 or oracle_price <= 0 or maintenance_health < 0:
        return None
    if amount > 0:
        if long_weight_maintenance <= 0:
            return None
        price = oracle_price - maintenance_health / amount / long_weight_maintenance
        return price if price > 0 else None
    if short_weight_maintenance <= 0:
        return None
    price = oracle_price + maintenance_health / abs(amount) / short_weight_maintenance
    return price if price < oracle_price * 10 else None


class NadoRealConnector(_BaseRealConnector):
    exchange = "nado"
    _X18 = 10**18

    @classmethod
    def _health_metrics(cls, healths: Any) -> tuple[float, float, float]:
        if not isinstance(healths, list) or len(healths) < 3:
            return 0.0, 0.0, 0.0
        scale = Decimal(cls._X18)
        initial_health = float(_nado_decimal((healths[0] or {}).get("health")) / scale)
        maintenance_health = float(_nado_decimal((healths[1] or {}).get("health")) / scale)
        equity = float(_nado_decimal((healths[2] or {}).get("health")) / scale)
        return equity, max(initial_health, 0.0), max(equity - maintenance_health, 0.0)

    def _unwrap_query(self, payload: Any, label: str) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise RealConnectorRequestError(f"nado {label} returned malformed payload")
        if payload.get("status") not in (None, "success"):
            raise RealConnectorRequestError(f"nado {label} error: {payload}")
        data = payload.get("data", payload)
        if not isinstance(data, dict):
            raise RealConnectorRequestError(f"nado {label} returned malformed data")
        return data

    def _symbol_map(self, payload: Any) -> dict[int, str]:
        rows = payload
        if isinstance(payload, dict):
            if payload.get("status") not in (None, "success"):
                raise RealConnectorRequestError(f"nado symbols error: {payload}")
            rows = payload.get("data", payload.get("symbols", []))
        if isinstance(rows, dict):
            rows = rows.get("symbols", [])
        if not isinstance(rows, list):
            raise RealConnectorRequestError("nado symbols returned malformed data")

        result: dict[int, str] = {}
        for row in rows:
            if not isinstance(row, dict) or str(row.get("type", "")).lower() != "perp":
                continue
            product_id = int(_safe_float(row.get("product_id"), default=-1))
            symbol = str(row.get("symbol") or "").strip()
            if product_id >= 0 and symbol:
                result[product_id] = symbol
        return result

    def _risk_weights(self, payload: Any) -> dict[int, tuple[float, float]]:
        rows = payload.get("data", payload.get("symbols", [])) if isinstance(payload, dict) else payload
        if isinstance(rows, dict):
            rows = rows.get("symbols", [])
        result: dict[int, tuple[float, float]] = {}
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict) or str(row.get("type", "")).lower() != "perp":
                continue
            product_id = int(_safe_float(row.get("product_id"), default=-1))
            long_weight = float(_nado_decimal(row.get("long_weight_maintenance_x18")) / Decimal(self._X18))
            short_raw = row.get("short_weight_maintenance_x18")
            short_weight = (
                float(_nado_decimal(short_raw) / Decimal(self._X18))
                if short_raw is not None else 2.0 - long_weight
            )
            if product_id >= 0 and long_weight > 0 and short_weight > 0:
                result[product_id] = (long_weight, short_weight)
        return result

    def _oracle_prices(self, payload: Any) -> dict[int, float]:
        data = payload.get("data", {}) if isinstance(payload, dict) and payload.get("status") is not None else payload
        result: dict[int, float] = {}
        for key, row in data.items() if isinstance(data, dict) else []:
            if not isinstance(row, dict):
                continue
            product_id = int(_safe_float(row.get("product_id", key), default=-1))
            oracle = float(_nado_decimal(row.get("oracle_price_x18")) / Decimal(self._X18))
            if product_id >= 0 and oracle > 0:
                result[product_id] = oracle
        return result

    def _mark_prices(self, payload: Any) -> dict[int, float]:
        data = payload
        if isinstance(payload, dict) and payload.get("status") is not None:
            if payload.get("status") != "success":
                raise RealConnectorRequestError(f"nado perp prices error: {payload}")
            data = payload.get("data", {})
        if not isinstance(data, dict):
            raise RealConnectorRequestError("nado perp prices returned malformed data")

        result: dict[int, float] = {}
        for key, row in data.items():
            if not isinstance(row, dict):
                continue
            product_id = int(_safe_float(row.get("product_id", key), default=-1))
            mark_price = float(_nado_decimal(row.get("mark_price_x18")) / Decimal(self._X18))
            if product_id >= 0 and mark_price > 0:
                result[product_id] = mark_price
        return result

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        settings = get_settings()
        credentials = _runtime_credentials(self.exchange)
        wallet_address = (credentials.get("wallet_address") or settings.nado_wallet_address).strip()
        subaccount_name = (credentials.get("subaccount_name") or settings.nado_subaccount_name).strip()
        if not wallet_address:
            raise RealConnectorNotConfiguredError(
                "nado wallet address is not configured (NADO_WALLET_ADDRESS)"
            )
        try:
            subaccount = _nado_subaccount_hex(wallet_address, subaccount_name)
        except ValueError as exc:
            raise RealConnectorNotConfiguredError(f"nado account identifier is invalid: {exc}") from exc

        headers = {"accept": "application/json", "accept-encoding": "gzip"}
        account_payload = await self._get(
            base_url=settings.nado_gateway_api_base,
            path="/query",
            params={"type": "subaccount_info", "subaccount": subaccount},
            headers=headers,
        )
        account = self._unwrap_query(account_payload, "subaccount_info")
        if account.get("exists") is False:
            raise RealConnectorRequestError(
                f"nado subaccount {subaccount_name!r} does not exist for the configured wallet"
            )

        isolated_payload = await self._get(
            base_url=settings.nado_gateway_api_base,
            path="/query",
            params={"type": "isolated_positions", "subaccount": subaccount},
            headers=headers,
        )
        isolated_data = self._unwrap_query(isolated_payload, "isolated_positions")

        balance_rows: list[dict[str, Any]] = [
            row for row in (account.get("perp_balances") or []) if isinstance(row, dict)
        ]
        isolated_health_by_product: dict[int, Any] = {}
        health_groups: list[Any] = [account.get("healths")]
        for isolated in isolated_data.get("isolated_positions") or []:
            if not isinstance(isolated, dict):
                continue
            isolated_healths = isolated.get("healths")
            health_groups.append(isolated_healths)
            base_balance = isolated.get("base_balance")
            if isinstance(base_balance, dict):
                balance_rows.append(base_balance)
                isolated_product_id = int(_safe_float(base_balance.get("product_id"), default=-1))
                if isolated_product_id >= 0:
                    isolated_health_by_product[isolated_product_id] = isolated_healths

        active_rows: list[tuple[int, float, float]] = []
        for row in balance_rows:
            nested_balance = row.get("balance")
            balance: dict[str, Any] = nested_balance if isinstance(nested_balance, dict) else row
            amount_x18 = _nado_decimal(balance.get("amount"))
            if amount_x18 == 0:
                continue
            product_id = int(_safe_float(row.get("product_id", balance.get("product_id")), default=-1))
            if product_id < 0:
                continue
            amount = amount_x18 / Decimal(self._X18)
            v_quote = _nado_decimal(balance.get("v_quote_balance")) / Decimal(self._X18)
            entry_price = abs(-v_quote / amount) if amount else Decimal(0)
            active_rows.append((product_id, float(amount), float(entry_price)))

        symbols_payload = await self._get(
            base_url=settings.nado_gateway_api_base,
            path="/symbols",
            headers=headers,
        )
        symbols = self._symbol_map(symbols_payload)
        risk_weights = self._risk_weights(symbols_payload)

        product_ids = sorted({product_id for product_id, _, _ in active_rows})
        marks: dict[int, float] = {}
        oracles: dict[int, float] = {}
        if product_ids:
            prices_payload = await self._post(
                base_url=settings.nado_archive_api_base,
                path="",
                body={"perp_prices": {"product_ids": product_ids}},
                headers={
                    "content-type": "application/json",
                    "accept": "application/json",
                    "accept-encoding": "gzip",
                },
            )
            marks = self._mark_prices(prices_payload)
            oracles = self._oracle_prices(prices_payload)

        cross_healths = account.get("healths") or []
        cross_maintenance_health = (
            float(_nado_decimal((cross_healths[1] or {}).get("health")) / Decimal(self._X18))
            if len(cross_healths) > 1 else 0.0
        )

        positions = [
            Position(
                exchange=self.exchange,
                symbol=symbols.get(product_id, f"PRODUCT-{product_id}-PERP"),
                side="long" if amount > 0 else "short",
                size=abs(amount),
                entry_price=entry_price,
                mark_price=marks.get(product_id, entry_price),
                leverage=1.0,
                liquidation_price=(
                    _nado_estimated_liquidation_price(
                        amount=amount,
                        oracle_price=oracles.get(product_id, marks.get(product_id, entry_price)),
                        maintenance_health=(
                            float(_nado_decimal((isolated_health_by_product[product_id][1] or {}).get("health")) / Decimal(self._X18))
                            if product_id in isolated_health_by_product
                            and isinstance(isolated_health_by_product[product_id], list)
                            and len(isolated_health_by_product[product_id]) > 1
                            else cross_maintenance_health
                        ),
                        long_weight_maintenance=risk_weights[product_id][0],
                        short_weight_maintenance=risk_weights[product_id][1],
                    )
                    if product_id in risk_weights
                    else None
                ),
            )
            for product_id, amount, entry_price in active_rows
        ]

        equity = 0.0
        available = 0.0
        maintenance_margin = 0.0
        for healths in health_groups:
            group_equity, group_available, group_maintenance = self._health_metrics(healths)
            equity += group_equity
            available += group_available
            maintenance_margin += group_maintenance

        return AccountSnapshot(
            exchange=self.exchange,
            equity_usd=equity,
            available_margin_usd=available,
            maintenance_margin_usd=maintenance_margin,
            positions=positions,
            updated_at=utc_now(),
        )
