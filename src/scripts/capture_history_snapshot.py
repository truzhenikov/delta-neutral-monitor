from __future__ import annotations

import asyncio
import logging

from src.deps import get_monitoring_service, get_status_service

logger = logging.getLogger(__name__)


def main() -> int:
    get_monitoring_service.cache_clear()
    get_status_service.cache_clear()

    monitoring = get_monitoring_service()
    status_service = get_status_service()
    accounts, connector_statuses = asyncio.run(monitoring.collect_with_status())
    snapshot = status_service.build_status(accounts, connector_statuses)
    logger.info(
        "history_snapshot_captured equity=%.2f warnings=%s",
        snapshot.current_snapshot.total_equity_usd,
        snapshot.current_snapshot.warning_count,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
