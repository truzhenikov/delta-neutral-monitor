from __future__ import annotations

import json
from pathlib import Path

from src.config import get_settings
from src.scripts.capture_history_snapshot import main


def test_capture_history_snapshot_runs_collection_and_persists(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv('HISTORY_STORAGE_DIR', str(tmp_path))
    monkeypatch.setenv('USE_MOCK_DATA', 'true')
    get_settings.cache_clear()

    try:
        exit_code = main()
    finally:
        get_settings.cache_clear()

    assert exit_code == 0
    payload = json.loads((tmp_path / 'portfolio-history.json').read_text(encoding='utf-8'))
    assert payload
    assert payload[0]['warnings'] is not None
