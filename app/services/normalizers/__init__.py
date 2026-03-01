"""Normalizers package — convert raw API responses into canonical types.

All functions are pure (no async, no I/O).  Callers handle empty/missing
data by filtering out None values in list comprehensions or by checking for
None/empty collections in the returned structures.

Import style:
    from app.services.normalizers import arrivals, positions, route_stops, schedule, stops
"""
from app.services.normalizers import arrivals, positions, route_stops, schedule, stops

__all__ = ["arrivals", "positions", "route_stops", "schedule", "stops"]
