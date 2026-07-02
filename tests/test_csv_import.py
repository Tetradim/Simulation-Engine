import pytest

from sentinel_archive.csv_import import parse_ohlcv_csv


def test_parse_ohlcv_csv_normalizes_symbols_and_numbers():
    rows = parse_ohlcv_csv(
        """timestamp,symbol,open,high,low,close,volume
2026-06-09T13:30:00Z,spy,540.10,541.00,539.80,540.75,1200
2026-06-09T13:31:00Z,SPY,540.75,542.20,540.70,542.00,1500
"""
    )

    assert [row.symbol for row in rows] == ["SPY", "SPY"]
    assert rows[0].close == 540.75
    assert rows[1].volume == 1500


def test_parse_ohlcv_csv_rejects_missing_required_columns():
    with pytest.raises(ValueError, match="missing required columns"):
        parse_ohlcv_csv("timestamp,symbol,close\n2026-06-09T13:30:00Z,SPY,540.75\n")
