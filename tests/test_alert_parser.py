import types

from sentinel_archive.alert_parser import build_discord_alert_text, parse_alert_text


def test_parse_buy_alert_normalizes_contract_key():
    alert = parse_alert_text("BTO SPY 500C 6/21 @ 1.25", message_id="m1")

    assert alert.parse_status == "parsed"
    assert alert.action == "buy"
    assert alert.ticker == "SPY"
    assert alert.expiration == "2026-06-21"
    assert alert.strike == 500
    assert alert.option_type == "CALL"
    assert alert.alert_price == 1.25
    assert alert.normalized["contract_key"] == "SPY|2026-06-21|500|CALL"


def test_parse_sell_alert_captures_percentage():
    alert = parse_alert_text("SELL 50% SPY 500 CALLS 6/21 @ 1.40", message_id="m2")

    assert alert.parse_status == "parsed"
    assert alert.action == "sell"
    assert alert.sell_percentage == 50.0
    assert alert.alert_price == 1.40


def test_parse_unrecognized_message_is_stored_as_unparsed():
    alert = parse_alert_text("watching SPY for a setup", message_id="m3")

    assert alert.parse_status == "unparsed"
    assert alert.raw_text == "watching SPY for a setup"


def test_embed_text_is_included_for_parser():
    embed = types.SimpleNamespace(
        author=types.SimpleNamespace(name="Analyst"),
        title="Trade Alert",
        description="BTO SPY 500C 6/21 @ 1.25",
        fields=[types.SimpleNamespace(name="Risk", value="starter")],
        footer=types.SimpleNamespace(text="desk"),
    )
    message = types.SimpleNamespace(content="", embeds=[embed])

    text = build_discord_alert_text(message)

    assert "Trade Alert" in text
    assert "BTO SPY 500C 6/21 @ 1.25" in text
    assert parse_alert_text(text, message_id="embed").parse_status == "parsed"
