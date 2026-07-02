import json
from pathlib import Path

from sentinel_archive.alert_parser import parse_alert_text


GOLDEN_PATH = Path(__file__).parent / "fixtures" / "alert_parser_golden.json"


def test_alert_parser_matches_golden_corpus():
    corpus = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))

    for case in corpus["cases"]:
        alert = parse_alert_text(case["text"], message_id=case["message_id"])
        actual = {
            "parse_status": alert.parse_status,
            "action": alert.action,
            "ticker": alert.ticker,
            "expiration": alert.expiration,
            "strike": alert.strike,
            "option_type": alert.option_type,
            "alert_price": alert.alert_price,
            "sell_percentage": alert.sell_percentage,
            "confidence": alert.confidence,
            "contract_key": alert.normalized.get("contract_key"),
        }

        assert actual == case["expected"], case["name"]
