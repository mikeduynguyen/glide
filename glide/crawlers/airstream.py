"""Crawler for Airstream trailer listings."""
from __future__ import annotations

import argparse
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence
from urllib.parse import urljoin
from urllib.request import OpenerDirector, Request, build_opener

from glide.html import HTMLElement, parse_html

_LOGGER = logging.getLogger(__name__)

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

_DEFAULT_LIST_URL_TEMPLATE = (
    "https://www.airstreamclassifieds.com/ads/category/airstream-trailers/page/{page}/"
)

_LISTING_URL_RE = re.compile(r"/ads/[\\w-]+/?$")
_PRICE_RE = re.compile(r"([0-9][0-9,]*)")
_YEAR_RE = re.compile(r"(19|20)\\d{2}")


@dataclass(slots=True)
class AirstreamListing:
    """Structured representation of a single trailer listing."""

    url: str
    title: Optional[str] = None
    year: Optional[int] = None
    make: Optional[str] = None
    model: Optional[str] = None
    price: Optional[int] = None
    currency: Optional[str] = None
    condition: Optional[str] = None
    location: Optional[str] = None
    options: List[str] = field(default_factory=list)
    configurations: Dict[str, str] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)


class AirstreamCrawler:
    """Crawler capable of ingesting listings from Airstream classifieds."""

    def __init__(
        self,
        list_url_template: str = _DEFAULT_LIST_URL_TEMPLATE,
        *,
        opener: Optional[OpenerDirector] = None,
        request_timeout: float = 20.0,
    ) -> None:
        self.list_url_template = list_url_template
        self.opener = opener or build_opener()
        self.request_timeout = request_timeout

    def crawl(
        self, *, page_limit: int = 1, delay: float = 0.0, include_raw: bool = True
    ) -> Iterator[AirstreamListing]:
        """Iterate through parsed listings from the configured classifieds feed."""

        for listing_url in self.iter_listing_urls(page_limit=page_limit):
            soup = self._fetch_soup(listing_url)
            listing = self.parse_listing_soup(listing_url, soup)
            if not include_raw:
                listing.raw.clear()
            yield listing
            if delay:
                time.sleep(delay)

    # ------------------------------------------------------------------
    # Fetch helpers

    def iter_listing_urls(self, *, page_limit: int = 1) -> Iterator[str]:
        """Yield listing URLs discovered from paginated index pages."""

        seen: set[str] = set()
        for page in range(1, page_limit + 1):
            list_page_url = self.list_url_template.format(page=page)
            _LOGGER.debug("Fetching listing index page %s", list_page_url)
            soup = self._fetch_soup(list_page_url)
            for anchor in soup.select("a[href]"):
                href = anchor.get("href", "").strip()
                if not href:
                    continue
                absolute_url = urljoin(list_page_url, href)
                if not _LISTING_URL_RE.search(absolute_url):
                    continue
                if absolute_url in seen:
                    continue
                seen.add(absolute_url)
                yield absolute_url

    def _fetch_soup(self, url: str) -> HTMLElement:
        html = self._fetch_html(url)
        return parse_html(html)

    def _fetch_html(self, url: str) -> str:
        request = Request(url, headers=_DEFAULT_HEADERS)
        with self.opener.open(request, timeout=self.request_timeout) as response:
            content = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
            return content.decode(charset, errors="replace")

    # ------------------------------------------------------------------
    # Parsing logic

    @staticmethod
    def parse_listing_soup(url: str, soup: HTMLElement) -> AirstreamListing:
        listing = AirstreamListing(url=url)
        listing.title = _clean_text(_first_text(soup.select_one("h1")))

        json_ld = _extract_json_ld(soup)
        labeled_values = _extract_labeled_values(soup)

        raw: Dict[str, Any] = {}
        if json_ld:
            raw["json_ld"] = json_ld
        if labeled_values:
            raw["labeled_values"] = labeled_values

        structured = _select_structured_vehicle(json_ld)
        if structured:
            listing.currency = _extract_currency(structured)
            listing.price = listing.price or _extract_price(structured)
            listing.condition = listing.condition or _clean_text(
                structured.get("itemCondition")
            )
            listing.make = listing.make or _extract_make(structured)
            listing.model = listing.model or _extract_model(structured)
            listing.year = listing.year or _coerce_int(structured.get("modelDate"))
            listing.location = listing.location or _extract_location(structured)
            options = _extract_options(structured)
            if options:
                listing.options = sorted({*listing.options, *options})
            configurations = _extract_configurations(structured)
            if configurations:
                listing.configurations.update(
                    {k: v for k, v in configurations.items() if v}
                )

        if labeled_values:
            price_value = _coerce_price(labeled_values.get("price"))
            if price_value is not None:
                listing.price = price_value
            condition_text = labeled_values.get("condition")
            if condition_text:
                listing.condition = condition_text
            year_value = _coerce_int(labeled_values.get("year"))
            if year_value is not None:
                listing.year = year_value
            make_text = labeled_values.get("make")
            if make_text:
                listing.make = make_text
            model_text = labeled_values.get("model")
            if model_text:
                listing.model = model_text
            location_text = labeled_values.get("location")
            if location_text:
                listing.location = location_text
            options_text = labeled_values.get("options")
            listing.options = _merge_options(listing.options, options_text)
            config_text = labeled_values.get("configuration")
            listing.configurations = _merge_configurations(
                listing.configurations, config_text
            )

        if listing.title:
            title_parts = _parse_title(listing.title)
            listing.year = listing.year or title_parts.get("year")
            listing.make = listing.make or title_parts.get("make")
            listing.model = listing.model or title_parts.get("model")

        listing.raw = raw
        return listing


# ----------------------------------------------------------------------
# Structured data helpers


def _extract_json_ld(soup: HTMLElement) -> List[Dict[str, Any]]:
    payload: List[Dict[str, Any]] = []
    for script in soup.select('script[type="application/ld+json"]'):
        text = script.string
        if not text:
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            payload.extend([d for d in data if isinstance(d, dict)])
        elif isinstance(data, dict):
            payload.append(data)
    return payload


def _select_structured_vehicle(data: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for candidate in data:
        type_hint = candidate.get("@type")
        if isinstance(type_hint, list):
            types = {t.lower() for t in type_hint if isinstance(t, str)}
        elif isinstance(type_hint, str):
            types = {type_hint.lower()}
        else:
            types = set()
        if {"vehicle", "product"} & types:
            return candidate
        if candidate.get("brand") and candidate.get("offers"):
            return candidate
    return None


def _extract_price(data: Dict[str, Any]) -> Optional[int]:
    offers = data.get("offers")
    if isinstance(offers, dict):
        price = offers.get("price")
    elif isinstance(offers, list) and offers:
        price = offers[0].get("price") if isinstance(offers[0], dict) else None
    else:
        price = data.get("price")
    return _coerce_int(price) or _coerce_price(price)


def _extract_currency(data: Dict[str, Any]) -> Optional[str]:
    offers = data.get("offers")
    if isinstance(offers, dict):
        currency = offers.get("priceCurrency")
    elif isinstance(offers, list) and offers:
        first = offers[0]
        currency = first.get("priceCurrency") if isinstance(first, dict) else None
    else:
        currency = data.get("priceCurrency")
    return _clean_text(currency)


def _extract_make(data: Dict[str, Any]) -> Optional[str]:
    brand = data.get("brand")
    if isinstance(brand, dict):
        return _clean_text(brand.get("name"))
    if isinstance(brand, str):
        return _clean_text(brand)
    manufacturer = data.get("manufacturer")
    if isinstance(manufacturer, dict):
        return _clean_text(manufacturer.get("name"))
    if isinstance(manufacturer, str):
        return _clean_text(manufacturer)
    return None


def _extract_model(data: Dict[str, Any]) -> Optional[str]:
    model = data.get("model")
    if isinstance(model, dict):
        return _clean_text(model.get("name"))
    if isinstance(model, str):
        return _clean_text(model)
    vehicle_model = data.get("vehicleModel")
    if isinstance(vehicle_model, dict):
        return _clean_text(vehicle_model.get("name"))
    if isinstance(vehicle_model, str):
        return _clean_text(vehicle_model)
    name = data.get("name")
    if isinstance(name, str):
        name_parts = _parse_title(name)
        return name_parts.get("model")
    return None


def _extract_location(data: Dict[str, Any]) -> Optional[str]:
    offers = data.get("offers")
    if isinstance(offers, dict):
        area = offers.get("areaServed") or offers.get("availableAtOrFrom")
    else:
        area = data.get("areaServed")
    if isinstance(area, dict):
        return _clean_text(area.get("name") or area.get("addressLocality"))
    if isinstance(area, str):
        return _clean_text(area)
    address = data.get("address")
    if isinstance(address, dict):
        parts = [
            _clean_text(address.get(key))
            for key in ("addressLocality", "addressRegion")
        ]
        return ", ".join([part for part in parts if part]) or None
    if isinstance(address, str):
        return _clean_text(address)
    return None


def _extract_options(data: Dict[str, Any]) -> List[str]:
    options: List[str] = []
    feature_list = data.get("featureList")
    if isinstance(feature_list, list):
        options.extend(_clean_text(item) for item in feature_list if item)
    elif isinstance(feature_list, str):
        options.extend(_split_options(feature_list))
    additional_properties = data.get("additionalProperty")
    return [opt for opt in options if opt]


def _extract_configurations(data: Dict[str, Any]) -> Dict[str, str]:
    configurations: Dict[str, str] = {}
    config = data.get("vehicleConfiguration")
    if isinstance(config, dict):
        for key, value in config.items():
            clean_key = _clean_text(key)
            clean_value = _clean_text(value)
            if clean_key and clean_value:
                configurations[clean_key] = clean_value
    elif isinstance(config, str):
        for item in _split_options(config):
            if ":" in item:
                key, value = [segment.strip() for segment in item.split(":", 1)]
                if key and value:
                    configurations[key] = value
    additional_properties = data.get("additionalProperty")
    if isinstance(additional_properties, list):
        for prop in additional_properties:
            if not isinstance(prop, dict):
                continue
            name = _clean_text(prop.get("name"))
            value = _clean_text(prop.get("value"))
            if name and value:
                configurations[name] = value
    return configurations


# ----------------------------------------------------------------------
# Label parsing helpers


def _extract_labeled_values(soup: HTMLElement) -> Dict[str, str]:
    labeled: Dict[str, str] = {}
    for definition_list in soup.select("dl"):
        terms = definition_list.find_all("dt")
        descriptions = definition_list.find_all("dd")
        if len(terms) != len(descriptions):
            continue
        for term, description in zip(terms, descriptions):
            key = _normalize_label(term.get_text(" "))
            value = _clean_text(description.get_text(" "))
            if key and value:
                labeled[key] = value
    for table in soup.select("table"):
        for row in table.select("tr"):
            cells = row.find_all(["th", "td"])
            if len(cells) != 2:
                continue
            key = _normalize_label(cells[0].get_text(" "))
            value = _clean_text(cells[1].get_text(" "))
            if key and value:
                labeled[key] = value
    for paragraph in soup.select("p"):
        text = paragraph.get_text(" ").strip()
        if ":" not in text:
            continue
        key, value = text.split(":", 1)
        key = _normalize_label(key)
        value = _clean_text(value)
        if key and value and key not in labeled:
            labeled[key] = value
    return labeled


def _merge_options(existing: Sequence[str], options_text: Optional[str]) -> List[str]:
    options = list(existing)
    if options_text:
        options.extend(_split_options(options_text))
    return sorted({opt for opt in options if opt})


def _merge_configurations(
    existing: Dict[str, str], config_text: Optional[str]
) -> Dict[str, str]:
    configurations = dict(existing)
    if config_text:
        for item in _split_options(config_text):
            if ":" in item:
                key, value = [segment.strip() for segment in item.split(":", 1)]
                if key and value:
                    configurations.setdefault(key, value)
    return configurations


# ----------------------------------------------------------------------
# Text parsing helpers


def _split_options(value: str) -> List[str]:
    if not value:
        return []
    separators = [",", "|", "\n", "•"]
    for separator in separators:
        value = value.replace(separator, ";")
    return [segment.strip() for segment in value.split(";") if segment.strip()]


def _normalize_label(label: str) -> str:
    label = _clean_text(label)
    if not label:
        return ""
    label = label.lower()
    replacements = {
        "asking price": "price",
        "price:": "price",
        "year built": "year",
        "manufacture year": "year",
        "manufacturer": "make",
        "brand": "make",
    }
    return replacements.get(label, label)


def _parse_title(title: str) -> Dict[str, Optional[str]]:
    if not title:
        return {}
    match = re.match(r"^(?P<year>(19|20)\\d{2})\\s+(?P<make>[A-Za-z]+)\\s+(?P<model>.+)$", title)
    if not match:
        return {}
    year = _coerce_int(match.group("year"))
    make = match.group("make").strip()
    model = match.group("model").strip()
    return {"year": year, "make": make, "model": model}


def _first_text(element: Optional[HTMLElement]) -> Optional[str]:
    if element is None:
        return None
    return element.get_text(" ")


def _clean_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = re.sub(r"\s+", " ", value).strip()
    if value.startswith("http://") or value.startswith("https://"):
        slug = value.rstrip("/").rsplit("/", 1)[-1]
        if slug:
            value = slug
    return value or None


def _coerce_int(value: Any) -> Optional[int]:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        match = _YEAR_RE.search(value)
        if match:
            return int(match.group(0))
        digits = _PRICE_RE.search(value)
        if digits:
            try:
                return int(digits.group(1).replace(",", ""))
            except ValueError:
                return None
    return None


def _coerce_price(value: Any) -> Optional[int]:
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        match = _PRICE_RE.search(value)
        if match:
            return int(match.group(1).replace(",", ""))
    return None


# ----------------------------------------------------------------------
# CLI entry point


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pages",
        type=int,
        default=1,
        help="Number of listing index pages to crawl.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help="Delay (in seconds) between listing fetches to be polite.",
    )
    parser.add_argument(
        "--list-url-template",
        default=_DEFAULT_LIST_URL_TEMPLATE,
        help="Template for paginated listing index URLs. Must accept a {page} placeholder.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional path to write the collected listings as JSON.",
    )
    parser.add_argument(
        "--no-raw",
        action="store_true",
        help="Exclude raw structured data blobs from the exported listings.",
    )
    args = parser.parse_args(argv)

    crawler = AirstreamCrawler(list_url_template=args.list_url_template)
    listings = list(
        crawler.crawl(
            page_limit=args.pages,
            delay=args.delay,
            include_raw=not args.no_raw,
        )
    )

    payload = json.dumps([asdict(listing) for listing in listings], indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(payload)
    else:
        print(payload)


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    main()
