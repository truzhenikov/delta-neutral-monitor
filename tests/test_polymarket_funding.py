"""Synthetic official-schema fixtures, never authenticated live evidence."""
import asyncio
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import pytest

BASE = 'https://api.perpetuals.polymarket.com'
START = datetime(2026, 10, 14, 11, 30, tzinfo=timezone(timedelta(hours=3)))
END = START + timedelta(days=1)
LOWER = 1791966600000
UPPER = LOWER + 86400000
PROXY = '0x' + 'a' * 40
OWNER = '0x' + '1' * 40
CREDS = {'proxy_address': PROXY, 'proxy_secret': 'synthetic-only-secret'}
SECOND = {'proxy_address': '0x' + 'b' * 40, 'proxy_secret': 'another-synthetic-secret'}


def row(**changes):
    result = dict(id=3055723280187747, instrument_id=1, size='10.00',
                  funding_rate='0.0001', funding_asset='pUSD', funding='1.00', timestamp=LOWER)
    result.update(changes)
    return result


def identity(owner=OWNER):
    return {'address': owner, 'keys': [
        {'proxy': PROXY, 'expiry': UPPER + 1000},
        {'proxy': SECOND['proxy_address'], 'expiry': UPPER + 1000}]}


def page(*items, more=False):
    return {'data': list(items), 'more': more}


def run(request, profiles=None, start=START, end=END, base=BASE):
    assert importlib.util.find_spec('src.services.polymarket_funding') is not None, 'ledger missing'
    from src.services.polymarket_funding import fetch_polymarket_funding
    profiles = {'polymarket:a': CREDS} if profiles is None else profiles
    store = SimpleNamespace(get_exchange_credentials=lambda ref: profiles[ref])
    return asyncio.run(fetch_polymarket_funding(store, list(profiles), start, end, request, base))


def test_raw_json_boundary_sign_large_ids_exact_under_small_context():
    ledger = json.loads(json.dumps(page(
        row(funding='123456789012345678901234567890.123456789', id=9007199254740993123456789),
        row(id=2, timestamp=LOWER - 1, funding='900'),
        row(id=3, timestamp=UPPER - 1, funding='-0.000000001'),
        row(id=4, timestamp=UPPER, funding='900'),
    )))
    request = AsyncMock(side_effect=[identity(), ledger, []])
    with localcontext() as ctx:
        ctx.prec = 3
        assert run(request) == {'1': Decimal('-123456789012345678901234567890.123456788')}
    private = request.call_args_list[:2]
    assert urlsplit(private[0].args[1]).path == '/v1/account/credentials'
    funding_call = private[1]
    query = parse_qs(urlsplit(funding_call.args[1]).query)
    assert query == {'start_timestamp': [str(LOWER)], 'end_timestamp': [str(UPPER)]}
    assert funding_call.kwargs['headers'] == {'polymarket-proxy': PROXY,
                                             'polymarket-secret': CREDS['proxy_secret']}
    for call in request.call_args_list:
        assert call.args[0] == 'GET'
        assert call.kwargs['follow_redirects'] is False
        assert 0 < call.kwargs['timeout'] <= 15
    assert request.call_args_list[-1].kwargs.get('headers', {}) == {}


def test_more_splits_inclusive_intervals_discards_capped_parent():
    midpoint = (LOWER + UPPER) // 2
    request = AsyncMock(side_effect=[identity(), page(row(timestamp=midpoint, funding='2'), more=True),
                                    page(row(timestamp=midpoint, funding='2')),
                                    page(row(id=2, timestamp=midpoint + 1, funding='-3')), []])
    # The capped parent repeats a real event; it must not also be added to leaves.
    assert run(request) == {'1': Decimal('1')}
    queries = [parse_qs(urlsplit(c.args[1]).query) for c in request.call_args_list[1:4]]
    assert queries == [
        {'start_timestamp': [str(LOWER)], 'end_timestamp': [str(UPPER)]},
        {'start_timestamp': [str(LOWER)], 'end_timestamp': [str(midpoint)]},
        {'start_timestamp': [str(midpoint + 1)], 'end_timestamp': [str(UPPER)]},
    ]


def test_closed_and_unknown_instruments_not_filtered_by_current_metadata():
    metadata = [{'instrument_id': 1, 'base_asset': 'SP500', 'display_symbol': 'USA500-USD',
                 'quote_asset': 'pUSD', 'instrument_type': 'perpetual'}]
    request = AsyncMock(side_effect=[identity(), page(row(), row(id=2, instrument_id=9007199254740993)),
                                    metadata])
    # uint32 instrument identifiers are required even though payment IDs have no 53-bit cap.
    with pytest.raises(ValueError):
        run(request)
    request = AsyncMock(side_effect=[identity(), page(row(), row(id=2, instrument_id=987)), metadata])
    assert run(request) == {'USA500': Decimal('-1'), '987': Decimal('-1')}
    assert request.call_args_list[-1].kwargs['headers'] == {}


@pytest.mark.parametrize('metadata', [RuntimeError('sensitive'), None, {},
    [{'instrument_id': 1, 'display_symbol': '<script>-USD'}],
    [{'instrument_id': True, 'display_symbol': 'BTC-USD'}]])
def test_optional_metadata_failure_or_unsafe_label_falls_back(metadata):
    request = AsyncMock(side_effect=[identity(), page(row()), metadata])
    assert run(request) == {'1': Decimal('-1')}


def test_metadata_only_once_across_profiles_and_never_private_headers():
    request = AsyncMock(side_effect=[identity(), page(row()), identity('0x' + '2' * 40),
                                    page(row()), []])
    assert run(request, {'polymarket:a': CREDS, 'polymarket:b': SECOND}) == {'1': Decimal('-2')}
    public = [c for c in request.call_args_list if '/info/' in c.args[1]]
    assert len(public) == 1
    assert public[0].kwargs['headers'] == {}


def test_multiple_keys_same_account_dedup_and_distinct_markets_same_id_survive():
    request = AsyncMock(side_effect=[identity(), page(row()), identity(),
                                    page(row(), row(instrument_id=2)), []])
    assert run(request, {'polymarket:a': CREDS, 'polymarket:b': SECOND}) == {
        '1': Decimal('-1'), '2': Decimal('-1')}


@pytest.mark.parametrize('change', [{'funding': '2'}, {'funding_rate': '0.2'}, {'size': '-3'},
                                   {'timestamp': LOWER + 1}])
def test_same_account_same_market_same_id_conflict_fails(change):
    request = AsyncMock(side_effect=[identity(), page(row()), identity(), page(row(**change))])
    with pytest.raises(ValueError, match='conflicting'):
        run(request, {'polymarket:a': CREDS, 'polymarket:b': SECOND})


def test_exact_proxy_aliases_fetched_once_case_insensitive_identity():
    request = AsyncMock(side_effect=[identity(), page(row()), []])
    alias = dict(CREDS, proxy_address='0x' + 'A' * 40)
    assert run(request, {'polymarket:a': CREDS, 'polymarket:alias': alias}) == {'1': Decimal('-1')}
    assert request.await_count == 3


@pytest.mark.parametrize('bad', [None, {}, {'proxy_address': PROXY},
    dict(CREDS, proxy_address=True), dict(CREDS, proxy_address='0x123'),
    dict(CREDS, proxy_address=PROXY + ' '), dict(CREDS, proxy_secret=''),
    dict(CREDS, proxy_secret='has\nnewline'), dict(CREDS, proxy_secret='has space')])
def test_all_refs_preflight_before_io(bad):
    request = AsyncMock()
    with pytest.raises(ValueError, match='credentials'):
        run(request, {'polymarket:a': CREDS, 'polymarket:bad': bad})
    request.assert_not_called()


def test_alias_conflicts_and_late_invalid_ref_are_all_preflighted():
    request = AsyncMock()
    for profiles in [
        {'polymarket:a': CREDS, 'polymarket:alias': dict(CREDS, proxy_secret='different')},
        {'polymarket:a': CREDS, 'polymarket:alias': dict(CREDS), 'polymarket:bad': {}},
    ]:
        with pytest.raises(ValueError):
            run(request, profiles)
    request.assert_not_called()


@pytest.mark.parametrize('bad', [
    {'id': True}, {'id': 1.0}, {'id': '3055723280187747'}, {'id': -1}, {'id': None},
    {'instrument_id': True}, {'instrument_id': 1.5}, {'instrument_id': '1'},
    {'instrument_id': -1}, {'instrument_id': 4294967296},
    {'timestamp': True}, {'timestamp': float(LOWER)}, {'timestamp': str(LOWER)},
    {'timestamp': -1}, {'timestamp': 253402300800000},
    {'funding': 'NaN'}, {'funding': 'Infinity'}, {'funding': 1.0}, {'funding': True},
    {'funding': '1e3'}, {'funding': ''}, {'funding_rate': 'NaN'}, {'size': 'Infinity'},
    {'funding_asset': 'USDC'}, {'funding_asset': 'USD'}, {'funding_asset': None},
])
def test_malformed_records_fail_closed_even_outside_window(bad):
    request = AsyncMock(side_effect=[identity(), page(row(timestamp=LOWER - 1, **bad))]) if 'timestamp' not in bad else AsyncMock(side_effect=[identity(), page(row(**bad))])
    with pytest.raises(ValueError):
        run(request)


@pytest.mark.parametrize('payload', [None, [], {}, {'data': []}, {'data': [], 'more': 0},
    {'data': [], 'more': 'false'}, {'data': {}, 'more': False}, page(more=True),
    {'data': [None], 'more': False}, page(*[row(id=i) for i in range(101)])])
def test_invalid_envelope_or_unknown_pagination_fails(payload):
    request = AsyncMock(side_effect=[identity(), payload])
    with pytest.raises(ValueError):
        run(request)


@pytest.mark.parametrize('error', [RuntimeError('secret transport detail'), {'code': 'failed'},
                                  page(row(funding_asset='BTC'))])
def test_failure_after_partial_leaf_never_returns_capped_or_partial_total(error):
    request = AsyncMock(side_effect=[identity(), page(row(), more=True), page(row()), error])
    with pytest.raises(ValueError) as exc:
        run(request)
    assert 'secret transport' not in str(exc.value)


def test_later_profile_failure_discards_prior_profile_result():
    request = AsyncMock(side_effect=[identity(), page(row()), identity(), RuntimeError('secret')])
    with pytest.raises(ValueError):
        run(request, {'polymarket:a': CREDS, 'polymarket:b': SECOND})


@pytest.mark.parametrize('bad', [None, {}, {'address': True}, {'address': '0x123'},
                                  {'address': OWNER + '\n'}])
def test_account_identity_required_to_scope_dedup(bad):
    request = AsyncMock(side_effect=[bad, page(row()), []])
    with pytest.raises(ValueError, match='identity'):
        run(request)
    assert request.await_count == 1


@pytest.mark.parametrize('keys', [None, [], {}, [{'proxy': '0x' + 'c' * 40}],
    [{'proxy': True}], [{'proxy': PROXY, 'expiry': True}]])
def test_identity_must_bind_the_authenticated_proxy_to_the_owner(keys):
    request = AsyncMock(side_effect=[dict(identity(), keys=keys), page(row()), []])
    with pytest.raises(ValueError, match='identity'):
        run(request)
    assert request.await_count == 1


def test_second_authenticated_proxy_missing_from_identity_fails_before_ledger():
    request = AsyncMock(side_effect=[identity(), page(row()),
        dict(identity('0x' + '2' * 40), keys=[{'proxy': PROXY, 'expiry': UPPER + 1000}])])
    with pytest.raises(ValueError, match='identity'):
        run(request, {'polymarket:a': CREDS, 'polymarket:b': SECOND})


def test_duplicate_terminal_records_fail_instead_of_inflating_money():
    with pytest.raises(ValueError, match='duplicate'):
        run(AsyncMock(side_effect=[identity(), page(row(), row())]))


def test_saturated_single_millisecond_fails_without_skipping_tied_records():
    tiny_end = START + timedelta(milliseconds=1)
    request = AsyncMock(side_effect=[identity(), page(row(), more=True), page(row(), more=True)])
    with pytest.raises(ValueError, match='saturat|pagination'):
        run(request, end=tiny_end)


def test_request_budget_includes_accounts_and_optional_metadata(monkeypatch):
    import src.services.polymarket_funding as funding
    monkeypatch.setattr(funding, 'MAX_REQUESTS', 3)
    request = AsyncMock(side_effect=[identity(), page(row(), more=True), page(row()), page(row(id=2))])
    with pytest.raises(ValueError, match='budget'):
        run(request)
    assert request.await_count == 3


def test_empty_is_complete_empty_without_public_metadata():
    request = AsyncMock(side_effect=[identity(), page()])
    assert run(request) == {}
    assert request.await_count == 2
    request = AsyncMock()
    assert run(request, {}) == {}
    request.assert_not_called()


@pytest.mark.parametrize('base', ['http://api.perpetuals.polymarket.com', BASE + '.evil',
    BASE + '/v1', BASE + '?q=1', 'https://secret@api.perpetuals.polymarket.com'])
def test_origin_rejected_before_io(base):
    request = AsyncMock()
    with pytest.raises(ValueError, match='origin'):
        run(request, base=base)
    request.assert_not_called()


@pytest.mark.parametrize('start,end', [(START.replace(tzinfo=None), END), (END, START),
    (START, START), (START + timedelta(microseconds=1), END),
    (START, END + timedelta(microseconds=1))])
def test_invalid_or_submillisecond_window_fails_before_io(start, end):
    request = AsyncMock()
    with pytest.raises(ValueError, match='window'):
        run(request, start=start, end=end)
    request.assert_not_called()


def test_parent_event_missing_from_complete_leaves_is_not_a_complete_result():
    request = AsyncMock(side_effect=[identity(), page(row(), more=True), page(), page(), []])
    with pytest.raises(ValueError, match='missing|pagination'):
        run(request)


def test_parent_event_changed_in_leaf_fails_closed():
    request = AsyncMock(side_effect=[identity(), page(row(), more=True),
                                    page(row(funding='2')), page(), []])
    with pytest.raises(ValueError, match='conflicting'):
        run(request)


def test_split_leaf_outside_its_query_fails_closed():
    request = AsyncMock(side_effect=[identity(), page(row(), more=True),
                                    page(row()), page(row(id=2)), []])
    with pytest.raises(ValueError, match='range|pagination'):
        run(request)


@pytest.mark.parametrize('payload', [dict(page(), cursor='unproven'),
                                    dict(page(), status='error'), dict(page(), has_more=True)])
def test_undocumented_envelope_pagination_fields_fail_closed(payload):
    with pytest.raises(ValueError):
        run(AsyncMock(side_effect=[identity(), payload]))


def test_metadata_budget_exhaustion_is_optional_and_exact_amount_survives(monkeypatch):
    import src.services.polymarket_funding as funding
    monkeypatch.setattr(funding, 'MAX_REQUESTS', 2)
    request = AsyncMock(side_effect=[identity(), page(row())])
    assert run(request) == {'1': Decimal('-1')}
    assert request.await_count == 2


def test_per_request_timeout_and_global_deadline(monkeypatch):
    import src.services.polymarket_funding as funding

    async def hang(*args, **kwargs):
        await asyncio.sleep(1)

    monkeypatch.setattr(funding, 'REQUEST_TIMEOUT', 0.001)
    with pytest.raises(ValueError, match='request|deadline'):
        run(hang)
    monkeypatch.setattr(funding, 'REQUEST_TIMEOUT', 1)
    monkeypatch.setattr(funding, 'TOTAL_TIMEOUT', 0.001)
    with pytest.raises(ValueError, match='deadline'):
        run(hang)
