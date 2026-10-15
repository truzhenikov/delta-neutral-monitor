from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock

from src.services.credential_store import CredentialStore
from src.services.funding_report_service import FundingReportService, MSK

NOW = datetime(2026, 10, 15, 12, tzinfo=MSK)


def make_service(tmp_path, request):
    store = CredentialStore(tmp_path / 'credentials.json')
    store.set_exchange_credentials('okx:main', {
        'api_key': 'synthetic-key', 'api_secret': 'synthetic-secret', 'api_passphrase': 'synthetic-pass',
    })
    return FundingReportService(store, tmp_path / 'funding.json', request_json=request)


def bill():
    return {'billId': '100', 'ts': '1791980000000', 'type': '8', 'subType': '174',
            'instType': 'SWAP', 'instId': 'BTC-USDT-SWAP', 'ccy': 'USDT',
            'pnl': '1.234', 'balChg': '1.234', 'mgnMode': 'cross'}


def test_okx_is_queried_when_history_empty(tmp_path):
    request = AsyncMock(return_value={'code': '0', 'data': []})
    report = asyncio.run(make_service(tmp_path, request).build_report(NOW))
    assert report.complete and report.rows == []
    request.assert_awaited_once()
    assert '/api/v5/account/bills?' in request.call_args.args[1]


def test_okx_is_included_in_shared_report(tmp_path):
    request = AsyncMock(return_value={'code': '0', 'data': [bill()]})
    report = asyncio.run(make_service(tmp_path, request).build_report(NOW))
    assert report.complete
    assert [(r.exchange, r.symbol, r.amount_usd) for r in report.rows] == [('OKX', 'BTC', Decimal('1.234'))]


def test_okx_failure_preserves_other_venue_and_sanitizes_errors(tmp_path):
    async def request(method, url, **kwargs):
        if url.endswith('/info'):
            return [{'time': kwargs['json']['startTime'] + 1, 'delta': {'coin': 'BTC', 'usdc': '2'}}]
        raise RuntimeError('sensitive upstream details')
    service = make_service(tmp_path, request)
    service.credential_store.set_exchange_credentials('hyperliquid:main', {'user_address': '0xabc'})
    report = asyncio.run(service.build_report(NOW))
    assert not report.complete
    assert [(r.exchange, r.amount_usd) for r in report.rows] == [('Hyperliquid', Decimal('2'))]
    assert any('OKX' in w for w in report.warnings)
    assert 'sensitive upstream details' not in '\n'.join(report.warnings)


def test_disabled_okx_not_queried(tmp_path):
    request = AsyncMock()
    service = make_service(tmp_path, request)
    service.credential_store.set_exchange_enabled('okx:main', False)
    report = asyncio.run(service.build_report(NOW))
    assert report.complete and not report.rows
    request.assert_not_called()
