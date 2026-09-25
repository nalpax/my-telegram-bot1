from decimal import Decimal

from bot.utils.formatting import esc, invoice_amount, parse_price, strip_html


def test_esc_escapes_html():
    assert esc("<b>&</b>") == "&lt;b&gt;&amp;&lt;/b&gt;"
    assert esc(None) == ""


def test_strip_html():
    assert strip_html("<b>Hello</b> &amp; bye") == "Hello & bye"


def test_parse_price_stars_rounds_to_integer():
    assert parse_price("15") == Decimal("15")
    assert parse_price("10,6") == Decimal("11")


def test_parse_price_rejects_garbage():
    for raw in ("", "abc", "-5", "0", "NaN", "Infinity", "1e30", None):
        assert parse_price(raw) is None


def test_invoice_amount_stars():
    assert invoice_amount(Decimal("25")) == 25
