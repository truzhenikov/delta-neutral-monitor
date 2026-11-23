import asyncio
from datetime import datetime
from decimal import Decimal, localcontext
from unittest.mock import AsyncMock

import pytest

from src.services.credential_store import CredentialStore
from src.services.funding_report_service import FundingReportService, MSK

NOW = datetime(2026, 11, 23, 12, tzinfo=MSK)
START, END = FundingReportService.completed_window(NOW)
ACCOUNT = '11111111-1111-4111-8111-111111111111'
SCOPE = {'accounts': [dict(account_id=ACCOUNT, is_master=True, equity=100, available_balance=100)]}


def row(ident='one', **changes):
    result = dict(id=ident, execution_type='funding', symbol='PANW-USD', amount=Decimal('-0.01048746'),
                  currency='USD', created_at=Decimal(str(START.timestamp())), state='completed',
                  transaction_id=ident, metadata={})
    result.update(changes)
    return result


def make(tmp_path, request):
    store = CredentialStore(tmp_path / 'credentials.json')
    store.set_exchange_credentials('qfex:main', dict(api_key='public', api_secret='secret'))
    return FundingReportService(store, tmp_path / 'state.json', request_json=request)


def test_actual_closed_position_ledger_and_boundaries(tmp_path):
    records = [row(), row('two', amount=Decimal('1.23456789')),
               row('end', created_at=Decimal(str(END.timestamp())), amount=999)]
    request = AsyncMock(side_effect=[SCOPE, dict(data=records, count=3)])
    with localcontext() as ctx:
        ctx.prec = 4
        report = asyncio.run(make(tmp_path, request).build_report(NOW))
    assert report.complete
    assert [(r.exchange, r.symbol, r.amount_usd) for r in report.rows] == [('QFEX', 'PANW', Decimal('1.22408043'))]
    assert request.await_count == 2
    discovery, ledger = request.call_args_list
    assert 'x-qfex-requested-account-id' not in discovery.kwargs['headers']
    assert ledger.kwargs['headers']['x-qfex-requested-account-id'] == ACCOUNT
    assert ledger.kwargs['params']['execution_type'] == 'funding'
    assert ledger.kwargs['params']['start_time'].endswith('Z')
    assert '/positions' not in ledger.args[1]


@pytest.mark.parametrize('change', [dict(amount=None), dict(currency='JPY'), dict(state='pending'),
                                    dict(execution_type='fee_rebate'), dict(symbol='PANW-JPY'),
                                    dict(created_at=True)])
def test_malformed_is_unknown_not_zero(tmp_path, change):
    request = AsyncMock(side_effect=[SCOPE, dict(data=[row(**change)], count=1)])
    report = asyncio.run(make(tmp_path, request).build_report(NOW))
    assert not report.complete and not report.rows
    assert 'QFEX' in report.warnings[0]


def test_complete_empty(tmp_path):
    request = AsyncMock(side_effect=[SCOPE, dict(data=None, count=0)])
    report = asyncio.run(make(tmp_path, request).build_report(NOW))
    assert report.complete and not report.rows


def test_permission_failure_is_redacted(tmp_path):
    request = AsyncMock(side_effect=[SCOPE, RuntimeError('secret history_account')])
    report = asyncio.run(make(tmp_path, request).build_report(NOW))
    assert not report.complete and not report.rows
    assert 'secret' not in str(report.warnings)


def test_split_saturated_pages_and_dedupe(tmp_path, monkeypatch):
    import src.services.qfex_funding as ledger
    monkeypatch.setattr(ledger, 'PAGE_LIMIT', 2)
    a, b = row(), row('two', created_at=Decimal(str(END.timestamp())), amount=999)
    request = AsyncMock(side_effect=[SCOPE, dict(data=[a, b], count=2),
                                    dict(data=[a], count=1), dict(data=[b], count=1)])
    report = asyncio.run(make(tmp_path, request).build_report(NOW))
    assert report.complete and report.total_usd == Decimal('-0.01048746')
    assert request.await_count == 4


def test_later_page_failure_discards_venue(tmp_path, monkeypatch):
    import src.services.qfex_funding as ledger
    monkeypatch.setattr(ledger, 'PAGE_LIMIT', 2)
    request = AsyncMock(side_effect=[SCOPE, dict(data=[row(), row('two')], count=2),
                                    dict(data=[row()], count=1), RuntimeError('private secret')])
    report = asyncio.run(make(tmp_path, request).build_report(NOW))
    assert not report.complete and not report.rows


def test_disabled_no_io(tmp_path):
    request = AsyncMock()
    service = make(tmp_path, request)
    service.credential_store.set_exchange_enabled('qfex:main', False)
    assert asyncio.run(service.build_report(NOW)).complete
    request.assert_not_awaited()
