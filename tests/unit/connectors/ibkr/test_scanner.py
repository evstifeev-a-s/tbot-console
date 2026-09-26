from __future__ import annotations

import pytest
from ib_async.util import UNSET_DOUBLE, UNSET_INTEGER

from tbot_console.connectors.ibkr.scanner import (
    MAX_ROWS,
    ScanLocation,
    ScannerVocabulary,
    ScanRequest,
    ScanRequestError,
    ScanType,
    build_subscription,
    filter_options,
    parse_scanner_parameters,
)

PARAMETERS_XML = """
<ScanParameterResponse>
  <InstrumentList varName="instrumentList">
    <Instrument><name>US Stocks</name><type>STK</type></Instrument>
    <Instrument><name>US Futures</name><type>FUT.US</type></Instrument>
  </InstrumentList>
  <LocationTree>
    <Location>
      <displayName>United States</displayName>
      <locationCode>STK.US</locationCode>
      <instruments>STK</instruments>
      <locationTree>
        <Location>
          <displayName>Major exchanges</displayName>
          <locationCode>STK.US.MAJOR</locationCode>
          <instruments>STK</instruments>
        </Location>
      </locationTree>
    </Location>
  </LocationTree>
  <ScanTypeList>
    <ScanType>
      <displayName>Top % Gainers</displayName>
      <scanCode>TOP_PERC_GAIN</scanCode>
      <instruments>STK,FUT.US</instruments>
    </ScanType>
    <ScanType>
      <displayName>Most Active</displayName>
      <scanCode>MOST_ACTIVE</scanCode>
      <instruments>STK</instruments>
    </ScanType>
  </ScanTypeList>
  <FilterList>
    <RangeFilter><AbstractField><code>priceAbove</code></AbstractField></RangeFilter>
    <RangeFilter><AbstractField><code>impliedVolAbove</code></AbstractField></RangeFilter>
  </FilterList>
</ScanParameterResponse>
"""


class TestScanRequest:
    def test_defaults_target_us_major_stocks(self):
        request = ScanRequest(scan_code="TOP_PERC_GAIN")
        assert request.instrument == "STK"
        assert request.location == "STK.US.MAJOR"
        assert request.limit == MAX_ROWS

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"scan_code": ""},
            {"scan_code": "TOP_PERC_GAIN", "instrument": ""},
            {"scan_code": "TOP_PERC_GAIN", "location": ""},
            {"scan_code": "TOP_PERC_GAIN", "limit": 0},
            {"scan_code": "TOP_PERC_GAIN", "limit": -5},
        ],
    )
    def test_rejects_missing_essentials(self, kwargs: dict):
        with pytest.raises(ScanRequestError):
            ScanRequest(**kwargs)

    def test_rejects_empty_price_range(self):
        with pytest.raises(ScanRequestError, match="price range is empty"):
            ScanRequest(scan_code="X", above_price=100.0, below_price=10.0)

    def test_rejects_empty_market_cap_range(self):
        with pytest.raises(ScanRequestError, match="market_cap range is empty"):
            ScanRequest(scan_code="X", market_cap_above=1e9, market_cap_below=1e6)

    def test_accepts_a_one_sided_bound(self):
        assert ScanRequest(scan_code="X", above_price=10.0).below_price is None


class TestBuildSubscription:
    def test_core_fields(self):
        subscription = build_subscription(
            ScanRequest(scan_code="TOP_PERC_GAIN", instrument="STK", location="STK.US", limit=25)
        )
        assert subscription.scanCode == "TOP_PERC_GAIN"
        assert subscription.instrument == "STK"
        assert subscription.locationCode == "STK.US"
        assert subscription.numberOfRows == 25

    def test_unset_bounds_keep_ib_sentinels(self):
        subscription = build_subscription(ScanRequest(scan_code="TOP_PERC_GAIN"))

        assert subscription.abovePrice == UNSET_DOUBLE
        assert subscription.belowPrice == UNSET_DOUBLE
        assert subscription.marketCapAbove == UNSET_DOUBLE
        assert subscription.marketCapBelow == UNSET_DOUBLE
        assert subscription.aboveVolume == UNSET_INTEGER
        assert subscription.averageOptionVolumeAbove == UNSET_INTEGER

    def test_zero_is_a_real_bound_not_an_unset_one(self):
        subscription = build_subscription(ScanRequest(scan_code="X", above_price=0.0))
        assert subscription.abovePrice == 0.0

    def test_every_named_bound_reaches_ib(self):
        subscription = build_subscription(
            ScanRequest(
                scan_code="X",
                above_price=10.0,
                below_price=500.0,
                above_volume=1_000_000,
                above_option_volume=500,
                market_cap_above=1e9,
                market_cap_below=1e12,
                exclude_convertible=True,
                stock_type="CORP",
            )
        )
        assert subscription.abovePrice == 10.0
        assert subscription.belowPrice == 500.0
        assert subscription.aboveVolume == 1_000_000
        assert subscription.averageOptionVolumeAbove == 500
        assert subscription.marketCapAbove == 1e9
        assert subscription.marketCapBelow == 1e12
        assert subscription.excludeConvertible is True
        assert subscription.stockTypeFilter == "CORP"

    def test_limit_is_clamped_to_the_ib_cap(self, caplog: pytest.LogCaptureFixture):
        subscription = build_subscription(ScanRequest(scan_code="X", limit=500))
        assert subscription.numberOfRows == MAX_ROWS
        assert "at most" in caplog.text

    def test_extra_filters_become_tag_values(self):
        options = filter_options(
            ScanRequest(
                scan_code="X", extra_filters=(("impliedVolAbove", "30"), ("avgVolumeAbove", 1000))
            )
        )
        assert [(o.tag, o.value) for o in options] == [
            ("impliedVolAbove", "30"),
            ("avgVolumeAbove", "1000"),
        ]

    def test_no_extra_filters_is_an_empty_list(self):
        assert filter_options(ScanRequest(scan_code="X")) == []


class TestParseParameters:
    @pytest.fixture
    def vocabulary(self) -> ScannerVocabulary:
        return parse_scanner_parameters(PARAMETERS_XML)

    def test_scan_types(self, vocabulary: ScannerVocabulary):
        assert [s.code for s in vocabulary.scan_types] == ["MOST_ACTIVE", "TOP_PERC_GAIN"]
        gainers = next(s for s in vocabulary.scan_types if s.code == "TOP_PERC_GAIN")
        assert gainers.display_name == "Top % Gainers"
        assert gainers.instruments == ("STK", "FUT.US")

    def test_nested_locations_are_found(self, vocabulary: ScannerVocabulary):
        assert [loc.code for loc in vocabulary.locations] == ["STK.US", "STK.US.MAJOR"]

    def test_instruments_and_filters(self, vocabulary: ScannerVocabulary):
        assert vocabulary.instruments == ("FUT.US", "STK")
        assert vocabulary.filters == ("impliedVolAbove", "priceAbove")

    def test_lookup_helpers(self, vocabulary: ScannerVocabulary):
        assert vocabulary.knows_scan_code("TOP_PERC_GAIN")
        assert vocabulary.knows_scan_code("top_perc_gain")
        assert not vocabulary.knows_scan_code("NOPE")
        assert vocabulary.knows_location("STK.US.MAJOR")
        assert vocabulary.knows_instrument("STK")
        assert vocabulary.knows_filter("priceAbove")
        assert not vocabulary.knows_filter("nope")

    def test_scan_codes_narrowed_by_instrument(self, vocabulary: ScannerVocabulary):
        assert vocabulary.scan_codes_for("FUT.US") == ("TOP_PERC_GAIN",)
        assert set(vocabulary.scan_codes_for("STK")) == {"TOP_PERC_GAIN", "MOST_ACTIVE"}

    def test_locations_narrowed_by_instrument(self, vocabulary: ScannerVocabulary):
        assert vocabulary.locations_for("STK") == ("STK.US", "STK.US.MAJOR")

    def test_empty_xml_gives_an_empty_vocabulary(self):
        empty = parse_scanner_parameters("   ")
        assert empty.is_empty
        assert empty.scan_codes_for("STK") == ()

    def test_malformed_xml_is_reported(self):
        with pytest.raises(ScanRequestError, match="not valid XML"):
            parse_scanner_parameters("<ScanParameterResponse><oops>")

    def test_dtd_is_refused(self):
        payload = (
            '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">]><ScanParameterResponse/>'
        )
        with pytest.raises(ScanRequestError, match="DTD"):
            parse_scanner_parameters(payload)

    def test_entries_without_a_code_are_skipped(self):
        vocabulary = parse_scanner_parameters(
            "<ScanParameterResponse><ScanTypeList>"
            "<ScanType><displayName>No code</displayName></ScanType>"
            "<ScanType><scanCode>REAL</scanCode></ScanType>"
            "</ScanTypeList></ScanParameterResponse>"
        )
        assert [s.code for s in vocabulary.scan_types] == ["REAL"]

    def test_duplicate_codes_are_collapsed(self):
        vocabulary = parse_scanner_parameters(
            "<ScanParameterResponse>"
            "<ScanType><scanCode>DUP</scanCode><displayName>First</displayName></ScanType>"
            "<ScanType><scanCode>DUP</scanCode><displayName>Second</displayName></ScanType>"
            "</ScanParameterResponse>"
        )
        assert len(vocabulary.scan_types) == 1
        assert vocabulary.scan_types[0].display_name == "First"


class TestUnknowns:
    @pytest.fixture
    def vocabulary(self) -> ScannerVocabulary:
        return parse_scanner_parameters(PARAMETERS_XML)

    def test_a_valid_request_has_no_complaints(self, vocabulary: ScannerVocabulary):
        request = ScanRequest(
            scan_code="TOP_PERC_GAIN",
            instrument="STK",
            location="STK.US.MAJOR",
            extra_filters=(("priceAbove", "10"),),
        )
        assert vocabulary.unknowns(request) == ()

    def test_unknown_scan_code(self, vocabulary: ScannerVocabulary):
        complaints = vocabulary.unknowns(ScanRequest(scan_code="NOPE"))
        assert any("scan code" in c for c in complaints)

    def test_scan_code_wrong_for_the_instrument(self, vocabulary: ScannerVocabulary):
        complaints = vocabulary.unknowns(
            ScanRequest(scan_code="MOST_ACTIVE", instrument="FUT.US", location="STK.US")
        )
        assert any("does not apply to instrument" in c for c in complaints)

    def test_unknown_location_and_filter(self, vocabulary: ScannerVocabulary):
        complaints = vocabulary.unknowns(
            ScanRequest(
                scan_code="TOP_PERC_GAIN",
                location="STK.MARS",
                extra_filters=(("madeUpTag", "1"),),
            )
        )
        assert any("location" in c for c in complaints)
        assert any("filter tag" in c for c in complaints)

    def test_empty_vocabulary_never_complains(self):
        assert ScannerVocabulary().unknowns(ScanRequest(scan_code="ANYTHING")) == ()


class TestSupports:
    def test_empty_instrument_list_is_treated_as_universal(self):
        assert ScanType(code="X", display_name="", instruments=()).supports("STK")
        assert ScanLocation(code="X", display_name="", instruments=()).supports("FUT.US")

    def test_membership_is_case_insensitive(self):
        assert ScanType(code="X", display_name="", instruments=("STK",)).supports("stk")
