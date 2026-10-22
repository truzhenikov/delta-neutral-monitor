from scripts import delta_portfolio_to_funding_sheet as exporter


def test_new_perps_accounts_map_to_existing_dedicated_columns() -> None:
    assert exporter.EXCHANGE_TO_HEADER['backpack:monitor'] == 'Backpack'
    assert exporter.EXCHANGE_TO_HEADER['polymarket:main'] == 'Polymarket'
    assert len(set(exporter.EXCHANGE_TO_HEADER.values())) == len(exporter.EXCHANGE_TO_HEADER)
    assert 'backpack:monitor' not in exporter.IGNORED_EXCHANGES
    assert 'polymarket:main' not in exporter.IGNORED_EXCHANGES


def test_var_3c3b_reuses_the_former_kucoin_sheet_column() -> None:
    assert exporter.EXCHANGE_TO_HEADER["variational:var-3c3b"] == "Var-3c3b"
    assert "kucoin:kucoin-1" not in exporter.EXCHANGE_TO_HEADER
