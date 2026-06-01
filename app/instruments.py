"""Symbol <-> Upstox instrument-key mapping.

Covers all major NSE/BSE indices and the top ~80 F&O stocks so search returns
useful suggestions even in mock mode.  Upstox instrument keys follow the pattern:
  NSE_INDEX|<name>   for indices
  NSE_EQ|<isin>      for NSE equities
  BSE_EQ|<isin>      for BSE-primary equities
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Instrument:
    symbol: str
    name: str
    exchange: str
    instrument_key: str
    kind: str  # index | stock | future | option


INSTRUMENTS: list[Instrument] = [
    # ── NSE Indices ────────────────────────────────────────────────────
    Instrument("NIFTY",       "Nifty 50 Index",           "NSE", "NSE_INDEX|Nifty 50",            "index"),
    Instrument("BANKNIFTY",   "Nifty Bank Index",          "NSE", "NSE_INDEX|Nifty Bank",          "index"),
    Instrument("FINNIFTY",    "Nifty Fin Service",         "NSE", "NSE_INDEX|Nifty Fin Service",   "index"),
    Instrument("MIDCPNIFTY",  "Nifty Midcap Select",       "NSE", "NSE_INDEX|NIFTY MID SELECT",    "index"),
    Instrument("NIFTYNXT50",  "Nifty Next 50",             "NSE", "NSE_INDEX|Nifty Next 50",       "index"),
    Instrument("NIFTY100",    "Nifty 100",                 "NSE", "NSE_INDEX|Nifty 100",           "index"),
    Instrument("NIFTY200",    "Nifty 200",                 "NSE", "NSE_INDEX|Nifty 200",           "index"),
    Instrument("NIFTY500",    "Nifty 500",                 "NSE", "NSE_INDEX|Nifty 500",           "index"),
    Instrument("NIFTYMIDCAP150", "Nifty Midcap 150",       "NSE", "NSE_INDEX|NIFTY MIDCAP 150",   "index"),
    Instrument("NIFTYSMALLCAP250","Nifty Smallcap 250",    "NSE", "NSE_INDEX|NIFTY SMALLCAP 250", "index"),
    Instrument("NIFTYIT",     "Nifty IT",                  "NSE", "NSE_INDEX|Nifty IT",            "index"),
    Instrument("NIFTYPHARMA", "Nifty Pharma",              "NSE", "NSE_INDEX|Nifty Pharma",        "index"),
    Instrument("NIFTYAUTO",   "Nifty Auto",                "NSE", "NSE_INDEX|Nifty Auto",          "index"),
    Instrument("NIFTYFMCG",   "Nifty FMCG",               "NSE", "NSE_INDEX|Nifty FMCG",          "index"),
    Instrument("NIFTYMETAL",  "Nifty Metal",               "NSE", "NSE_INDEX|Nifty Metal",         "index"),
    Instrument("NIFTYPSUBANK","Nifty PSU Bank",            "NSE", "NSE_INDEX|Nifty PSU Bank",      "index"),
    Instrument("NIFTYREALTY", "Nifty Realty",              "NSE", "NSE_INDEX|Nifty Realty",        "index"),
    Instrument("NIFTYMEDIA",  "Nifty Media",               "NSE", "NSE_INDEX|Nifty Media",         "index"),
    Instrument("NIFTYENERGY", "Nifty Energy",              "NSE", "NSE_INDEX|Nifty Energy",        "index"),
    Instrument("NIFTYINFRA",  "Nifty Infrastructure",      "NSE", "NSE_INDEX|Nifty Infrastructure","index"),
    Instrument("VIXNSE",      "India VIX",                 "NSE", "NSE_INDEX|India VIX",           "index"),

    # ── BSE Indices ────────────────────────────────────────────────────
    Instrument("SENSEX",   "BSE Sensex",                   "BSE", "BSE_INDEX|SENSEX",              "index"),
    Instrument("BANKEX",   "BSE Bankex",                   "BSE", "BSE_INDEX|BANKEX",              "index"),
    Instrument("BSE500",   "BSE 500",                      "BSE", "BSE_INDEX|BSE500",              "index"),
    Instrument("BSE200",   "BSE 200",                      "BSE", "BSE_INDEX|BSE200",              "index"),
    Instrument("BSEMIDCAP","BSE Midcap",                   "BSE", "BSE_INDEX|BSE-MidCap",          "index"),
    Instrument("BSESMCAP", "BSE Smallcap",                 "BSE", "BSE_INDEX|BSE-SmallCap",        "index"),

    # ── Nifty 50 constituents (NSE_EQ) ────────────────────────────────
    Instrument("RELIANCE",    "Reliance Industries",        "NSE", "NSE_EQ|INE002A01018", "stock"),
    Instrument("TCS",         "Tata Consultancy Svcs",      "NSE", "NSE_EQ|INE467B01029", "stock"),
    Instrument("HDFCBANK",    "HDFC Bank",                  "NSE", "NSE_EQ|INE040A01034", "stock"),
    Instrument("INFY",        "Infosys",                    "NSE", "NSE_EQ|INE009A01021", "stock"),
    Instrument("ICICIBANK",   "ICICI Bank",                 "NSE", "NSE_EQ|INE090A01021", "stock"),
    Instrument("HINDUNILVR",  "Hindustan Unilever",         "NSE", "NSE_EQ|INE030A01027", "stock"),
    Instrument("KOTAKBANK",   "Kotak Mahindra Bank",        "NSE", "NSE_EQ|INE237A01028", "stock"),
    Instrument("SBIN",        "State Bank of India",        "NSE", "NSE_EQ|INE062A01020", "stock"),
    Instrument("LT",          "Larsen & Toubro",            "NSE", "NSE_EQ|INE018A01030", "stock"),
    Instrument("BHARTIARTL",  "Bharti Airtel",              "NSE", "NSE_EQ|INE397D01024", "stock"),
    Instrument("ITC",         "ITC",                        "NSE", "NSE_EQ|INE154A01025", "stock"),
    Instrument("ASIANPAINT",  "Asian Paints",               "NSE", "NSE_EQ|INE021A01026", "stock"),
    Instrument("AXISBANK",    "Axis Bank",                  "NSE", "NSE_EQ|INE238A01034", "stock"),
    Instrument("MARUTI",      "Maruti Suzuki India",        "NSE", "NSE_EQ|INE585B01010", "stock"),
    Instrument("SUNPHARMA",   "Sun Pharmaceutical",         "NSE", "NSE_EQ|INE044A01036", "stock"),
    Instrument("TITAN",       "Titan Company",              "NSE", "NSE_EQ|INE280A01028", "stock"),
    Instrument("ULTRACEMCO",  "UltraTech Cement",           "NSE", "NSE_EQ|INE481G01011", "stock"),
    Instrument("BAJFINANCE",  "Bajaj Finance",              "NSE", "NSE_EQ|INE296A01024", "stock"),
    Instrument("BAJAJFINSV",  "Bajaj Finserv",              "NSE", "NSE_EQ|INE918I01026", "stock"),
    Instrument("WIPRO",       "Wipro",                      "NSE", "NSE_EQ|INE075A01022", "stock"),
    Instrument("NESTLEIND",   "Nestle India",               "NSE", "NSE_EQ|INE239A01024", "stock"),
    Instrument("HCLTECH",     "HCL Technologies",           "NSE", "NSE_EQ|INE860A01027", "stock"),
    Instrument("TECHM",       "Tech Mahindra",              "NSE", "NSE_EQ|INE669C01036", "stock"),
    Instrument("TATAMOTORS",  "Tata Motors",                "NSE", "NSE_EQ|INE155A01022", "stock"),
    Instrument("TATASTEEL",   "Tata Steel",                 "NSE", "NSE_EQ|INE081A01020", "stock"),
    Instrument("POWERGRID",   "Power Grid Corp",            "NSE", "NSE_EQ|INE752E01010", "stock"),
    Instrument("NTPC",        "NTPC",                       "NSE", "NSE_EQ|INE733E01010", "stock"),
    Instrument("ONGC",        "Oil & Natural Gas Corp",     "NSE", "NSE_EQ|INE213A01029", "stock"),
    Instrument("COALINDIA",   "Coal India",                 "NSE", "NSE_EQ|INE522F01014", "stock"),
    Instrument("ADANIPORTS",  "Adani Ports & SEZ",          "NSE", "NSE_EQ|INE742F01042", "stock"),
    Instrument("ADANIENT",    "Adani Enterprises",          "NSE", "NSE_EQ|INE423A01024", "stock"),
    Instrument("JSWSTEEL",    "JSW Steel",                  "NSE", "NSE_EQ|INE019A01038", "stock"),
    Instrument("INDUSINDBK",  "IndusInd Bank",              "NSE", "NSE_EQ|INE095A01012", "stock"),
    Instrument("DRREDDY",     "Dr Reddy's Laboratories",    "NSE", "NSE_EQ|INE089A01023", "stock"),
    Instrument("CIPLA",       "Cipla",                      "NSE", "NSE_EQ|INE059A01026", "stock"),
    Instrument("EICHERMOT",   "Eicher Motors",              "NSE", "NSE_EQ|INE066A01021", "stock"),
    Instrument("HEROMOTOCO",  "Hero MotoCorp",              "NSE", "NSE_EQ|INE158A01026", "stock"),
    Instrument("BPCL",        "BPCL",                       "NSE", "NSE_EQ|INE029A01011", "stock"),
    Instrument("APOLLOHOSP",  "Apollo Hospitals",           "NSE", "NSE_EQ|INE437A01024", "stock"),
    Instrument("SBILIFE",     "SBI Life Insurance",         "NSE", "NSE_EQ|INE123W01016", "stock"),
    Instrument("HDFCLIFE",    "HDFC Life Insurance",        "NSE", "NSE_EQ|INE795G01014", "stock"),
    Instrument("BRITANNIA",   "Britannia Industries",       "NSE", "NSE_EQ|INE216A01030", "stock"),
    Instrument("DIVISLAB",    "Divi's Laboratories",        "NSE", "NSE_EQ|INE361B01024", "stock"),
    Instrument("GRASIM",      "Grasim Industries",          "NSE", "NSE_EQ|INE047A01021", "stock"),
    Instrument("SHREECEM",    "Shree Cement",               "NSE", "NSE_EQ|INE070A01015", "stock"),
    Instrument("TATACONSUM",  "Tata Consumer Products",     "NSE", "NSE_EQ|INE192A01025", "stock"),
    Instrument("BAJAJ-AUTO",  "Bajaj Auto",                 "NSE", "NSE_EQ|INE917I01010", "stock"),

    # ── Popular midcap F&O / heavily traded ──────────────────────────
    Instrument("PIDILITIND",  "Pidilite Industries",        "NSE", "NSE_EQ|INE318A01026", "stock"),
    Instrument("HAVELLS",     "Havells India",              "NSE", "NSE_EQ|INE176B01034", "stock"),
    Instrument("VOLTAS",      "Voltas",                     "NSE", "NSE_EQ|INE226A01021", "stock"),
    Instrument("MUTHOOTFIN",  "Muthoot Finance",            "NSE", "NSE_EQ|INE414G01012", "stock"),
    Instrument("CHOLAFIN",    "Cholamandalam Investment",   "NSE", "NSE_EQ|INE121A01024", "stock"),
    Instrument("PAGEIND",     "Page Industries",            "NSE", "NSE_EQ|INE761H01022", "stock"),
    Instrument("BERGEPAINT",  "Berger Paints India",        "NSE", "NSE_EQ|INE463A01038", "stock"),
    Instrument("TORNTPHARM",  "Torrent Pharmaceuticals",    "NSE", "NSE_EQ|INE685A01028", "stock"),
    Instrument("LUPIN",       "Lupin",                      "NSE", "NSE_EQ|INE326A01037", "stock"),
    Instrument("AUROPHARMA",  "Aurobindo Pharma",           "NSE", "NSE_EQ|INE406A01037", "stock"),
    Instrument("ABBOTINDIA",  "Abbott India",               "NSE", "NSE_EQ|INE358A01014", "stock"),
    Instrument("IDFCFIRSTB",  "IDFC First Bank",            "NSE", "NSE_EQ|INE092T01019", "stock"),
    Instrument("FEDERALBNK",  "Federal Bank",               "NSE", "NSE_EQ|INE171A01029", "stock"),
    Instrument("BANDHANBNK",  "Bandhan Bank",               "NSE", "NSE_EQ|INE545U01014", "stock"),
    Instrument("CANBK",       "Canara Bank",                "NSE", "NSE_EQ|INE476A01022", "stock"),
    Instrument("PNB",         "Punjab National Bank",       "NSE", "NSE_EQ|INE160A01022", "stock"),
    Instrument("BANKBARODA",  "Bank of Baroda",             "NSE", "NSE_EQ|INE028A01039", "stock"),
    Instrument("GAIL",        "GAIL India",                 "NSE", "NSE_EQ|INE129A01019", "stock"),
    Instrument("IOC",         "Indian Oil Corp",            "NSE", "NSE_EQ|INE242A01010", "stock"),
    Instrument("HAL",         "Hindustan Aeronautics",      "NSE", "NSE_EQ|INE066F01020", "stock"),
    Instrument("BEL",         "Bharat Electronics",         "NSE", "NSE_EQ|INE263A01024", "stock"),
    Instrument("IRCTC",       "IRCTC",                      "NSE", "NSE_EQ|INE335Y01012", "stock"),
    Instrument("DMART",       "Avenue Supermarts",          "NSE", "NSE_EQ|INE192R01011", "stock"),
    Instrument("ZOMATO",      "Zomato",                     "NSE", "NSE_EQ|INE758T01015", "stock"),
    Instrument("PAYTM",       "Paytm (One97 Comm)",         "NSE", "NSE_EQ|INE982J01020", "stock"),
    Instrument("NYKAA",       "Nykaa (FSN E-Commerce)",     "NSE", "NSE_EQ|INE388Y01029", "stock"),
    Instrument("POLICYBZR",   "PolicyBazaar (PB Fintech)",  "NSE", "NSE_EQ|INE417T01026", "stock"),
    Instrument("TATAPOWER",   "Tata Power Company",         "NSE", "NSE_EQ|INE245A01021", "stock"),
    Instrument("TORNTPOWER",  "Torrent Power",              "NSE", "NSE_EQ|INE813H01021", "stock"),
    Instrument("CUMMINSIND",  "Cummins India",              "NSE", "NSE_EQ|INE298A01020", "stock"),
    Instrument("ABB",         "ABB India",                  "NSE", "NSE_EQ|INE117A01022", "stock"),
    Instrument("SIEMENS",     "Siemens India",              "NSE", "NSE_EQ|INE003A01024", "stock"),
    Instrument("CONCOR",      "Container Corp of India",    "NSE", "NSE_EQ|INE111A01025", "stock"),
    Instrument("AMBUJACEM",   "Ambuja Cements",             "NSE", "NSE_EQ|INE079A01024", "stock"),
    Instrument("ACECEM",      "ACC",                        "NSE", "NSE_EQ|INE012A01025", "stock"),
    Instrument("OBEROIRLTY",  "Oberoi Realty",              "NSE", "NSE_EQ|INE093I01010", "stock"),
    Instrument("DLF",         "DLF",                        "NSE", "NSE_EQ|INE271C01023", "stock"),
    Instrument("GODREJPROP",  "Godrej Properties",          "NSE", "NSE_EQ|INE484J01027", "stock"),
    Instrument("MCDOWELL-N",  "United Spirits",             "NSE", "NSE_EQ|INE854D01024", "stock"),
    Instrument("UBL",         "United Breweries",           "NSE", "NSE_EQ|INE686F01025", "stock"),
    Instrument("JUBLFOOD",    "Jubilant FoodWorks",         "NSE", "NSE_EQ|INE797F01020", "stock"),
    Instrument("TRENT",       "Trent",                      "NSE", "NSE_EQ|INE849A01020", "stock"),
    Instrument("VEDL",        "Vedanta",                    "NSE", "NSE_EQ|INE205A01025", "stock"),
    Instrument("HINDALCO",    "Hindalco Industries",        "NSE", "NSE_EQ|INE038A01020", "stock"),
    Instrument("NMDC",        "NMDC",                       "NSE", "NSE_EQ|INE584A01023", "stock"),
    Instrument("SAIL",        "Steel Authority of India",   "NSE", "NSE_EQ|INE114A01011", "stock"),
    Instrument("RECLTD",      "REC Limited",                "NSE", "NSE_EQ|INE020B01018", "stock"),
    Instrument("PFC",         "Power Finance Corp",         "NSE", "NSE_EQ|INE134E01011", "stock"),
    Instrument("IRFC",        "Indian Railway Fin Corp",    "NSE", "NSE_EQ|INE053F01010", "stock"),
    Instrument("SUZLON",      "Suzlon Energy",              "NSE", "NSE_EQ|INE040H01021", "stock"),
    Instrument("ADANIGREEN",  "Adani Green Energy",         "NSE", "NSE_EQ|INE364U01010", "stock"),
    Instrument("ADANIPOWER",  "Adani Power",                "NSE", "NSE_EQ|INE814H01011", "stock"),
    Instrument("IDEA",        "Vodafone Idea",              "NSE", "NSE_EQ|INE669E01016", "stock"),
]

_BY_SYMBOL = {i.symbol.upper(): i for i in INSTRUMENTS}
_BY_KEY    = {i.instrument_key: i for i in INSTRUMENTS}


def by_symbol(symbol: str) -> Instrument | None:
    return _BY_SYMBOL.get(symbol.upper())


def by_key(key: str) -> Instrument | None:
    return _BY_KEY.get(key)


def search(query: str, limit: int = 40) -> list[Instrument]:
    """Return up to `limit` instruments matching `query`.

    Priority order:
    1. Exact symbol match
    2. Symbol starts-with
    3. Symbol contains
    4. Name contains (case-insensitive)
    """
    q = query.strip().upper()
    if not q:
        # Default listing: show all indices first, then top equities
        indices = [i for i in INSTRUMENTS if i.kind == "index"]
        stocks  = [i for i in INSTRUMENTS if i.kind == "stock"]
        return (indices + stocks)[:limit]

    exact    = [i for i in INSTRUMENTS if i.symbol.upper() == q]
    starts   = [i for i in INSTRUMENTS if i.symbol.upper().startswith(q) and i not in exact]
    sym_cont = [i for i in INSTRUMENTS if q in i.symbol.upper() and i not in exact and i not in starts]
    nam_cont = [i for i in INSTRUMENTS if q in i.name.upper()
                and i not in exact and i not in starts and i not in sym_cont]

    return (exact + starts + sym_cont + nam_cont)[:limit]
