"""Symbol <-> Upstox instrument-key mapping.

A curated set covering the indices/stocks shown on the chart page plus common
NSE names. Upstox instrument keys look like "NSE_INDEX|Nifty 50" or
"NSE_EQ|INE002A01018". Phase 8 can swap this for the full downloadable
instrument master; this curated map keeps search/quote working today.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Instrument:
    symbol: str
    name: str
    exchange: str
    instrument_key: str
    kind: str  # index | stock | future


INSTRUMENTS: list[Instrument] = [
    # Indices
    Instrument("NIFTY", "Nifty 50 Index", "NSE", "NSE_INDEX|Nifty 50", "index"),
    Instrument("BANKNIFTY", "Nifty Bank Index", "NSE", "NSE_INDEX|Nifty Bank", "index"),
    Instrument("FINNIFTY", "Nifty Fin Service", "NSE", "NSE_INDEX|Nifty Fin Service", "index"),
    Instrument("MIDCPNIFTY", "Nifty Midcap Select", "NSE", "NSE_INDEX|NIFTY MID SELECT", "index"),
    Instrument("SENSEX", "BSE Sensex", "BSE", "BSE_INDEX|SENSEX", "index"),
    Instrument("BANKEX", "BSE Bankex", "BSE", "BSE_INDEX|BANKEX", "index"),
    # Equities (NSE)
    Instrument("RELIANCE", "Reliance Industries", "NSE", "NSE_EQ|INE002A01018", "stock"),
    Instrument("TCS", "Tata Consultancy Svcs", "NSE", "NSE_EQ|INE467B01029", "stock"),
    Instrument("HDFCBANK", "HDFC Bank", "NSE", "NSE_EQ|INE040A01034", "stock"),
    Instrument("INFY", "Infosys", "NSE", "NSE_EQ|INE009A01021", "stock"),
    Instrument("ICICIBANK", "ICICI Bank", "NSE", "NSE_EQ|INE090A01021", "stock"),
    Instrument("SBIN", "State Bank of India", "NSE", "NSE_EQ|INE062A01020", "stock"),
    Instrument("TITAN", "Titan Company", "NSE", "NSE_EQ|INE280A01028", "stock"),
    Instrument("APOLLOHOSP", "Apollo Hospitals", "NSE", "NSE_EQ|INE437A01024", "stock"),
    Instrument("NESTLEIND", "Nestle India", "NSE", "NSE_EQ|INE239A01024", "stock"),
    Instrument("TATAMOTORS", "Tata Motors", "NSE", "NSE_EQ|INE155A01022", "stock"),
    Instrument("WIPRO", "Wipro", "NSE", "NSE_EQ|INE075A01022", "stock"),
    Instrument("ITC", "ITC", "NSE", "NSE_EQ|INE154A01025", "stock"),
]

_BY_SYMBOL = {i.symbol.upper(): i for i in INSTRUMENTS}
_BY_KEY = {i.instrument_key: i for i in INSTRUMENTS}


def by_symbol(symbol: str) -> Instrument | None:
    return _BY_SYMBOL.get(symbol.upper())


def by_key(key: str) -> Instrument | None:
    return _BY_KEY.get(key)


def search(query: str, limit: int = 30) -> list[Instrument]:
    q = query.strip().upper()
    if not q:
        return INSTRUMENTS[:limit]
    starts = [i for i in INSTRUMENTS if i.symbol.upper().startswith(q)]
    contains = [
        i for i in INSTRUMENTS
        if i not in starts and (q in i.symbol.upper() or q in i.name.upper())
    ]
    return (starts + contains)[:limit]
