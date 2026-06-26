from __future__ import annotations

import base64
import collections
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
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
            "PF-API-KEY": api_key,
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
        account_index = credentials.get("account_index") or settings.lighter_account_index
        l1_address = credentials.get("l1_address") or settings.lighter_l1_address
        if not account_index and not l1_address:
            raise RealConnectorNotConfiguredError(
                "lighter credentials are not configured (LIGHTER_ACCOUNT_INDEX or LIGHTER_L1_ADDRESS)"
            )
        if not account_index and l1_address:
            account_index = await self._resolve_account_index(settings.lighter_api_base, l1_address)

        payload = await self._get(
            base_url=settings.lighter_api_base,
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
