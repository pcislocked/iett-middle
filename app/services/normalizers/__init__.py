"""Normalizers package — convert raw API responses into canonical types.

All functions are pure (no async, no I/O).  Callers handle empty/missing
data by catching ValueError or by filtering out None from list comprehensions.

Import style:
    from app.services.normalizers import arrivals, positions, route_stops, schedule, stops
"""
from app.services.normalizers import arrivals, positions, route_stops, schedule, stops

__all__ = ["arrivals", "positions", "route_stops", "schedule", "stops"]
