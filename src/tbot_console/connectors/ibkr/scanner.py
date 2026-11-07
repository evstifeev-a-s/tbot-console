from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any
from xml.etree import ElementTree

from ib_async import ScannerSubscription, TagValue

from tbot_console.connectors.ibkr.instruments import IBKRInstrument

LOG = logging.getLogger(__name__)

_DOCTYPE_RE = re.compile(r"<!(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)

MAX_ROWS = 50
MAX_ACTIVE_SCANS = 10
DEFAULT_INSTRUMENT = "STK"
DEFAULT_LOCATION = "STK.US.MAJOR"


class ScanRequestError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ScanRequest:
    scan_code: str
    instrument: str = DEFAULT_INSTRUMENT
    location: str = DEFAULT_LOCATION
    limit: int = MAX_ROWS
    above_price: float | None = None
    below_price: float | None = None
    above_volume: int | None = None
    above_option_volume: int | None = None
    market_cap_above: float | None = None
    market_cap_below: float | None = None
    exclude_convertible: bool = False
    stock_type: str = ""
    extra_filters: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.scan_code:
            raise ScanRequestError("scan_code must be set, e.g. TOP_PERC_GAIN")
        if not self.instrument:
            raise ScanRequestError(f"{self.scan_code}: instrument must be set, e.g. STK")
        if not self.location:
            raise ScanRequestError(f"{self.scan_code}: location must be set, e.g. STK.US.MAJOR")
        if self.limit <= 0:
            raise ScanRequestError(f"{self.scan_code}: limit must be positive, got {self.limit}")
        self._check_range("price", self.above_price, self.below_price)
        self._check_range("market_cap", self.market_cap_above, self.market_cap_below)

    def _check_range(self, name: str, lower: float | None, upper: float | None) -> None:
        if lower is not None and upper is not None and lower > upper:
            raise ScanRequestError(
                f"{self.scan_code}: {name} range is empty — "
                f"above ({lower}) is greater than below ({upper})"
            )


@dataclass(frozen=True, slots=True)
class ScanHit:
    rank: int
    con_id: int
    local_symbol: str
    exchange: str
    instrument: IBKRInstrument | None
    distance: str = ""
    benchmark: str = ""
    projection: str = ""
    legs: str = ""


@dataclass(frozen=True, slots=True)
class ScanType:
    code: str
    display_name: str
    instruments: tuple[str, ...]

    def supports(self, instrument: str) -> bool:
        return not self.instruments or instrument.upper() in self.instruments


@dataclass(frozen=True, slots=True)
class ScanLocation:
    code: str
    display_name: str
    instruments: tuple[str, ...]

    def supports(self, instrument: str) -> bool:
        return not self.instruments or instrument.upper() in self.instruments


@dataclass(frozen=True, slots=True)
class ScannerVocabulary:
    scan_types: tuple[ScanType, ...] = ()
    locations: tuple[ScanLocation, ...] = ()
    instruments: tuple[str, ...] = ()
    filters: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not (self.scan_types or self.locations or self.instruments or self.filters)

    def scan_codes_for(self, instrument: str) -> tuple[str, ...]:
        return tuple(s.code for s in self.scan_types if s.supports(instrument))

    def locations_for(self, instrument: str) -> tuple[str, ...]:
        return tuple(loc.code for loc in self.locations if loc.supports(instrument))

    def knows_scan_code(self, code: str) -> bool:
        return any(s.code == code.upper() for s in self.scan_types)

    def knows_location(self, code: str) -> bool:
        return any(loc.code == code.upper() for loc in self.locations)

    def knows_instrument(self, instrument: str) -> bool:
        return instrument.upper() in self.instruments

    def knows_filter(self, tag: str) -> bool:
        return tag in self.filters

    def unknowns(self, request: ScanRequest) -> tuple[str, ...]:
        if self.is_empty:
            return ()
        return (
            *self._unknown_instrument(request),
            *self._unknown_scan_code(request),
            *self._unknown_location(request),
            *self._unknown_filters(request),
        )

    def _unknown_instrument(self, request: ScanRequest) -> list[str]:
        if self.instruments and not self.knows_instrument(request.instrument):
            return [f"instrument {request.instrument!r} is not one IBKR offers"]
        return []

    def _unknown_scan_code(self, request: ScanRequest) -> list[str]:
        if not self.scan_types:
            return []
        if not self.knows_scan_code(request.scan_code):
            return [f"scan code {request.scan_code!r} is not one IBKR offers"]
        if request.scan_code.upper() not in self.scan_codes_for(request.instrument):
            return [
                f"scan code {request.scan_code!r} does not apply to instrument "
                f"{request.instrument!r}"
            ]
        return []

    def _unknown_location(self, request: ScanRequest) -> list[str]:
        if self.locations and not self.knows_location(request.location):
            return [f"location {request.location!r} is not one IBKR offers"]
        return []

    def _unknown_filters(self, request: ScanRequest) -> list[str]:
        if not self.filters:
            return []
        return [
            f"filter tag {tag!r} is not one IBKR offers"
            for tag, _ in request.extra_filters
            if not self.knows_filter(tag)
        ]


def build_subscription(request: ScanRequest) -> ScannerSubscription:
    rows = request.limit
    if rows > MAX_ROWS:
        LOG.warning(
            "IBKR serves at most %d scan rows; asking for %d instead of %d",
            MAX_ROWS,
            MAX_ROWS,
            rows,
        )
        rows = MAX_ROWS

    fields: dict[str, Any] = {
        "scanCode": request.scan_code,
        "instrument": request.instrument,
        "locationCode": request.location,
        "numberOfRows": rows,
    }
    optional: tuple[tuple[str, float | int | None], ...] = (
        ("abovePrice", request.above_price),
        ("belowPrice", request.below_price),
        ("aboveVolume", request.above_volume),
        ("averageOptionVolumeAbove", request.above_option_volume),
        ("marketCapAbove", request.market_cap_above),
        ("marketCapBelow", request.market_cap_below),
    )
    fields.update({name: value for name, value in optional if value is not None})
    if request.exclude_convertible:
        fields["excludeConvertible"] = True
    if request.stock_type:
        fields["stockTypeFilter"] = request.stock_type

    return ScannerSubscription(**fields)


def filter_options(request: ScanRequest) -> list[TagValue]:
    return [TagValue(tag, str(value)) for tag, value in request.extra_filters]


def _child_text(node: ElementTree.Element, tag: str) -> str:
    child = node.find(tag)
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def _csv(text: str) -> tuple[str, ...]:
    return tuple(part.strip().upper() for part in text.split(",") if part.strip())


def _instruments_of(node: ElementTree.Element) -> tuple[str, ...]:
    for tag in ("instruments", "instrument"):
        text = _child_text(node, tag)
        if text:
            return _csv(text)
    return ()


def _parse_without_doctype(xml: str) -> ElementTree.Element:
    if _DOCTYPE_RE.search(xml):
        raise ScanRequestError(
            "IBKR scanner parameters carry a DTD; refusing to parse it "
            "(entity-expansion hardening — IB's own response never has one)"
        )
    try:
        return ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise ScanRequestError(f"IBKR scanner parameters are not valid XML: {exc}") from exc


def _catalogue[E: (ScanType, ScanLocation)](
    root: ElementTree.Element, tag: str, code_tag: str, make: type[E]
) -> tuple[E, ...]:
    found: dict[str, E] = {}
    for node in root.iter(tag):
        code = _child_text(node, code_tag).upper()
        if code and code not in found:
            found[code] = make(
                code=code,
                display_name=_child_text(node, "displayName"),
                instruments=_instruments_of(node),
            )
    return tuple(found[code] for code in sorted(found))


def parse_scanner_parameters(xml: str) -> ScannerVocabulary:
    if not xml.strip():
        LOG.warning("IBKR returned empty scanner parameters")
        return ScannerVocabulary()

    root = _parse_without_doctype(xml)
    scan_types = _catalogue(root, "ScanType", "scanCode", ScanType)
    locations = _catalogue(root, "Location", "locationCode", ScanLocation)
    instruments = {
        text.upper() for node in root.iter("Instrument") if (text := _child_text(node, "type"))
    }
    filters = {text for node in root.iter("AbstractField") if (text := _child_text(node, "code"))}

    return ScannerVocabulary(
        scan_types=scan_types,
        locations=locations,
        instruments=tuple(sorted(instruments)),
        filters=tuple(sorted(filters)),
    )
