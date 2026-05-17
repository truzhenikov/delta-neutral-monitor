from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from src.config import get_settings
from src.connectors.base import ExchangeConnector
from src.core.models import AccountSnapshot, Position, utc_now


class RealConnectorNotConfiguredError(RuntimeError):
    pass


class RealConnectorRequestError(RuntimeError):
    pass


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
        if not (settings.bitget_api_key and settings.bitget_api_secret and settings.bitget_api_passphrase):
            raise RealConnectorNotConfiguredError(
                "bitget credentials are not configured (BITGET_API_KEY/SECRET/PASSPHRASE)"
            )

        params = {"productType": settings.bitget_product_type}
        account_path = "/api/v2/mix/account/accounts"
        account_headers = self._build_signed_headers(
            api_key=settings.bitget_api_key,
            api_secret=settings.bitget_api_secret,
            passphrase=settings.bitget_api_passphrase,
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
            api_key=settings.bitget_api_key,
            api_secret=settings.bitget_api_secret,
            passphrase=settings.bitget_api_passphrase,
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
        if not (settings.bingx_api_key and settings.bingx_api_secret):
            raise RealConnectorNotConfiguredError(
                "bingx credentials are not configured (BINGX_API_KEY/SECRET)"
            )

        signed_params = {"recvWindow": 0, "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000)}
        if params:
            signed_params.update(params)
        query = urlencode(sorted(signed_params.items()))
        signature = hmac.new(
            settings.bingx_api_secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        signed_params["signature"] = signature
        headers = {"X-BX-APIKEY": settings.bingx_api_key}
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


class MexcRealConnector(_BaseRealConnector):
    exchange = "mexc"


class HyperliquidRealConnector(_BaseRealConnector):
    exchange = "hyperliquid"

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        settings = get_settings()
        user = settings.hyperliquid_user_address.strip()
        if not user:
            raise RealConnectorNotConfiguredError(
                "hyperliquid user address is not configured (HYPERLIQUID_USER_ADDRESS)"
            )

        state_payload = await self._post(
            base_url=settings.hyperliquid_api_base,
            path="/info",
            body={"type": "clearinghouseState", "user": user},
            headers={"Content-Type": "application/json"},
        )
        meta_ctx_payload = await self._post(
            base_url=settings.hyperliquid_api_base,
            path="/info",
            body={"type": "metaAndAssetCtxs"},
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

        margin_summary = state_payload.get("marginSummary") or {}
        equity = _safe_float(margin_summary.get("accountValue"))

        raw_positions = state_payload.get("assetPositions") or []
        positions: list[Position] = []
        estimated_maintenance = 0.0

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

            pos_notional = abs(size_signed * mark_price)
            lev = leverage if leverage > 0 else 1.0
            estimated_maintenance += (pos_notional / lev) * 0.05

        available = _safe_float(state_payload.get("withdrawable"))
        if available <= 0:
            available = max(equity - estimated_maintenance, 0.0)

        return AccountSnapshot(
            exchange=self.exchange,
            equity_usd=equity,
            available_margin_usd=available,
            maintenance_margin_usd=estimated_maintenance,
            positions=positions,
            updated_at=utc_now(),
        )


class ExtendedRealConnector(_BaseRealConnector):
    exchange = "extended"


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
        if not (settings.okx_api_key and settings.okx_api_secret and settings.okx_api_passphrase):
            raise RealConnectorNotConfiguredError(
                "okx credentials are not configured (OKX_API_KEY/SECRET/PASSPHRASE)"
            )

        balance_path = "/api/v5/account/balance"
        balance_headers = self._build_okx_headers(
            api_key=settings.okx_api_key,
            api_secret=settings.okx_api_secret,
            passphrase=settings.okx_api_passphrase,
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
            api_key=settings.okx_api_key,
            api_secret=settings.okx_api_secret,
            passphrase=settings.okx_api_passphrase,
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
        if not (settings.kucoin_api_key and settings.kucoin_api_secret and settings.kucoin_api_passphrase):
            raise RealConnectorNotConfiguredError(
                "kucoin credentials are not configured (KUCOIN_API_KEY/SECRET/PASSPHRASE)"
            )

        account_params = {"currency": "USDT"}
        account_path = "/api/v1/account-overview"
        account_headers = self._build_kucoin_headers(
            api_key=settings.kucoin_api_key,
            api_secret=settings.kucoin_api_secret,
            passphrase=settings.kucoin_api_passphrase,
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
            api_key=settings.kucoin_api_key,
            api_secret=settings.kucoin_api_secret,
            passphrase=settings.kucoin_api_passphrase,
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
