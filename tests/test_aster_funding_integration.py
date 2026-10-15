from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock

from eth_account import Account

from src.services.credential_store import CredentialStore
from src.services.funding_report_service import FundingReportService, MSK

NOW = datetime(2026, 10, 15, 12, tzinfo=MSK)
TEST_KEY = '0x' + '11' * 32


def service(tmp_path, request):
    store = CredentialStore(tmp_path / 'credentials.json')
    address = Account.from_key(TEST_KEY).address
    store.set_exchange_credentials('aster:main', {
        'user_address': address, 'signer': address, 'signer_private_key': TEST_KEY,
    })
    return FundingReportService(store, tmp_path / 'funding.json', request_json=request)


def test_aster_empty_history_still_queries_ledger(tmp_path):
    request = AsyncMock(return_value=[])
    report = asyncio.run(service(tmp_path, request).build_report(NOW))
    assert report.complete and not report.rows
    request.assert_awaited_once()
    assert request.call_args.args[1] == 'https://fapi.asterdex.com/fapi/v3/income'


def test_aster_signed_amount_in_shared_report(tmp_path):
    request = AsyncMock(return_value=[{
        'symbol': 'BTCUSDT', 'incomeType': 'FUNDING_FEE', 'income': '-1.25',
        'asset': 'USDT', 'time': 1791980000000, 'tranId': 'one',
    }])
    report = asyncio.run(service(tmp_path, request).build_report(NOW))
    assert report.complete
    assert [(r.exchange, r.symbol, r.amount_usd) for r in report.rows] == [('Aster', 'BTC', Decimal('-1.25'))]


def test_aster_error_preserves_other_venues(tmp_path):
    async def request(method, url, **kwargs):
        if url.endswith('/info'):
            return [{'time': kwargs['json']['startTime'] + 1, 'delta': {'coin': 'BTC', 'usdc': '2'}}]
        raise RuntimeError('sensitive transport payload')
    s = service(tmp_path, request)
    s.credential_store.set_exchange_credentials('hyperliquid:main', {'user_address': '0xabc'})
    report = asyncio.run(s.build_report(NOW))
    assert not report.complete
    assert [(r.exchange, r.amount_usd) for r in report.rows] == [('Hyperliquid', Decimal('2'))]
    assert any('Aster' in w for w in report.warnings)
    assert 'sensitive transport payload' not in '\n'.join(report.warnings)


def test_disabled_aster_is_not_queried(tmp_path):
    request = AsyncMock()
    s = service(tmp_path, request)
    s.credential_store.set_exchange_enabled('aster:main', False)
    report = asyncio.run(s.build_report(NOW))
    assert report.complete and not report.rows
    request.assert_not_called()
