from pathlib import Path

from glide.crawlers.airstream import AirstreamCrawler
from glide.html import parse_html


FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


def test_parse_listing_extracts_structured_fields() -> None:
    html = _load_fixture("airstream_listing.html")
    soup = parse_html(html)

    listing = AirstreamCrawler.parse_listing_soup(
        "https://www.airstreamclassifieds.com/ads/example/", soup
    )

    assert listing.year == 2021
    assert listing.make == "Airstream"
    assert listing.model == "Classic 33FB"
    assert listing.price == 149_900
    assert listing.currency == "USD"
    assert listing.condition == "Used - Like New"
    assert listing.location == "Austin, TX"
    assert set(listing.options) == {
        "Solar package",
        "Upgraded upholstery",
        "Convection microwave",
    }
    assert listing.configurations == {
        "Bedroom": "Queen",
        "Axles": "2",
        "Condition": "Excellent",
        "Miles": "5,000",
    }
    assert "json_ld" in listing.raw
    assert listing.raw["json_ld"], "Raw JSON-LD payload should be preserved"
