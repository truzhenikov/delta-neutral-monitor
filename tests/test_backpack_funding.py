import asyncio
import base64
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

BASE = 'https://api.backpack.exchange'
START = datetime(2026, 10, 14, 8, 30, tzinfo=timezone.utc)
END = START + timedelta(days=1)


def credentials(seed=b'a' * 32):
    key = Ed25519PrivateKey.from_private_bytes(seed)
    return {'api_key': base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode(),
            'api_secret': base64.b64encode(seed).decode()}


CREDS = credentials()


def row(**changes):
    result = dict(userId=123, subaccountId=0, symbol='BTC_USDC_PERP', quantity='1.25',
                  intervalEndTimestamp='2026-10-14T08:30:00', fundingRate='0.0001')
    result.update(changes)
    return result


def run(request, profiles=None, start=START, end=END, base=BASE):
    assert importlib.util.find_spec('src.services.backpack_funding') is not None, 'ledger missing'
    from src.services.backpack_funding import fetch_backpack_funding
    profiles = {'backpack:a': CREDS} if profiles is None else profiles
    store = SimpleNamespace(get_exchange_credentials=lambda ref: profiles[ref])
    return asyncio.run(fetch_backpack_funding(store, list(profiles), start, end, request, base))


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    if importlib.util.find_spec('src.services.backpack_funding') is not None:
        import src.services.backpack_funding as funding
        monkeypatch.setattr(funding, 'sleep', AsyncMock())


def test_raw_json_boundaries_exact_sign_and_wire_signature():
    payload = json.loads(json.dumps([
        row(quantity='123456789012345678901234567890.123456789'),
        row(intervalEndTimestamp='2026-10-14T08:29:59.999999999', quantity='999'),
        row(intervalEndTimestamp='2026-10-15T08:29:59.999999999', quantity='-0.000000001'),
        row(intervalEndTimestamp='2026-10-15T08:30:00', quantity='999'),
    ]))
    request = AsyncMock(side_effect=[payload, []])
    with localcontext() as ctx:
        ctx.prec = 3
        assert run(request) == {'BTC': Decimal('123456789012345678901234567890.123456788')}
    args, kwargs = request.call_args_list[0]
    assert args[0] == 'GET'
    wire = httpx.Request(*args).url.query.decode()
    assert wire == 'limit=1000&offset=0&sortDirection=Asc'
    assert urlsplit(args[1]).path == '/wapi/v1/history/funding'
    assert kwargs['follow_redirects'] is False
    headers = kwargs['headers']
    message = ('instruction=fundingHistoryQueryAll&' + wire + '&timestamp=' + headers['X-Timestamp']
               + '&window=' + headers['X-Window'])
    Ed25519PrivateKey.from_private_bytes(b'a' * 32).public_key().verify(
        base64.b64decode(headers['X-Signature']), message.encode())


def page():
    return [row(intervalEndTimestamp=(START + timedelta(seconds=i)).replace(tzinfo=None).isoformat())
            for i in range(1000)]


def test_full_page_then_terminal_and_all_profiles_aliases():
    request = AsyncMock(side_effect=[page(), [], [row(userId=124, quantity='-2')], []])
    profiles = {'backpack:a': CREDS, 'backpack:alias': dict(CREDS), 'backpack:b': credentials(b'b' * 32)}
    assert run(request, profiles) == {'BTC': Decimal('1248')}
    assert [parse_qs(urlsplit(c.args[1]).query)['offset'] for c in request.call_args_list] == [['0'], ['1000'], ['0'], ['1']]


def test_short_server_capped_pages_continue_to_empty():
    request = AsyncMock(side_effect=[[row()], [row(intervalEndTimestamp='2026-10-14T09:00:00')], []])
    assert run(request) == {'BTC': Decimal('2.50')}
    assert [parse_qs(urlsplit(c.args[1]).query)['offset'] for c in request.call_args_list] == [['0'], ['1'], ['2']]


def test_repeat_offset_fails():
    with pytest.raises(ValueError, match='overlap|progress'):
        run(AsyncMock(return_value=[row()]))


def test_error_after_partial_is_not_a_result():
    with pytest.raises(ValueError):
        run(AsyncMock(side_effect=[[row()], {'code': 'ERROR', 'message': 'private'}]))
    with pytest.raises(ValueError, match='request'):
        run(AsyncMock(side_effect=[[row()], RuntimeError('secret transport material')]))


def test_budget(monkeypatch):
    import src.services.backpack_funding as funding
    monkeypatch.setattr(funding, 'MAX_REQUESTS', 1)
    request = AsyncMock(return_value=[row()])
    with pytest.raises(ValueError, match='budget'):
        run(request)
    assert request.await_count == 1


@pytest.mark.parametrize('bad', [
    {'userId': True}, {'userId': '123'}, {'userId': -1}, {'userId': 9007199254740993},
    {'subaccountId': -1}, {'subaccountId': 65536}, {'subaccountId': 1.0},
    {'symbol': 'BTC_USDT_PERP'}, {'symbol': 'BTC_USDC'}, {'symbol': '_USDC_PERP'},
    {'symbol': 'btc_USDC_PERP'}, {'asset': 'BTC'}, {'currency': 'USDT'},
    {'quantity': 'NaN'}, {'quantity': 'Infinity'}, {'quantity': 1.25}, {'quantity': ''},
    {'fundingRate': 'NaN'}, {'intervalEndTimestamp': 1791966600000},
    {'intervalEndTimestamp': '2026-10-14T08:30:00.1234567890'},
    {'intervalEndTimestamp': 'bad'}, {'id': 1.5}, {'id': True},
])
def test_bad_records_fail_closed(bad):
    with pytest.raises(ValueError):
        run(AsyncMock(side_effect=[[row(**bad)], []]))


@pytest.mark.parametrize('bad', [None, {}, {'api_key': 'x', 'api_secret': 'y'},
                                dict(CREDS, api_secret=credentials(b'b' * 32)['api_secret'])])
def test_all_profiles_preflight_before_io(bad):
    request = AsyncMock(return_value=[])
    with pytest.raises(ValueError):
        run(request, {'backpack:a': CREDS, 'backpack:bad': bad})
    request.assert_not_called()


@pytest.mark.parametrize('change', [{'api_secret': 'bad'}, {'api_passphrase': 'conflict'}])
def test_alias_secret_conflict_preflight(change):
    request = AsyncMock(return_value=[])
    with pytest.raises(ValueError):
        run(request, {'backpack:a': CREDS, 'backpack:alias': dict(CREDS, **change)})
    request.assert_not_called()


def test_opaque_optional_id_preserves_large_integer_exactly():
    huge = 9007199254740993123456789
    request = AsyncMock(side_effect=[json.loads(json.dumps([row(id=huge)])), []])
    assert run(request) == {'BTC': Decimal('1.25')}


@pytest.mark.parametrize('base', ['http://api.backpack.exchange', BASE + '.evil', BASE + '/path', BASE + '?q=1'])
def test_origin_rejected_before_io(base):
    request = AsyncMock()
    with pytest.raises(ValueError):
        run(request, base=base)
    request.assert_not_called()


def test_empty_is_empty_not_synthetic_zero():
    assert run(AsyncMock(return_value=[])) == {}


def test_profile_error_after_valid_profile_discards_result():
    request = AsyncMock(side_effect=[[row()], [], {'code': 'error'}])
    with pytest.raises(ValueError):
        run(request, {'backpack:a': CREDS, 'backpack:b': credentials(b'b' * 32)})


def test_duplicate_same_interval_fails_not_double_counted():
    with pytest.raises(ValueError):
        run(AsyncMock(return_value=[row(), row(quantity='2')]))


def test_different_keys_same_underlying_account_deduplicate_globally():
    request = AsyncMock(side_effect=[[row()], [], [row()], []])
    profiles = {'backpack:a': CREDS, 'backpack:alias': credentials(b'b' * 32)}
    assert run(request, profiles) == {'BTC': Decimal('1.25')}
    assert request.await_count == 4


@pytest.mark.parametrize('changes', [{'quantity': '2'}, {'fundingRate': '0.0002'}])
def test_different_keys_conflicting_same_account_event_fail_closed(changes):
    request = AsyncMock(side_effect=[[row()], [], [row(**changes)], []])
    profiles = {'backpack:a': CREDS, 'backpack:alias': credentials(b'b' * 32)}
    with pytest.raises(ValueError, match='conflicting'):
        run(request, profiles)


def test_different_users_or_subaccounts_are_not_globally_collapsed():
    request = AsyncMock(side_effect=[[row()], [], [row(userId=124), row(subaccountId=1)], []])
    profiles = {'backpack:a': CREDS, 'backpack:b': credentials(b'b' * 32)}
    assert run(request, profiles) == {'BTC': Decimal('3.75')}
