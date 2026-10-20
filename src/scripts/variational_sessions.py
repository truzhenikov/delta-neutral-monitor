"""Protected local import and periodic session keeper; never accept secret argv."""
from __future__ import annotations

import argparse
from pathlib import Path
import signal
import sys
from threading import Event

from src.config import get_settings
from src.services.credential_store import CredentialStore
from src.services.variational_sessions import SessionError, VariationalSessions


def run_once(store, manager):
    failures = 0
    for entry in store.list_configured_exchanges():
        if entry['base_exchange'] != 'variational' or not entry['enabled']:
            continue
        ref = entry['exchange']
        try:
            if manager.path(ref).exists():
                manager.token(ref)
        except SessionError:
            failures += 1
            print(f'Variational session unavailable [{ref}]', file=sys.stderr)
    return failures


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['import', 'once', 'keep'])
    parser.add_argument('--credential-store', type=Path)
    parser.add_argument('--profile', help='Exact configured reference, e.g. variational:main')
    parser.add_argument('--source', type=Path, help='Protected local JSON cookie jar; never a token')
    args = parser.parse_args(argv)
    store = CredentialStore(args.credential_store or Path(get_settings().credential_store_path))
    manager = VariationalSessions(store)
    if args.command == 'import':
        if not args.profile or not args.source:
            parser.error('import requires --profile and --source')
        try:
            manager.import_file(args.profile, args.source)
        except SessionError:
            print('Variational session unavailable: import rejected', file=sys.stderr)
            return 1
        print('Variational session imported; profile enablement unchanged')
        return 0
    if args.command == 'once':
        return int(bool(run_once(store, manager)))
    stop = Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    while not stop.is_set():
        run_once(store, manager)
        stop.wait(60)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
