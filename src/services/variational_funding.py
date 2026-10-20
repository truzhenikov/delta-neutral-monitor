"""Confirmed Omni funding transfers; never reconstruct from current positions."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Awaitable, Callable

from src.connectors.real_connectors import VariationalRealConnector
from src.services.credential_store import CredentialStore


async def fetch_variational_funding(
    store: CredentialStore,
    refs: list[str],
    start: datetime,
    end: datetime,
    request_json: Callable[..., Awaitable[Any]],
    api_base: str,
) -> dict[str, Decimal]:
    if api_base.rstrip('/') != 'https://omni.variational.io':
        raise ValueError('untrusted Variational origin')
    totals: dict[str, Decimal] = {}
    seen: dict[str, dict] = {}
    for ref in refs:
        import asyncio
        from src.services.variational_sessions import VariationalSessions

        token = (await asyncio.to_thread(VariationalSessions(store).token, ref)).strip()
        if not token:
            raise ValueError('missing Variational token')
        headers = VariationalRealConnector._default_headers(token)
        if 'vr-connected-address' not in headers:
            raise ValueError('missing Variational account identity')
        offset = 0
        expected_count = None
        account_ids: set[str] = set()
        while True:
            payload = await request_json(
                'GET', api_base.rstrip('/') + '/api/transfers', headers=headers, follow_redirects=False,
                params={
                    'type': 'funding',
                    'created_at_gte': start.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z'),
                    'created_at_lte': end.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z'),
                    'order_by': 'created_at', 'order': 'asc', 'limit': 20, 'offset': offset,
                },
            )
            if not isinstance(payload, dict) or not isinstance(payload.get('result'), list):
                raise ValueError('unexpected Variational history')
            pagination = payload.get('pagination')
            if not isinstance(pagination, dict) or type(pagination.get('object_count')) is not int:
                raise ValueError('missing Variational pagination')
            count = pagination['object_count']
            if count < 0 or (expected_count is not None and count != expected_count):
                raise ValueError('Variational history changed during pagination')
            expected_count = count
            rows = payload['result']
            for item in rows:
                record_id = item.get('id')
                if not isinstance(record_id, str) or not record_id:
                    raise ValueError('missing Variational transfer ID')
                if record_id in account_ids:
                    raise ValueError('overlapping Variational pages')
                account_ids.add(record_id)
                if record_id in seen:
                    if seen[record_id] != item:
                        raise ValueError('conflicting Variational transfer')
                    continue
                seen[record_id] = item
                event_time = datetime.fromisoformat(item['created_at'].replace('Z', '+00:00'))
                if event_time.tzinfo is None:
                    raise ValueError('timezone missing from Variational transfer')
                if not start <= event_time < end:
                    continue
                if item.get('transfer_type') != 'funding':
                    raise ValueError('unexpected Variational transfer type')
                # Dividend credits are separately labelled by Omni, not funding-rate income.
                if item.get('funding_type') == 'dividend' or item.get('status') == 'failed':
                    continue
                if item.get('funding_type') not in (None, 'funding') or item.get('status') != 'confirmed':
                    raise ValueError('unconfirmed or unknown Variational funding')
                if item.get('asset') != 'USDC':
                    raise ValueError('unsupported Variational settlement asset')
                symbol = str((item.get('reference_instrument') or {}).get('underlying') or '').strip().upper()
                if not symbol or item.get('qty') in (None, ''):
                    raise ValueError('incomplete Variational transfer')
                amount = Decimal(str(item['qty']))
                if not amount.is_finite():
                    raise ValueError('non-finite Variational funding')
                totals[symbol] = totals.get(symbol, Decimal('0')) + amount
            next_page = pagination.get('next_page')
            if next_page is None:
                if len(account_ids) != count:
                    raise ValueError('truncated Variational history')
                break
            if not rows or not isinstance(next_page, dict) or len(account_ids) >= count:
                raise ValueError('invalid Variational pagination')
            next_offset = int(next_page['offset'])
            if next_offset != offset + len(rows) or next_offset >= count:
                raise ValueError('Variational pagination made no progress')
            offset = next_offset
    return totals
