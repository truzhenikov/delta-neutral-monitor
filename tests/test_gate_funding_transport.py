import asyncio
from datetime import datetime
from decimal import Decimal

import httpx

from src.services.credential_store import CredentialStore
from src.services.funding_report_service import MSK, FundingReportService


def test_real_json_transport_preserves_fractional_gate_timestamp(tmp_path, monkeypatch):
    # JSON numbers near a seconds boundary must not first become binary floats.
    store = CredentialStore(tmp_path / 'credentials.json')
    store.set_exchange_credentials('gate', {'api_key': 'synthetic', 'api_secret': 'secret'})
    original_client = httpx.AsyncClient
    def respond(request):
        return httpx.Response(200, content=b'[{"id":"one","contract":"BTC_USDT",'
            b'"type":"fund","time":1792052999.999999999,"change":"1.25"}]')
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original_client(
        **kw, transport=httpx.MockTransport(respond)))
    service = FundingReportService(store, tmp_path / 'funding.json')
    report = asyncio.run(service.build_report(datetime(2026, 10, 15, 12, tzinfo=MSK)))
    assert report.complete
    assert [(r.exchange, r.symbol, r.amount_usd) for r in report.rows] == [('Gate', 'BTC', Decimal('1.25'))]
