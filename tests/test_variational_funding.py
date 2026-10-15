from __future__ import annotations

import asyncio
import base64
import json
from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from src.services.credential_store import CredentialStore
from src.services.funding_report_service import FundingReportService, MSK

NOW = datetime(2026, 10, 15, 12, tzinfo=MSK)


def token(address='0xabc'):
    body = base64.urlsafe_b64encode(json.dumps({'address': address}).encode()).decode().rstrip('=')
    return f'header.{body}.signature'


def row(record_id='one', **changes):
    result = dict(id=record_id, asset='USDC', qty='1.25', transfer_type='funding', funding_type=None,
                  status='confirmed', created_at='2026-10-14T08:30:00Z',
                  reference_instrument={'underlying': 'NBIS', 'instrument_type': 'perpetual_rwa_future'})
    result.update(changes)
    return result


def page(rows, count=None, next_page=None):
    return {'result': rows, 'pagination': {'object_count': len(rows) if count is None else count,
                                          'next_page': next_page}}


def service(tmp_path, request, profiles=1):
    store = CredentialStore(tmp_path / 'credentials.json')
    for i in range(profiles):
        store.set_exchange_credentials(f'variational:p{i}', {'vr_token': token(f'0xabc{i}')})
    return FundingReportService(store, tmp_path / 'funding.json', request_json=request)


def test_signed_ledger_aggregation_and_half_open_boundaries(tmp_path):
    request = AsyncMock(return_value=page([
        row(), row('two', qty='-0.50'),
        row('before', qty='99', created_at='2026-10-14T08:29:59Z'),
        row('end', qty='99', created_at='2026-10-15T08:30:00Z'),
        row('failed', status='failed'), row('dividend', funding_type='dividend'),
    ]))
    report = asyncio.run(service(tmp_path, request).build_report(NOW))
    assert report.complete
    assert [(r.exchange, r.symbol, r.amount_usd) for r in report.rows] == [('Variational', 'NBIS', Decimal('0.75'))]
    method, url = request.call_args.args
    assert method == 'GET' and url == 'https://omni.variational.io/api/transfers'
    params = request.call_args.kwargs['params']
    assert params['type'] == 'funding'
    assert params['created_at_gte'] == '2026-10-14T08:30:00Z'
    assert params['created_at_lte'] == '2026-10-15T08:30:00Z'
    assert 'vr-connected-address' in request.call_args.kwargs['headers']


def test_pagination_and_duplicate_profiles_do_not_double_count(tmp_path):
    responses = [page([row()], 2, {'limit': '20', 'offset': '1'}),
                 page([row('two', qty='-0.25')], 2),
                 page([row(), row('two', qty='-0.25')], 2)]
    request = AsyncMock(side_effect=responses)
    report = asyncio.run(service(tmp_path, request, profiles=2).build_report(NOW))
    assert report.complete
    assert report.total_usd == Decimal('1.00')
    assert request.call_args_list[1].kwargs['params']['offset'] == 1


@pytest.mark.parametrize('bad', [
    {'qty': 'NaN'}, {'qty': 'Infinity'}, {'qty': None}, {'asset': 'BTC'},
    {'created_at': 'broken'}, {'created_at': '2026-10-14T09:00:00'},
    {'reference_instrument': {}}, {'id': ''}, {'status': 'pending'},
])
def test_bad_ledger_fails_venue_closed(tmp_path, bad):
    request = AsyncMock(return_value=page([row(), row('bad', **bad)]))
    report = asyncio.run(service(tmp_path, request).build_report(NOW))
    assert not report.complete
    assert report.rows == []
    assert any('Variational' in warning for warning in report.warnings)


@pytest.mark.parametrize('payload', [[], {}, page([], 1), page([row()], 2),
                                   page([row()], 1, {'offset': '0', 'limit': '20'})])
def test_incomplete_or_unexpected_pagination_fails_closed(tmp_path, payload):
    request = AsyncMock(return_value=payload)
    report = asyncio.run(service(tmp_path, request).build_report(NOW))
    assert not report.complete and not report.rows


def test_one_profile_failure_discards_all_variational_rows(tmp_path):
    request = AsyncMock(side_effect=[page([row()]), RuntimeError('private details')])
    report = asyncio.run(service(tmp_path, request, profiles=2).build_report(NOW))
    assert not report.complete and not report.rows
    assert 'private details' not in '\n'.join(report.warnings)


def test_missing_token_fails_closed_without_request(tmp_path):
    request = AsyncMock()
    s = service(tmp_path, request)
    s.credential_store.get_exchange_credentials = lambda ref: {}
    report = asyncio.run(s.build_report(NOW))
    assert not report.complete and not report.rows
    request.assert_not_called()


def test_empty_confirmed_history_is_complete(tmp_path):
    request = AsyncMock(return_value=page([]))
    report = asyncio.run(service(tmp_path, request).build_report(NOW))
    assert report.complete and report.rows == []
