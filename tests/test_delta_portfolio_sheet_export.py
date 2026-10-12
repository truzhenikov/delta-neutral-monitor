from scripts import delta_portfolio_to_funding_sheet as exporter


def test_var_3c3b_reuses_the_former_kucoin_sheet_column() -> None:
    assert exporter.EXCHANGE_TO_HEADER["variational:var-3c3b"] == "Var-3c3b"
    assert "kucoin:kucoin-1" not in exporter.EXCHANGE_TO_HEADER
