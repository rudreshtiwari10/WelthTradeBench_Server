"""Zerodha Kite Connect integration (optional second broker).

Normalised to the same dict/JSON shapes as the Upstox broker so the frontend's
BrokerFunds / BrokerPosition / BrokerOrder types work unchanged regardless of
which broker is active. Dormant unless KITE_API_KEY / KITE_API_SECRET are set.
"""
