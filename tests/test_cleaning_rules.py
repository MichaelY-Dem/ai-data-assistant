from datetime import date

import pytest

import cleaning_rules as cr
from clean_and_load import to_country, to_deal_type, to_sector


@pytest.mark.parametrize("text, expected", [
    ("2019-03-14", date(2019, 3, 14)),
    ("2019/03/14", date(2019, 3, 14)),
    ("14/03/2019", date(2019, 3, 14)),   # slashes: day first
    ("03-14-2019", date(2019, 3, 14)),   # dashes: month first
    ("March 14, 2019", date(2019, 3, 14)),
    ("14-Mar-2019", date(2019, 3, 14)),
    ("43538", date(2019, 3, 14)),        # Excel serial
    ("", None),
    ("N/A", None),
])
def test_parse_date(text, expected):
    assert cr.parse_date(text) == expected


def test_parse_date_rejects_impossible_date():
    with pytest.raises(ValueError):
        cr.parse_date("31/02/2020")


@pytest.mark.parametrize("text, expected", [
    ("1234.50", 1234.5),
    ("1,234.50", 1234.5),
    ("$1,234.5", 1234.5),
    ("USD 1234.5", 1234.5),
    (" 12.5 ", 12.5),
    ("(12.50)", -12.5),
    ("-$12.5", -12.5),
    ("450m", 450.0),
    ("USD 450 million", 450.0),
    ("$1.2345bn", 1234.5),
    ("450000000", 450.0),   # whole dollars
    ("N/A", None),
    ("", None),
])
def test_parse_money(text, expected):
    assert cr.parse_money_musd(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("45.5", 45.5), ("45.5%", 45.5), ("45.5 %", 45.5), ("0.455", 45.5), ("1.000", 100.0), ("", None),
])
def test_parse_pct(text, expected):
    assert cr.parse_pct(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("2023", 2023), ("2023.0", 2023), ("FY2023", 2023), ("FY23", 2023), ("est. 1998", 1998),
])
def test_parse_year(text, expected):
    assert cr.parse_year(text) == expected


@pytest.mark.parametrize("text", ["CO-007", "co-7", "CO7", " CO-007 ", "co 007"])
def test_company_code(text):
    assert cr.normalize_company_code(text) == "CO-007"


def test_parse_capacity():
    assert cr.parse_capacity("250 MW") == (250.0, "MW")
    assert cr.parse_capacity("1,200") == (1200.0, None)
    assert cr.parse_capacity("85ML/day") == (85.0, "ML/day")


def test_aliases():
    assert to_sector("Data Centres") == "data_centers"
    assert to_sector("toll-road") == "toll_roads"
    assert to_country("U.S.") == "United States"
    assert to_deal_type("Refi") == "refinancing"
    with pytest.raises(ValueError):
        to_sector("spaceports")


def test_clean_name_fixes_shouting_and_spaces():
    assert cr.clean_name("  NORTHWIND SOLAR PLC ") == "Northwind Solar PLC"
    assert cr.clean_name("Meridian  Water Ltd") == "Meridian Water Ltd"
