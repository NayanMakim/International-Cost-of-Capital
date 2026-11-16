"""
Cross-border Cost of Capital Calculator (Streamlit)
===================================================

Compares a project's cost of equity, cost of debt and WACC in USD and in the
currency of a selected country.

Live data sources
-----------------
* Industry unlevered betas ........ Damodaran, betas.xls (U.S. industries)
* BBB (Baa2) USD default spread ... Damodaran, ratings.html (large non-financial firms)
* Statutory corporate tax rates ... OECD Corporate Tax Statistics (combined CIT rate)
* 10-year government bond yields .. Trading Economics (scraped, no API key)

Every fetched number can be overwritten with a manual input in the sidebar.
If a source cannot be reached, the app falls back to clearly labelled
offline values so it still runs; use "Refresh data" or the manual
overrides to replace them.

Python 3.12 compatible.
"""

from __future__ import annotations

import datetime as dt
import io
import re
from typing import Any

import altair as alt
import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------

URL_BETAS = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls"
URL_RATINGS = [
    "https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/ratings.html",
    "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ratings.html",
]
URL_OECD_PAGE = (
    "https://www.oecd.org/en/publications/corporate-tax-statistics-2024_9c27d6e8-en/"
    "full-report/statutory-corporate-income-tax-rates_440f9286.html#chapter-d1e2342-08e74a33fb"
)
# OECD SDMX API: Corporate Income Tax (CIT) - statutory rates (Corporate Tax Statistics database)
URL_OECD_SDMX = (
    "https://sdmx.oecd.org/public/rest/data/OECD.CTP.TPS,DSD_TAX_CIT@DF_CIT,/all"
    "?startPeriod=2018&dimensionAtObservation=AllDimensions&format=csvfilewithlabels"
)
URL_TE_BONDS = "https://tradingeconomics.com/bonds"
URL_TE_COUNTRY = "https://tradingeconomics.com/{slug}/government-bond-yield"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = 30
CACHE_TTL = 6 * 60 * 60  # 6 hours

DEFAULT_INDUSTRY = "Software (System & Application)"
DEFAULT_COUNTRY = "United States"
DEFAULT_ERP = 6.5  # %
DEFAULT_DEBT_SHARE = 30.0  # %

# name -> (ISO3 code used by OECD, Trading Economics URL slug, currency code)
COUNTRIES: dict[str, tuple[str, str, str]] = {
    "United States": ("USA", "united-states", "USD"),
    "Australia": ("AUS", "australia", "AUD"),
    "Austria": ("AUT", "austria", "EUR"),
    "Belgium": ("BEL", "belgium", "EUR"),
    "Brazil": ("BRA", "brazil", "BRL"),
    "Canada": ("CAN", "canada", "CAD"),
    "Chile": ("CHL", "chile", "CLP"),
    "China": ("CHN", "china", "CNY"),
    "Colombia": ("COL", "colombia", "COP"),
    "Czech Republic": ("CZE", "czech-republic", "CZK"),
    "Denmark": ("DNK", "denmark", "DKK"),
    "Egypt": ("EGY", "egypt", "EGP"),
    "Finland": ("FIN", "finland", "EUR"),
    "France": ("FRA", "france", "EUR"),
    "Germany": ("DEU", "germany", "EUR"),
    "Greece": ("GRC", "greece", "EUR"),
    "Hong Kong": ("HKG", "hong-kong", "HKD"),
    "Hungary": ("HUN", "hungary", "HUF"),
    "Iceland": ("ISL", "iceland", "ISK"),
    "India": ("IND", "india", "INR"),
    "Indonesia": ("IDN", "indonesia", "IDR"),
    "Ireland": ("IRL", "ireland", "EUR"),
    "Israel": ("ISR", "israel", "ILS"),
    "Italy": ("ITA", "italy", "EUR"),
    "Japan": ("JPN", "japan", "JPY"),
    "Kenya": ("KEN", "kenya", "KES"),
    "Malaysia": ("MYS", "malaysia", "MYR"),
    "Mexico": ("MEX", "mexico", "MXN"),
    "Netherlands": ("NLD", "netherlands", "EUR"),
    "New Zealand": ("NZL", "new-zealand", "NZD"),
    "Nigeria": ("NGA", "nigeria", "NGN"),
    "Norway": ("NOR", "norway", "NOK"),
    "Pakistan": ("PAK", "pakistan", "PKR"),
    "Peru": ("PER", "peru", "PEN"),
    "Philippines": ("PHL", "philippines", "PHP"),
    "Poland": ("POL", "poland", "PLN"),
    "Portugal": ("PRT", "portugal", "EUR"),
    "Romania": ("ROU", "romania", "RON"),
    "Singapore": ("SGP", "singapore", "SGD"),
    "Slovakia": ("SVK", "slovakia", "EUR"),
    "South Africa": ("ZAF", "south-africa", "ZAR"),
    "South Korea": ("KOR", "south-korea", "KRW"),
    "Spain": ("ESP", "spain", "EUR"),
    "Sweden": ("SWE", "sweden", "SEK"),
    "Switzerland": ("CHE", "switzerland", "CHF"),
    "Thailand": ("THA", "thailand", "THB"),
    "Turkey": ("TUR", "turkey", "TRY"),
    "United Kingdom": ("GBR", "united-kingdom", "GBP"),
    "Vietnam": ("VNM", "vietnam", "VND"),
}

# ----------------------------------------------------------------------------
# Offline fallback values (used ONLY when a live fetch fails).
# These are approximate snapshots and must be verified / overwritten.
# ----------------------------------------------------------------------------

FALLBACK_BETAS = pd.DataFrame(
    {
        "Industry": [
            "Software (System & Application)", "Software (Internet)", "Computer Services",
            "Semiconductor", "Drugs (Pharmaceutical)", "Healthcare Products",
            "Retail (General)", "Restaurant/Dining", "Utility (General)", "Oil/Gas (Integrated)",
            "Auto & Truck", "Telecom. Services", "Machinery", "Chemical (Basic)",
            "Total Market",
        ],
        "Unlevered beta": [
            1.23, 1.40, 0.98, 1.45, 1.00, 0.92, 0.80, 0.82, 0.30, 0.65,
            1.10, 0.55, 0.90, 0.80, 0.85,
        ],
    }
)
FALLBACK_BBB_SPREAD = 0.0111  # Baa2/BBB, large non-financial firms (approx.)
FALLBACK_TAX = {  # combined statutory CIT rate, OECD Corporate Tax Statistics 2024 (approx.)
    "USA": 0.258, "AUS": 0.30, "AUT": 0.23, "BEL": 0.25, "BRA": 0.34, "CAN": 0.261,
    "CHL": 0.27, "CHN": 0.25, "COL": 0.35, "CZE": 0.21, "DNK": 0.22, "EGY": 0.225,
    "FIN": 0.20, "FRA": 0.258, "DEU": 0.299, "GRC": 0.22, "HKG": 0.165, "HUN": 0.09,
    "ISL": 0.21, "IND": 0.252, "IDN": 0.22, "IRL": 0.125, "ISR": 0.23, "ITA": 0.278,
    "JPN": 0.297, "KEN": 0.30, "MYS": 0.24, "MEX": 0.30, "NLD": 0.258, "NZL": 0.28,
    "NGA": 0.30, "NOR": 0.22, "PAK": 0.29, "PER": 0.295, "PHL": 0.25, "POL": 0.19,
    "PRT": 0.315, "ROU": 0.16, "SGP": 0.17, "SVK": 0.21, "ZAF": 0.27, "KOR": 0.264,
    "ESP": 0.25, "SWE": 0.206, "CHE": 0.196, "THA": 0.20, "TUR": 0.25, "GBR": 0.25,
    "VNM": 0.20,
}
FALLBACK_YIELDS = {  # 10-year government yields, decimal (approx.)
    "united-states": 0.041, "australia": 0.044, "austria": 0.029, "belgium": 0.031,
    "brazil": 0.138, "canada": 0.032, "chile": 0.055, "china": 0.018, "colombia": 0.115,
    "czech-republic": 0.044, "denmark": 0.024, "egypt": 0.20, "finland": 0.029,
    "france": 0.034, "germany": 0.026, "greece": 0.033, "hong-kong": 0.030,
    "hungary": 0.069, "iceland": 0.068, "india": 0.065, "indonesia": 0.062,
    "ireland": 0.029, "israel": 0.040, "italy": 0.034, "japan": 0.017, "kenya": 0.13,
    "malaysia": 0.035, "mexico": 0.088, "netherlands": 0.027, "new-zealand": 0.043,
    "nigeria": 0.16, "norway": 0.040, "pakistan": 0.12, "peru": 0.060,
    "philippines": 0.060, "poland": 0.053, "portugal": 0.030, "romania": 0.070,
    "singapore": 0.020, "slovakia": 0.033, "south-africa": 0.090, "south-korea": 0.030,
    "spain": 0.032, "sweden": 0.026, "switzerland": 0.002, "thailand": 0.017,
    "turkey": 0.31, "united-kingdom": 0.045, "vietnam": 0.035,
}

MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|November|December"
)


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def _now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def _to_float(x: Any) -> float | None:
    """Parse '4.123', '4.12%', ' 1,234.5 ' etc. Returns None if not numeric."""
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return None if pd.isna(x) else float(x)
    s = str(x).strip().replace(",", "").replace("%", "").replace("−", "-")
    try:
        return float(s)
    except ValueError:
        return None


def _get(url: str) -> requests.Response:
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r


def _result(data, source, as_of, status, note="") -> dict:
    return {
        "data": data,
        "source": source,
        "as_of": as_of,
        "fetched": _now(),
        "status": status,
        "note": note,
    }


# ----------------------------------------------------------------------------
# Parsers (pure functions on raw bytes/text, so they can be tested offline)
# ----------------------------------------------------------------------------

def parse_betas(content: bytes) -> tuple[pd.DataFrame, str]:
    """Parse Damodaran betas.xls -> DataFrame[Industry, Number of firms, Beta,
    D/E Ratio, Effective Tax rate, Unlevered beta, ...] and an 'as of' string."""
    xls = pd.ExcelFile(io.BytesIO(content))  # pandas picks xlrd (.xls) or openpyxl (.xlsx)
    sheet = next((s for s in xls.sheet_names if "industry average" in s.lower()), xls.sheet_names[0])
    raw = pd.read_excel(xls, sheet_name=sheet, header=None)

    hdr = None
    for i in range(min(len(raw), 40)):
        if any(str(v).strip().lower() == "industry name" for v in raw.iloc[i].values):
            hdr = i
            break
    if hdr is None:
        raise ValueError("Could not locate 'Industry Name' header in betas.xls")

    df = raw.iloc[hdr + 1:].copy()
    df.columns = [str(c).strip() for c in raw.iloc[hdr].values]
    df = df.rename(columns={"Industry Name": "Industry"})
    ub_col = next(
        (c for c in df.columns if c.lower() == "unlevered beta"),
        next(c for c in df.columns if "unlevered beta" in c.lower() and "cash" not in c.lower()),
    )
    if ub_col != "Unlevered beta":
        df = df.rename(columns={ub_col: "Unlevered beta"})
    df["Unlevered beta"] = pd.to_numeric(df["Unlevered beta"], errors="coerce")
    df["Industry"] = df["Industry"].astype(str).str.strip()
    df = df[df["Unlevered beta"].notna() & (df["Industry"] != "") & (df["Industry"].str.lower() != "nan")]

    keep = ["Industry"] + [
        c for c in df.columns
        if c != "Industry" and any(k in c.lower() for k in
                                   ["number of firms", "beta", "d/e", "tax rate", "cash"])
    ]
    df = df[keep].reset_index(drop=True)
    for c in df.columns[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # Find a date anywhere near the top of any sheet ("Date updated: ...").
    as_of = ""
    for s in xls.sheet_names:
        top = pd.read_excel(xls, sheet_name=s, header=None, nrows=12)
        cells = [v for v in top.values.flatten() if pd.notna(v)]
        for j, v in enumerate(cells):
            if isinstance(v, (dt.datetime, pd.Timestamp)):
                as_of = pd.Timestamp(v).strftime("%Y-%m-%d")
                break
            txt = str(v)
            if "date" in txt.lower() or "updated" in txt.lower():
                m = re.search(rf"({MONTHS})\s+\d{{1,2}}?,?\s*\d{{4}}|\d{{1,2}}/\d{{1,2}}/\d{{2,4}}", txt)
                if m:
                    as_of = m.group(0)
                    break
                if j + 1 < len(cells):
                    nxt = cells[j + 1]
                    if isinstance(nxt, (dt.datetime, pd.Timestamp)):
                        as_of = pd.Timestamp(nxt).strftime("%Y-%m-%d")
                        break
                    if re.search(r"\d{4}|\d{1,2}/\d{1,2}/\d{2}", str(nxt)):
                        as_of = str(nxt)
                        break
        if as_of:
            break
    return df, as_of or "see file"


def parse_bbb_spread(html: str) -> tuple[float, str]:
    """Return (Baa2/BBB spread as decimal, 'as of' text) from Damodaran ratings.html.
    The first table on the page is for large non-financial service firms."""
    spread = None
    try:
        tables = pd.read_html(io.StringIO(html))
        for t in tables:  # first table containing Baa2/BBB = large non-financial firms
            flat = t.map(lambda v: "" if pd.isna(v) else str(v))
            mask = flat.apply(lambda r: r.str.contains("Baa2/BBB", regex=False)).any(axis=1)
            if mask.any():
                row = flat[mask].iloc[0].tolist()
                vals = [v for v in row if "%" in v] or row[::-1]
                for v in vals:
                    f = _to_float(v)
                    if f is not None:
                        spread = f / 100 if ("%" in v or f > 0.2) else f
                        break
                if spread is not None:
                    break
    except ValueError:
        pass

    text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
    if spread is None:
        i = text.find("Baa2/BBB")
        if i < 0:
            raise ValueError("Baa2/BBB row not found on ratings page")
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", text[i:i + 200])
        if not m:
            raise ValueError("Spread for Baa2/BBB not found")
        spread = float(m.group(1)) / 100

    m = re.search(rf"({MONTHS})\s+(\d{{1,2}},\s*)?\d{{4}}", text)
    as_of = m.group(0) if m else "see page"
    return spread, as_of


def parse_oecd_sdmx(csv_text: str) -> pd.DataFrame:
    """Parse OECD SDMX 'csvfilewithlabels' for the CIT dataflow and return the
    latest combined statutory CIT rate per jurisdiction: index ISO3, cols
    [Country, Rate (decimal), Year]."""
    df = pd.read_csv(io.StringIO(csv_text), low_memory=False)
    cols = {c.upper(): c for c in df.columns}
    area, year, val = cols.get("REF_AREA"), cols.get("TIME_PERIOD"), cols.get("OBS_VALUE")
    if not (area and year and val):
        raise ValueError("Unexpected OECD CSV layout")
    name_col = next((c for c in df.columns if c.lower() in ("reference area", "ref_area label")), None)

    skip = {area, year, val, name_col}
    text_cols = [c for c in df.columns
                 if c not in skip and not pd.api.types.is_numeric_dtype(df[c])]
    label = df[text_cols].fillna("").astype(str).agg(" | ".join, axis=1).str.lower()
    is_comb = label.str.contains("combined") | label.str.contains("comb")
    # drop targeted / small-business and dividend series, but keep "non-targeted"
    is_comb &= ~label.str.contains(r"(?<!non-)(?<!non )\btargeted\b", regex=True)
    for e in ("small business", "small-business", "dividend", "less deductions", "exclusive of surtax"):
        is_comb &= ~label.str.contains(e, regex=False)
    d = df[is_comb].copy()
    if d.empty:
        raise ValueError("Combined CIT rate series not found in OECD data")

    d["_v"] = pd.to_numeric(d[val], errors="coerce")
    d["_y"] = pd.to_numeric(d[year].astype(str).str[:4], errors="coerce")
    d = d.dropna(subset=["_v", "_y"])
    # latest year per jurisdiction; if several combined series remain, the standard
    # (headline) rate is the highest of them
    d = d.sort_values(["_y", "_v"]).groupby(area).tail(1)
    out = pd.DataFrame(
        {
            "Country": d[name_col].values if name_col else d[area].values,
            "Rate": d["_v"].values / 100.0,
            "Year": d["_y"].astype(int).values,
        },
        index=d[area].values,
    )
    return out


def parse_te_bonds(html: str) -> dict[str, dict]:
    """Parse tradingeconomics.com/bonds -> {slug: {'yield': decimal, 'date': str, 'name': str}}.
    Rows are identified by links of the form /<country-slug>/government-bond-yield (10Y)."""
    soup = BeautifulSoup(html, "lxml")
    out: dict[str, dict] = {}
    for a in soup.find_all("a", href=True):
        m = re.search(r"/([a-z0-9\-]+)/government-bond-yield/?$", a["href"].lower())
        if not m:
            continue
        slug = m.group(1)
        if slug in out:
            continue
        tr = a.find_parent("tr")
        if tr is None:
            continue
        tds = tr.find_all("td")
        idx = next((i for i, td in enumerate(tds) if a in td.descendants), -1)
        y = None
        for td in tds[idx + 1:]:
            y = _to_float(td.get_text(strip=True))
            if y is not None:
                break
        if y is None:
            continue
        date = tds[-1].get_text(strip=True) if tds else ""
        out[slug] = {"yield": y / 100.0, "date": date, "name": a.get_text(strip=True)}
    if not out:
        raise ValueError("No 10Y yield rows found on Trading Economics page")
    return out


def parse_te_country(html: str) -> tuple[float, str]:
    """Parse a Trading Economics country bond page; the meta description reads
    like '... 10Y Bond Note Yield rose to 4.15% on November 14, 2026 ...'."""
    soup = BeautifulSoup(html, "lxml")
    texts = []
    for meta in soup.find_all("meta"):
        if meta.get("name", "").lower() == "description" or meta.get("property", "") == "og:description":
            texts.append(meta.get("content", ""))
    texts.append(soup.get_text(" ", strip=True)[:20000])
    date_re = rf"(({MONTHS})\s+\d{{1,2}},?\s*\d{{4}})"
    for t in texts:
        m = re.search(rf"(-?\d+(?:\.\d+)?)\s*(?:%|percent)[^.%]{{0,60}}?{date_re}", t)
        if m:
            return float(m.group(1)) / 100.0, m.group(2)
    for t in texts:  # value without a date in the same sentence
        m = re.search(r"(?:yield|bond)[^.%]{0,80}?(?:to|at|around)\s+(-?\d+(?:\.\d+)?)\s*(?:%|percent)", t, re.I)
        if m:
            return float(m.group(1)) / 100.0, "latest on page"
    raise ValueError("Yield not found on Trading Economics country page")


# ----------------------------------------------------------------------------
# Fetchers (cached; 'Refresh data' clears the cache)
# ----------------------------------------------------------------------------

@st.cache_data(ttl=CACHE_TTL, show_spinner="Fetching industry betas (Damodaran)...")
def fetch_betas() -> dict:
    try:
        df, as_of = parse_betas(_get(URL_BETAS).content)
        return _result(df, URL_BETAS, as_of, "Live")
    except Exception as e:  # noqa: BLE001
        return _result(FALLBACK_BETAS.copy(), URL_BETAS, "offline snapshot", "Fallback", str(e))


@st.cache_data(ttl=CACHE_TTL, show_spinner="Fetching BBB spread (Damodaran)...")
def fetch_bbb_spread() -> dict:
    err = ""
    for url in URL_RATINGS:
        try:
            spread, as_of = parse_bbb_spread(_get(url).text)
            return _result(spread, url, as_of, "Live")
        except Exception as e:  # noqa: BLE001
            err = str(e)
    return _result(FALLBACK_BBB_SPREAD, URL_RATINGS[0], "offline snapshot", "Fallback", err)


@st.cache_data(ttl=CACHE_TTL, show_spinner="Fetching corporate tax rates (OECD)...")
def fetch_tax_rates() -> dict:
    errs = []
    # 1) OECD SDMX API (the machine-readable version of the Corporate Tax Statistics table)
    try:
        df = parse_oecd_sdmx(_get(URL_OECD_SDMX).text)
        yr = int(df["Year"].max())
        return _result(df, URL_OECD_SDMX, f"OECD Corporate Tax Statistics, {yr}", "Live")
    except Exception as e:  # noqa: BLE001
        errs.append(f"SDMX: {e}")
    # 2) HTML tables on the OECD publication page (if any are rendered server-side)
    try:
        tables = pd.read_html(io.StringIO(_get(URL_OECD_PAGE).text))
        name_to_iso = {k.lower(): v[0] for k, v in COUNTRIES.items()}
        rows = {}
        for t in tables:
            for _, r in t.iterrows():
                name = str(r.iloc[0]).strip().lower()
                if name in name_to_iso:
                    nums = [_to_float(x) for x in r.iloc[1:]]
                    nums = [n for n in nums if n is not None and 0 <= n <= 60]
                    if nums:
                        rows[name_to_iso[name]] = (str(r.iloc[0]).strip(), nums[-1] / 100, 2024)
        if rows:
            df = pd.DataFrame.from_dict(rows, orient="index", columns=["Country", "Rate", "Year"])
            return _result(df, URL_OECD_PAGE, "OECD Corporate Tax Statistics 2024", "Live")
        errs.append("page: no rate table found")
    except Exception as e:  # noqa: BLE001
        errs.append(f"page: {e}")
    iso_to_name = {v[0]: k for k, v in COUNTRIES.items()}
    df = pd.DataFrame(
        {"Country": [iso_to_name.get(k, k) for k in FALLBACK_TAX],
         "Rate": list(FALLBACK_TAX.values()), "Year": 2024},
        index=list(FALLBACK_TAX.keys()),
    )
    return _result(df, URL_OECD_PAGE, "offline snapshot (2024)", "Fallback", "; ".join(errs))


@st.cache_data(ttl=CACHE_TTL, show_spinner="Fetching 10Y yields (Trading Economics)...")
def fetch_yield_table() -> dict:
    try:
        data = parse_te_bonds(_get(URL_TE_BONDS).text)
        return _result(data, URL_TE_BONDS, "latest quote on page", "Live")
    except Exception as e:  # noqa: BLE001
        return _result({}, URL_TE_BONDS, "", "Failed", str(e))


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_country_yield(slug: str) -> dict:
    url = URL_TE_COUNTRY.format(slug=slug)
    try:
        y, date = parse_te_country(_get(url).text)
        return _result(y, url, date, "Live")
    except Exception as e:  # noqa: BLE001
        return _result(None, url, "", "Failed", str(e))


def get_yield(slug: str) -> dict:
    """10Y yield for one country: bonds table -> country page -> offline fallback."""
    table = fetch_yield_table()
    if slug in table["data"]:
        row = table["data"][slug]
        res = dict(table)
        res.update(data=row["yield"], as_of=row["date"] or table["as_of"])
        return res
    single = fetch_country_yield(slug)
    if single["data"] is not None:
        return single
    note = "; ".join(n for n in (table["note"], single["note"]) if n)
    return _result(FALLBACK_YIELDS.get(slug), URL_TE_BONDS, "offline snapshot", "Fallback", note)


# ----------------------------------------------------------------------------
# Finance
# ----------------------------------------------------------------------------

METHODS = {
    "parity": "Interest-rate parity (multiplicative): (1+k_FC) = (1+k_USD)·(1+rf_FC)/(1+rf_USD)",
    "additive": "Additive yield differential: k_FC = k_USD + (rf_FC − rf_USD)",
    "local": "Local CAPM: ke_FC = rf_FC + βL·ERP; kd_FC = rf_FC + spread_FC",
}


def compute(
    beta_u: float,
    tax: float,
    debt_share: float,
    erp: float,
    rf_us: float,
    rf_fc: float,
    spread_us: float,
    spread_fc_manual: float | None = None,
    method: str = "parity",
) -> dict:
    """All rates in decimals. Returns a dict with USD and foreign-currency results.

    beta_L   = beta_U · [1 + (1 − t) · D/E]                (Hamada)
    ke_USD   = rf_USD + beta_L · ERP                        (CAPM)
    kd_USD   = rf_USD + BBB spread_USD                      (pre-tax)
    WACC     = E/V · ke + D/V · kd · (1 − t)
    Foreign currency: see METHODS. The automatic foreign BBB spread is the
    one implied by the chosen conversion: spread_FC = kd_FC − rf_FC.
    """
    wd = debt_share
    we = 1.0 - wd
    de = wd / we
    beta_l = beta_u * (1.0 + (1.0 - tax) * de)

    ke_us = rf_us + beta_l * erp
    kd_us = rf_us + spread_us

    if method == "parity":
        conv = lambda k: (1.0 + k) * (1.0 + rf_fc) / (1.0 + rf_us) - 1.0  # noqa: E731
        ke_fc = conv(ke_us)
        kd_fc_auto = conv(kd_us)
    elif method == "additive":
        ke_fc = ke_us + (rf_fc - rf_us)
        kd_fc_auto = kd_us + (rf_fc - rf_us)
    elif method == "local":
        ke_fc = rf_fc + beta_l * erp
        kd_fc_auto = rf_fc + spread_us
    else:
        raise ValueError(method)

    spread_fc_auto = kd_fc_auto - rf_fc
    spread_fc = spread_fc_auto if spread_fc_manual is None else spread_fc_manual
    kd_fc = rf_fc + spread_fc

    wacc_us = we * ke_us + wd * kd_us * (1.0 - tax)
    wacc_fc = we * ke_fc + wd * kd_fc * (1.0 - tax)

    return {
        "E/V": we, "D/V": wd, "D/E": de, "tax": tax, "erp": erp,
        "beta_u": beta_u, "beta_l": beta_l,
        "rf_us": rf_us, "rf_fc": rf_fc,
        "spread_us": spread_us, "spread_fc": spread_fc, "spread_fc_auto": spread_fc_auto,
        "ke_us": ke_us, "ke_fc": ke_fc,
        "kd_us": kd_us, "kd_fc": kd_fc,
        "kd_us_at": kd_us * (1.0 - tax), "kd_fc_at": kd_fc * (1.0 - tax),
        "wacc_us": wacc_us, "wacc_fc": wacc_fc,
    }


# ----------------------------------------------------------------------------
# Output helpers
# ----------------------------------------------------------------------------

def to_excel_bytes(sheets: dict[str, pd.DataFrame]) -> bytes:
    """Plain workbook: Calibri, no fills, no bold headers, no borders."""
    from openpyxl.styles import Alignment, Border, Font

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for name, df in sheets.items():
            df.to_excel(xw, sheet_name=name[:31], index=False)
            ws = xw.sheets[name[:31]]
            for row in ws.iter_rows():
                for c in row:
                    c.font = Font(name="Calibri", size=11, bold=False)
                    c.border = Border()
                    c.alignment = Alignment()
            for col in ws.columns:
                width = max(len(str(c.value)) if c.value is not None else 0 for c in col)
                ws.column_dimensions[col[0].column_letter].width = min(max(10, width + 2), 60)
    return buf.getvalue()


def pct(x: float | None, d: int = 2) -> str:
    return "n/a" if x is None else f"{x * 100:.{d}f}%"


# ----------------------------------------------------------------------------
# App
# ----------------------------------------------------------------------------

def main() -> None:
    st.set_page_config(page_title="Cross-border WACC: USD vs local currency", layout="wide")
    st.title("Project Cost of Capital: USD vs. Local Currency")
    st.caption(
        "Cost of equity, cost of debt and WACC for a project in USD and in the currency of a "
        "selected country, using Damodaran industry betas and BBB spread, OECD corporate tax "
        "rates and Trading Economics 10-year government yields."
    )

    # ------------------------- Sidebar: data & inputs -------------------------
    with st.sidebar:
        st.header("Data")
        if st.button("🔄 Refresh data", width="stretch",
                     help="Clear the cache and re-download all sources."):
            st.cache_data.clear()
            st.rerun()

    betas_res = fetch_betas()
    spread_res = fetch_bbb_spread()
    tax_res = fetch_tax_rates()
    betas: pd.DataFrame = betas_res["data"]
    taxes: pd.DataFrame = tax_res["data"]

    industries = betas["Industry"].tolist()
    countries = list(COUNTRIES.keys())

    with st.sidebar:
        st.header("Project")
        industry = st.selectbox(
            "Industry", industries, key="industry",
            index=industries.index(DEFAULT_INDUSTRY) if DEFAULT_INDUSTRY in industries else 0,
        )
        country = st.selectbox("Target country", countries, index=countries.index(DEFAULT_COUNTRY),
                               key="country")
        iso, slug, ccy = COUNTRIES[country]

        erp = st.number_input("Equity risk premium (%)", 0.0, 20.0, DEFAULT_ERP, 0.1,
                              format="%.2f", key="erp") / 100
        debt_share = st.slider("Target debt share D/(D+E) (%)", 0.0, 90.0, DEFAULT_DEBT_SHARE, 1.0,
                               key="debt_share") / 100

        tax_basis = st.radio(
            "Project tax rate", ["us", "fc"], index=0, horizontal=True, key="tax_basis",
            format_func=lambda k: "U.S." if k == "us" else f"Selected country ({country})",
            help="Switch which country's corporate tax rate is used. The same rate is applied "
                 "to both the USD and the local-currency calculation.",
        )
        tax_choice = "U.S." if tax_basis == "us" else f"Selected country ({country})"
        method_key = st.selectbox(
            "Currency conversion method", list(METHODS.keys()), index=0, key="method",
            format_func=lambda k: {"parity": "Interest-rate parity (default)",
                                   "additive": "Additive yield differential",
                                   "local": "Local CAPM"}[k],
            help="Pick the formula used in class. Default: interest-rate parity.",
        )

    # Fetched values for the two countries
    yus_res = get_yield("united-states")
    yfc_res = get_yield(slug)
    beta_fetched = float(betas.loc[betas["Industry"] == industry, "Unlevered beta"].iloc[0])
    tax_us_fetched = float(taxes.loc["USA", "Rate"]) if "USA" in taxes.index else FALLBACK_TAX["USA"]
    tax_fc_avail = iso in taxes.index
    tax_fc_fetched = float(taxes.loc[iso, "Rate"]) if tax_fc_avail else FALLBACK_TAX.get(iso)
    rf_us_fetched = yus_res["data"]
    rf_fc_fetched = yfc_res["data"]

    # ------------------------- Sidebar: overrides -------------------------
    status = {}
    with st.sidebar:
        st.header("Manual overrides")
        st.caption("Tick a box to replace the fetched value with your own input.")

        def override(label, key, fetched, fmt_pct=True, lo=0.0, hi=100.0, step=0.01):
            on = st.checkbox(f"Override {label}", key=f"ov_{key}",
                             value=fetched is None)  # force manual if data missing
            shown = (fetched or 0.0) * (100 if fmt_pct else 1)
            val = st.number_input(
                f"{label}{' (%)' if fmt_pct else ''}", lo, hi, float(round(shown, 4)), step,
                # no key while disabled, so the box always mirrors the fetched value
                format="%.4f", key=f"in_{key}" if on else None, disabled=not on,
                label_visibility="collapsed",
            )
            status[key] = "Manual" if on else None
            if on:
                return val / 100 if fmt_pct else val
            return fetched

        beta_u = override("unlevered beta", "beta", beta_fetched, fmt_pct=False, lo=0.0, hi=5.0)
        tax_us = override("U.S. tax rate", "tax_us", tax_us_fetched)
        tax_fc = override(f"{country} tax rate", "tax_fc", tax_fc_fetched)
        spread_us = override("USD BBB spread", "spread_us", spread_res["data"], lo=0.0, hi=20.0)
        rf_us = override("U.S. 10Y yield", "rf_us", rf_us_fetched, lo=-5.0, hi=100.0)
        rf_fc = override(f"{country} 10Y yield", "rf_fc", rf_fc_fetched, lo=-5.0, hi=100.0)

    tax = tax_us if tax_choice == "U.S." else tax_fc

    # Auto foreign spread, then optional override (shows auto value when off)
    auto = compute(beta_u, tax, debt_share, erp, rf_us, rf_fc, spread_us, None, method_key)
    with st.sidebar:
        fc_spread_on = st.toggle(f"Override {ccy} BBB spread", value=False, key="ov_spread_fc")
        fc_spread_in = st.number_input(
            f"{ccy} BBB spread (%)", -5.0, 30.0, float(round(auto["spread_fc_auto"] * 100, 4)), 0.01,
            format="%.4f", disabled=not fc_spread_on, key="in_spread_fc" if fc_spread_on else None,
            help="Off = automatic: spread implied by the conversion method (kd_FC − rf_FC).",
        )
        if not fc_spread_on:
            st.caption(f"Auto-calculated: {pct(auto['spread_fc_auto'], 3)}")
    spread_fc_manual = fc_spread_in / 100 if fc_spread_on else None

    r = compute(beta_u, tax, debt_share, erp, rf_us, rf_fc, spread_us, spread_fc_manual, method_key)

    # ------------------------- Data sources panel -------------------------
    def src_row(item, value, res, key):
        st_ = status.get(key) or res["status"]
        return {
            "Input": item, "Value used": value, "Status": st_,
            "Data date": res["as_of"], "Fetched": res["fetched"], "Source": res["source"],
        }

    tax_src_fc = dict(tax_res)
    if not tax_fc_avail:
        tax_src_fc.update(status="Fallback", as_of="not in OECD data")
    sources = pd.DataFrame([
        src_row(f"Unlevered beta – {industry}", f"{beta_u:.4f}", betas_res, "beta"),
        src_row("U.S. corporate tax rate", pct(tax_us), tax_res, "tax_us"),
        src_row(f"{country} corporate tax rate", pct(tax_fc), tax_src_fc, "tax_fc"),
        src_row("USD BBB (Baa2) spread", pct(spread_us), spread_res, "spread_us"),
        src_row("U.S. 10Y government yield", pct(rf_us, 3), yus_res, "rf_us"),
        src_row(f"{country} 10Y government yield", pct(rf_fc, 3), yfc_res, "rf_fc"),
    ])

    fallbacks = sources[sources["Status"].isin(["Fallback", "Failed"])]
    if not fallbacks.empty:
        st.warning(
            "Some sources could not be reached, so offline snapshot values are in use for: "
            + ", ".join(fallbacks["Input"])
            + ". Press **Refresh data** or enter the values under **Manual overrides**."
        )

    # ------------------------- Results -------------------------
    fc_label = f"{ccy} ({country})"
    st.subheader(f"Results — {industry}, {country}")
    st.caption(
        f"Project tax rate: **{pct(tax)}** ({tax_choice}) · ERP **{pct(erp)}** · "
        f"Debt share **{pct(debt_share, 0)}** · Method: {METHODS[method_key]}"
    )

    c1, c2 = st.columns(2)
    for col, lab, ke, kd, kd_at, w in (
        (c1, "USD", r["ke_us"], r["kd_us"], r["kd_us_at"], r["wacc_us"]),
        (c2, fc_label, r["ke_fc"], r["kd_fc"], r["kd_fc_at"], r["wacc_fc"]),
    ):
        with col:
            with st.container(border=True):
                st.markdown(f"#### {lab}")
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Cost of equity", pct(ke))
                m2.metric("Pre-tax cost of debt", pct(kd))
                m3.metric("After-tax cost of debt", pct(kd_at))
                m4.metric("WACC", pct(w))

    detail = pd.DataFrame(
        [
            ("10Y government yield (rf)", pct(r["rf_us"], 3), pct(r["rf_fc"], 3)),
            ("Unlevered beta (βU)", f"{r['beta_u']:.4f}", f"{r['beta_u']:.4f}"),
            ("Debt / Equity (D/E)", f"{r['D/E']:.4f}", f"{r['D/E']:.4f}"),
            ("Project tax rate (t)", pct(r["tax"]), pct(r["tax"])),
            ("Relevered beta (βL)", f"{r['beta_l']:.4f}", f"{r['beta_l']:.4f}"),
            ("Equity risk premium", pct(r["erp"]), pct(r["erp"])),
            ("Cost of equity (ke)", pct(r["ke_us"], 3), pct(r["ke_fc"], 3)),
            ("BBB spread", pct(r["spread_us"], 3),
             pct(r["spread_fc"], 3) + (" (manual)" if fc_spread_on else " (auto)")),
            ("Pre-tax cost of debt (kd)", pct(r["kd_us"], 3), pct(r["kd_fc"], 3)),
            ("After-tax cost of debt kd·(1−t)", pct(r["kd_us_at"], 3), pct(r["kd_fc_at"], 3)),
            ("Equity weight E/V", pct(r["E/V"], 1), pct(r["E/V"], 1)),
            ("Debt weight D/V", pct(r["D/V"], 1), pct(r["D/V"], 1)),
            ("WACC", pct(r["wacc_us"], 3), pct(r["wacc_fc"], 3)),
        ],
        columns=["Component", "USD", fc_label],
    )
    st.dataframe(detail, hide_index=True, width="stretch")

    with st.expander("Data sources and dates", expanded=True):
        st.dataframe(
            sources, hide_index=True, width="stretch",
            column_config={"Source": st.column_config.LinkColumn("Source")},
        )
        notes = [f"- **{n}**: {res['note']}" for n, res in
                 [("Betas", betas_res), ("BBB spread", spread_res), ("Tax rates", tax_res),
                  ("U.S. yield", yus_res), (f"{country} yield", yfc_res)] if res.get("note")]
        if notes:
            st.caption("Fetch messages:\n" + "\n".join(notes))
        st.caption(
            "OECD page referenced: " + URL_OECD_PAGE + " — the app reads the same Corporate Tax "
            "Statistics data from the OECD SDMX API (combined central + sub-central statutory rate)."
        )

    # ------------------------- Assumptions -------------------------
    with st.expander("Main assumptions and formulas", expanded=False):
        st.markdown(
            r"""
- **Capital structure**: target debt share $w_D = D/V$, so $E/V = 1-w_D$ and $D/E = w_D/(1-w_D)$.
- **Relevered beta (Hamada)**: $\beta_L = \beta_U\,[1 + (1-t)\,D/E]$, using Damodaran's U.S. industry
  *unlevered beta* (not cash-corrected).
- **USD cost of equity (CAPM)**: $k_e^{USD} = r_f^{US} + \beta_L \times ERP$, with $r_f^{US}$ = U.S. 10-year Treasury yield.
- **USD cost of debt**: $k_d^{USD} = r_f^{US} + s_{BBB}$, where $s_{BBB}$ is Damodaran's Baa2/BBB default spread
  for large non-financial firms. After tax: $k_d(1-t)$.
- **Foreign-currency rates** (default, interest-rate parity / international Fisher effect):
  $(1+k^{FC}) = (1+k^{USD})\,\dfrac{1+r_f^{FC}}{1+r_f^{US}}$, applied to $k_e$ and $k_d$. The 10-year
  government yield gap is taken as the expected inflation / currency differential.
- **Automatic foreign BBB spread**: $s^{FC} = k_d^{FC} - r_f^{FC}$ (the spread implied by the conversion);
  it can be overwritten manually, in which case $k_d^{FC} = r_f^{FC} + s^{FC}$.
- **WACC**: $WACC = \frac{E}{V}k_e + \frac{D}{V}k_d(1-t)$, in each currency, with the **same** project tax
  rate, debt share and ERP in both currencies.
- Government yields are treated as risk-free in their own currency (no separate sovereign default
  spread is removed). The same ERP is applied in both currencies.
- Alternative methods are available in the sidebar (additive differential, local CAPM) in case the
  class formulas differ. With the United States selected, both columns are identical by construction.
"""
        )

    # ------------------------- All-industry comparison -------------------------
    st.subheader("All-industry comparison")
    rows = []
    for _, b in betas.iterrows():
        bu = beta_u if (b["Industry"] == industry and status.get("beta")) else float(b["Unlevered beta"])
        x = compute(bu, tax, debt_share, erp, rf_us, rf_fc, spread_us, spread_fc_manual, method_key)
        rows.append({
            "Industry": b["Industry"],
            "Unlevered beta": round(bu, 4),
            "Relevered beta": round(x["beta_l"], 4),
            "Cost of equity USD (%)": round(x["ke_us"] * 100, 3),
            "WACC USD (%)": round(x["wacc_us"] * 100, 3),
            f"Cost of equity {ccy} (%)": round(x["ke_fc"] * 100, 3),
            f"WACC {ccy} (%)": round(x["wacc_fc"] * 100, 3),
        })
    all_ind = pd.DataFrame(rows).sort_values(f"WACC {ccy} (%)", ascending=False).reset_index(drop=True)

    sort_by = st.radio("Chart", [f"WACC {ccy} (%)", "WACC USD (%)",
                                 f"Cost of equity {ccy} (%)", "Cost of equity USD (%)"],
                       horizontal=True, label_visibility="collapsed", key="chart_metric")
    chart_df = all_ind.assign(Selected=all_ind["Industry"].eq(industry))
    chart = (
        alt.Chart(chart_df)
        .mark_bar()
        .encode(
            x=alt.X(f"{sort_by}:Q", title=sort_by),
            y=alt.Y("Industry:N", sort="-x", title=None),
            color=alt.condition(alt.datum.Selected, alt.value("#d62728"), alt.value("#4c78a8")),
            tooltip=list(all_ind.columns),
        )
        .properties(height=max(300, 14 * len(chart_df)))
    )
    st.altair_chart(chart, width="stretch")
    st.dataframe(all_ind, hide_index=True, width="stretch", height=400)

    # ------------------------- Downloads -------------------------
    st.subheader("Download results")
    inputs = pd.DataFrame(
        [
            ("Industry", industry), ("Target country", country), ("Currency", ccy),
            ("Project tax rate basis", tax_choice), ("Project tax rate", r["tax"]),
            ("Equity risk premium", r["erp"]), ("Debt share D/V", r["D/V"]),
            ("Conversion method", METHODS[method_key]),
            ("Unlevered beta", r["beta_u"]), ("U.S. tax rate", tax_us), (f"{country} tax rate", tax_fc),
            ("USD BBB spread", r["spread_us"]),
            (f"{ccy} BBB spread", r["spread_fc"]),
            (f"{ccy} BBB spread mode", "manual" if fc_spread_on else "auto"),
            ("U.S. 10Y yield", r["rf_us"]), (f"{country} 10Y yield", r["rf_fc"]),
            ("Generated", _now()),
        ],
        columns=["Input", "Value"],
    )
    results_num = pd.DataFrame(
        {
            "Component": ["Risk-free rate", "Relevered beta", "Cost of equity", "BBB spread",
                          "Pre-tax cost of debt", "After-tax cost of debt", "E/V", "D/V", "WACC"],
            "USD": [r["rf_us"], r["beta_l"], r["ke_us"], r["spread_us"], r["kd_us"],
                    r["kd_us_at"], r["E/V"], r["D/V"], r["wacc_us"]],
            ccy if ccy != "USD" else "USD (selected country)":
                [r["rf_fc"], r["beta_l"], r["ke_fc"], r["spread_fc"], r["kd_fc"],
                 r["kd_fc_at"], r["E/V"], r["D/V"], r["wacc_fc"]],
        }
    )
    stamp = dt.date.today().isoformat()
    d1, d2, d3 = st.columns(3)
    d1.download_button("Results (CSV)", results_num.to_csv(index=False).encode(),
                       f"wacc_results_{ccy}_{stamp}.csv", "text/csv", width="stretch")
    d2.download_button("All industries (CSV)", all_ind.to_csv(index=False).encode(),
                       f"wacc_all_industries_{ccy}_{stamp}.csv", "text/csv", width="stretch")
    d3.download_button(
        "Full workbook (Excel)",
        to_excel_bytes({"Inputs": inputs, "Results": results_num,
                        "All industries": all_ind, "Sources": sources}),
        f"wacc_{ccy}_{stamp}.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        width="stretch",
    )


if __name__ == "__main__":
    main()
