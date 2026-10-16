import asyncio
import importlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services.credential_store import CredentialStore
from src.services.funding_report_service import MSK, FundingReportService, render_funding_report

BASE = 'https://mainnet.zklighter.elliot.ai'
RH = 'https://api.rh.lighter.xyz'
START = datetime(2026, 10, 14, 11, 30, tzinfo=MSK)
END = START + timedelta(days=1)
NOW = datetime(2026, 10, 16, 10, tzinfo=MSK)
LOW = 1791966600
TOKEN = 'ro:123:single:2100000000:abcdef0123456789'


def row(**changes):
    return dict(dict(timestamp=LOW, market_id=147, funding_id=1709243, change='1.623401',
                     discount='0', rate='0.000071', position_size='14', position_side='short'), **changes)


def page(rows, **extra):
    return dict(code=200, position_fundings=rows, **extra)


def identity(index='123', token=TOKEN):
    return dict(account_index=index, l1_address='0x'+'ab'*20, read_only_token=token)


def run(request, identities=None, base=BASE, start=START, end=END):
    module = importlib.import_module('src.services.lighter_funding')
    identities = identities or [identity()]
    refs = [f'{"lighter-rh" if base == RH else "lighter"}:p{i}' for i in range(len(identities))]
    store = SimpleNamespace(get_exchange_credentials=lambda ref: identities[refs.index(ref)])
    return asyncio.run(module.fetch_lighter_funding(store, refs, start, end, request, base, now=NOW))


def wire(pages, metadata=None):
    history = iter(pages)

    async def request(method, url, **kwargs):
        if url.endswith('/orderBookDetails'):
            return metadata if metadata is not None else dict(code=200, order_book_details=[dict(market_id=147, symbol='XPT')])
        return next(history)

    return AsyncMock(side_effect=request)


@pytest.fixture(autouse=True)
def no_pacing(monkeypatch):
    try:
        module = importlib.import_module('src.services.lighter_funding')
    except ModuleNotFoundError:
        return
    monkeypatch.setattr(module, 'sleep', AsyncMock())


def test_store_optional_history_token_survives_reload_masking_and_secure_write(tmp_path):
    store = CredentialStore(tmp_path/'credentials.json')
    for venue in ('lighter', 'lighter-rh'):
        store.set_exchange_credentials(venue+':main', identity())
    reloaded = CredentialStore(store.storage_path)
    for venue in ('lighter', 'lighter-rh'):
        assert reloaded.get_exchange_credentials(venue+':main').get('read_only_token') == TOKEN
        assert TOKEN not in str(reloaded.list_exchange_statuses())
        assert reloaded.get_exchange_credentials_masked(venue+':main')['read_only_token'] != TOKEN
    store.set_exchange_enabled('lighter-rh:main', False)
    assert reloaded.get_exchange_credentials('lighter:main')['read_only_token'] == TOKEN
    assert store.storage_path.stat().st_mode & 0o777 == 0o600


def test_authenticated_history_signed_change_not_rate_or_discount_closed_market(tmp_path):
    request = wire([page([row(discount='100'), row(market_id=146, change='0.214440', rate='0.000027')])])
    assert run(request) == {'XPT': Decimal('1.623401'), 'market#146': Decimal('0.214440')}
    history = next(c for c in request.call_args_list if c.args[1].endswith('/positionFunding'))
    assert history.args == ('GET', BASE+'/api/v1/positionFunding')
    assert history.kwargs == dict(params=dict(account_index=123, limit=100, side='all'),
                                  headers={'Authorization': TOKEN}, follow_redirects=False)
    metadata = next(c for c in request.call_args_list if c.args[1].endswith('/orderBookDetails'))
    assert 'headers' not in metadata.kwargs and metadata.kwargs['follow_redirects'] is False


def test_cursor_walk_ignores_short_empty_out_of_window_pages_and_filters_half_open():
    request = wire([page([row(timestamp=LOW-1, funding_id=1)], next_cursor='a'), page([], next_cursor='b'),
                    page([row(), row(timestamp=LOW+86400, funding_id=2)], next_cursor='')])
    assert run(request) == {'XPT': Decimal('1.623401')}
    calls = [c for c in request.call_args_list if c.args[1].endswith('/positionFunding')]
    assert [c.kwargs['params'].get('cursor') for c in calls] == [None, 'a', 'b']
    assert all('start_timestamp' not in c.kwargs['params'] for c in calls)


def test_exact_addition_and_market_scoped_ids_deduplication():
    request = wire([page([row(change='123456789012345678901234567890.00000001'),
                         row(funding_id=2, change='0.00000002'), row(market_id=146, change='-2'), row(market_id=146, change='-2')])])
    with localcontext() as ctx:
        ctx.prec = 4
        assert run(request) == {'XPT': Decimal('123456789012345678901234567890.00000003'), 'market#146': Decimal('-2')}


def test_aliases_deduplicated_accounts_separate_and_global_pacing():
    request = wire([page([row()]), page([row()])])
    identities = [identity(), identity(index='00123'), identity('124', TOKEN.replace(':123:', ':124:'))]
    assert run(request, identities)['XPT'] == Decimal('3.246802')
    history = [c for c in request.call_args_list if c.args[1].endswith('/positionFunding')]
    assert len(history) == 2
    module = importlib.import_module('src.services.lighter_funding')
    assert module.sleep.await_count == request.await_count-1
    assert all(c.args == (0.51,) for c in module.sleep.call_args_list)


@pytest.mark.parametrize('bad', [identity(token=''), identity(index='-1'), identity(index=True),
    identity(token='123:1:1:abcdef'), identity(token=TOKEN+'\n'), identity(token=TOKEN.replace(':123:', ':124:')),
    identity(token=TOKEN.replace(':single:', ':bogus:')), identity(token=TOKEN.replace('2100000000', '1700000000')),
    identity(token=TOKEN.replace('abcdef0123456789', 'nothex')), identity(token=TOKEN.replace(':123:single', ':124:all'))])
def test_all_profile_tokens_and_identity_validated_before_any_get(bad):
    request = AsyncMock()
    with pytest.raises(ValueError):
        run(request, [identity(), bad])
    request.assert_not_called()


def test_all_scope_only_accepted_for_explicit_matching_identity():
    assert run(wire([page([])]), [identity(token=TOKEN.replace(':single:', ':all:'))]) == {}


@pytest.mark.parametrize('base', ['http://mainnet.zklighter.elliot.ai', BASE+'/evil', BASE+'@evil.test', RH+'/api', 'https://evil.test'])
def test_untrusted_origin_rejected_before_auth(base):
    request = AsyncMock()
    with pytest.raises(ValueError):
        run(request, base=base)
    request.assert_not_called()


@pytest.mark.parametrize('changes', [dict(timestamp=LOW*1000), dict(timestamp=LOW*1000000), dict(timestamp=True),
    dict(timestamp=str(LOW)), dict(timestamp=0), dict(market_id=True), dict(market_id=-1), dict(funding_id=None),
    dict(funding_id=-1), dict(change='NaN'), dict(change=1.2), dict(discount='bad'), dict(rate='Infinity'),
    dict(position_size=''), dict(position_side='all'), dict(currency='EUR')])
def test_malformed_rows_even_outside_window_fail_closed(changes):
    with pytest.raises(ValueError):
        run(wire([page([row(**changes)])]))


@pytest.mark.parametrize('payload', [{}, [], dict(code=20001, position_fundings=[]), dict(code='200', position_fundings=[]),
    dict(code=200, position_fundings=None), page([], next_cursor=123), page([], next_cursor=None),
    page([], error='denied'), page([], currency='EUR'), page([row()]*101)])
def test_bad_page_fails_closed(payload):
    with pytest.raises(ValueError):
        run(wire([payload]))


def test_conflicting_event_across_pages_rejected():
    with pytest.raises(ValueError, match='conflict'):
        run(wire([page([row()], next_cursor='x'), page([row(change='99')])]))


def test_repeated_cursor_rejected():
    with pytest.raises(ValueError, match='cursor'):
        run(wire([page([], next_cursor='x'), page([], next_cursor='x')]))


def test_request_budget_global_across_profiles(monkeypatch):
    module = importlib.import_module('src.services.lighter_funding')
    assert module.MAX_REQUESTS == 256
    monkeypatch.setattr(module, 'MAX_REQUESTS', 2)
    request = wire([page([], next_cursor='a'), page([])])
    with pytest.raises(ValueError, match='budget'):
        run(request, [identity(), identity('124', TOKEN.replace(':123:', ':124:'))])
    assert request.await_count == 2


@pytest.mark.parametrize('metadata', [{}, dict(code=200, order_book_details=[]),
    dict(code=200, order_book_details=[dict(market_id=147, symbol='WRONG', quote_currency='EUR')]),
    dict(code=200, order_book_details=[dict(market_id=147, symbol='A'), dict(market_id=147, symbol='B')])])
def test_unavailable_untrusted_or_conflicting_metadata_uses_identity(metadata):
    assert run(wire([page([row()])], metadata)) == {'market#147': Decimal('1.623401')}


def test_partner_origin_and_quote_metadata_are_isolated():
    request = wire([page([row()])], dict(code=200, order_book_details=[dict(market_id=147, symbol='RH', quote_currency='USDG')]))
    assert run(request, base=RH) == {'RH': Decimal('1.623401')}
    assert all(c.args[1].startswith(RH+'/') for c in request.call_args_list)


@pytest.mark.parametrize('start,end', [(START.replace(tzinfo=None), END), (END, START),
    (datetime(1970, 1, 1, tzinfo=timezone.utc), END)])
def test_bad_window_no_network(start, end):
    request = AsyncMock()
    with pytest.raises(ValueError):
        run(request, start=start, end=end)
    request.assert_not_called()


def test_report_uses_history_not_sampler_distinct_deployments_and_disabled_profiles(tmp_path):
    store = CredentialStore(tmp_path/'credentials.json')
    store.set_exchange_credentials('lighter:main', identity())
    store.set_exchange_credentials('lighter-rh:main', identity())
    request = wire([page([row()]), page([row(change='-2')])])
    service = FundingReportService(store, tmp_path/'state.json', request_json=request)
    service.lighter_rows_for_window = lambda *_: pytest.fail('sampler fallback used')
    report = asyncio.run(service.build_report(NOW))
    assert report.complete
    assert {(r.exchange, r.symbol, r.amount_usd) for r in report.rows} == {
        ('Lighter', 'XPT', Decimal('1.623401')), ('Lighterhood', 'XPT', Decimal('-2'))}
    assert any(c.args[1].startswith(RH+'/') for c in request.call_args_list)
    store.set_exchange_enabled('lighter-rh:main', False)
    request = wire([page([])])
    service._request_json_override = request
    assert asyncio.run(service.build_report(NOW)).complete
    assert all(not c.args[1].startswith(RH+'/') for c in request.call_args_list)


@pytest.mark.parametrize('failure', ['page', 'profile', 'missing_token'])
def test_report_withholds_entire_failed_deployment_preserves_other_venue_and_no_secret(tmp_path, failure):
    store = CredentialStore(tmp_path/'credentials.json')
    store.set_exchange_credentials('lighter:main', identity())
    if failure in ('profile', 'missing_token'):
        store.set_exchange_credentials('lighter:second', identity('124', '' if failure == 'missing_token' else TOKEN.replace(':123:', ':124:')))
    store.set_exchange_credentials('lighter-rh:main', identity())
    calls = []

    async def request(method, url, **kwargs):
        if url.startswith(RH):
            return dict(code=200, order_book_details=[]) if url.endswith('/orderBookDetails') else page([row(change='2')])
        if url.endswith('/orderBookDetails'):
            return dict(code=200, order_book_details=[])
        calls.append(kwargs['params'])
        if len(calls) == 1:
            return page([row()], **({'next_cursor': 'x'} if failure == 'page' else {}))
        raise RuntimeError(TOKEN)

    service = FundingReportService(store, tmp_path/'state.json', request_json=request)
    service.record_lighter_sample({'lighter:main|XPT': Decimal('100')}, now=START)
    service.record_lighter_sample({'lighter:main|XPT': Decimal('900')}, now=END)
    report = asyncio.run(service.build_report(NOW))
    assert not report.complete
    assert [(r.exchange, r.amount_usd) for r in report.rows] == [('Lighterhood', Decimal('2'))]
    assert len(report.warnings) == 1 and report.warnings[0].startswith('Lighter —')
    assert TOKEN not in render_funding_report(report)
    if failure == 'missing_token':
        assert 'read_only_token' in report.warnings[0]
        assert not calls


def test_real_http_transport_sends_raw_auth_no_redirect_and_seconds(tmp_path, monkeypatch):
    import httpx
    import src.services.funding_report_service as reports
    seen = []

    def handle(request):
        seen.append(request)
        if request.url.path.endswith('/orderBookDetails'):
            assert 'authorization' not in request.headers
            return httpx.Response(200, json=dict(code=200, order_book_details=[]))
        assert request.headers['authorization'] == TOKEN
        assert request.url.params['account_index'] == '123'
        return httpx.Response(200, json=page([row()]))

    real_client = httpx.AsyncClient
    monkeypatch.setattr(reports.httpx, 'AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw))
    store = CredentialStore(tmp_path/'credentials.json')
    store.set_exchange_credentials('lighter', identity())
    report = asyncio.run(FundingReportService(store, tmp_path/'state.json').build_report(NOW))
    assert report.complete and report.rows[0].amount_usd == Decimal('1.623401')
    assert len(seen) == 2


@pytest.mark.parametrize('venue', ['lighter', 'lighter-rh'])
@pytest.mark.parametrize('skip', [False, True])
def test_setup_optional_history_token_and_skip_preserves_existing(tmp_path, venue, skip):
    from src.bot.setup_flow import TelegramSetupFlow
    from src.services.telegram_preferences import TelegramPreferencesService
    store = CredentialStore(tmp_path/'credentials.json')
    if skip:
        store.set_exchange_credentials(venue+':main', identity())
    flow = TelegramSetupFlow(
        TelegramPreferencesService(state_path=tmp_path/'preferences.json', admin_chat_ids=['1']), store,
    )
    flow.start_setup('1', f'/setup {venue} main')
    flow.handle_message('1', '123')
    prompt = flow.handle_message('1', identity()['l1_address'])
    assert 'read_only_token' in prompt and '-' in prompt
    assert flow.has_active_session('1')
    reply = flow.handle_message('1', '-' if skip else TOKEN)
    assert 'сохранены' in reply.lower()
    assert store.get_exchange_credentials(venue+':main')['read_only_token'] == TOKEN
    assert TOKEN not in flow.list_exchanges('1')


def test_setup_invalid_token_not_saved_and_can_retry_or_skip(tmp_path):
    from src.bot.setup_flow import TelegramSetupFlow
    from src.services.telegram_preferences import TelegramPreferencesService
    store = CredentialStore(tmp_path/'credentials.json')
    store.set_exchange_credentials('lighter:main', identity())
    flow = TelegramSetupFlow(
        TelegramPreferencesService(state_path=tmp_path/'preferences.json', admin_chat_ids=['1']), store,
    )
    flow.start_setup('1', '/setup lighter main')
    flow.handle_message('1', '123')
    flow.handle_message('1', identity()['l1_address'])
    bad = TOKEN.replace(':123:', ':124:')
    reply = flow.handle_message('1', bad)
    assert 'read_only_token' in reply and bad not in reply
    assert flow.has_active_session('1')
    assert store.get_exchange_credentials('lighter:main')['read_only_token'] == TOKEN
    flow.handle_message('1', '-')
    assert not flow.has_active_session('1')


def test_setup_without_history_token_keeps_public_credentials_only(tmp_path):
    from src.bot.setup_flow import TelegramSetupFlow
    from src.services.telegram_preferences import TelegramPreferencesService
    store = CredentialStore(tmp_path/'credentials.json')
    flow = TelegramSetupFlow(
        TelegramPreferencesService(state_path=tmp_path/'preferences.json', admin_chat_ids=['1']), store,
    )
    flow.start_setup('1', '/setup lighter main')
    flow.handle_message('1', '123')
    flow.handle_message('1', identity()['l1_address'])
    flow.handle_message('1', '-')
    assert store.get_exchange_credentials('lighter:main') == {
        'account_index': '123', 'l1_address': identity()['l1_address'],
    }


def test_dependencies_explicitly_wire_partner_origin(monkeypatch):
    import src.deps as deps
    fields = ['hyperliquid', 'lighter', 'lighter_rh', 'variational', 'bingx', 'okx', 'ondo', 'aster', 'gate', 'woofi', 'arcus']
    settings = SimpleNamespace(**{name+'_api_base': 'https://example.invalid' for name in fields},
                               funding_report_state_path='unused.json', gate_settle_currency='usdt',
                               mexc_funding_api_base='mexc', request_timeout_sec=3)
    monkeypatch.setattr(deps, 'get_settings', lambda: settings)
    monkeypatch.setattr(deps, 'get_credential_store', lambda: None)
    deps.get_funding_report_service.cache_clear()
    try:
        assert deps.get_funding_report_service().lighter_rh_api_base == 'https://example.invalid'
    finally:
        deps.get_funding_report_service.cache_clear()


def test_report_total_remains_exact_under_reduced_precision(tmp_path):
    store = CredentialStore(tmp_path/'credentials.json')
    store.set_exchange_credentials('lighter', identity())
    request = wire([page([row(change='123456789012345678901234567890.00000001'),
                         row(market_id=146, change='0.00000002')])])
    with localcontext() as ctx:
        ctx.prec = 4
        report = asyncio.run(FundingReportService(store, tmp_path/'state.json', request_json=request).build_report(NOW))
        assert report.complete
        assert report.total_usd == Decimal('123456789012345678901234567890.00000003')


@pytest.mark.parametrize('venue,base', [('lighter', RH), ('lighter-rh', BASE)])
def test_misconfigured_deployment_never_receives_other_deployment_token(tmp_path, venue, base):
    store = CredentialStore(tmp_path/'credentials.json')
    store.set_exchange_credentials(venue, identity())
    request = AsyncMock(return_value=page([]))
    options = {'lighter_api_base' if venue == 'lighter' else 'lighter_rh_api_base': base}
    report = asyncio.run(FundingReportService(store, tmp_path/'state.json', request_json=request, **options).build_report(NOW))
    assert not report.complete
    request.assert_not_called()


def test_store_secret_file_is_private_during_serialization_not_only_after(tmp_path, monkeypatch):
    import json
    store = CredentialStore(tmp_path/'credentials.json')
    dumps = json.dumps

    def checked_dumps(*args, **kwargs):
        pending = [path for path in tmp_path.iterdir() if path.name != 'credentials.json']
        assert pending and all(path.stat().st_mode & 0o777 == 0o600 for path in pending)
        return dumps(*args, **kwargs)

    monkeypatch.setattr(json, 'dumps', checked_dumps)
    store.set_exchange_credentials('lighter', identity())
    assert store.storage_path.stat().st_mode & 0o777 == 0o600


def test_real_http_redirect_is_not_followed_and_withholds_venue(tmp_path, monkeypatch):
    import httpx
    import src.services.funding_report_service as reports
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(302, headers={'Location': 'https://evil.invalid/stolen'})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(reports.httpx, 'AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw))
    store = CredentialStore(tmp_path/'credentials.json')
    store.set_exchange_credentials('lighter', identity())
    report = asyncio.run(FundingReportService(store, tmp_path/'state.json').build_report(NOW))
    assert not report.complete and not report.rows
    assert len(seen) == 1 and seen[0].url.host == 'mainnet.zklighter.elliot.ai'
    assert TOKEN not in render_funding_report(report)
