"""Earth comms delay from distance, using the speed of light.

This is the physical minimum delay. Real links add relay hops (e.g. via TDRS
or Mars orbiters), ground processing and the surgeon's own response time, so
the actual wait is always longer than this.
"""
from __future__ import annotations

SPEED_OF_LIGHT_KM_S = 299_792.458

# Default Earth distances per environment, in km. These are project choices
# for the demo; POST /api/environment can override them with earth_distance_km.
DEFAULT_DISTANCE_KM = {
    "ISS_LEO": 400.0,              # ISS orbital altitude (direct line, ignores TDRS relay)
    "LUNAR_SURFACE": 384_400.0,    # mean Earth-Moon distance
    "MARS_TRANSIT": 225_000_000.0, # mean Earth-Mars distance
    "MARS_HABITAT": 225_000_000.0, # mean Earth-Mars distance
}

# Earth-Mars ranges from ~54.6 million km to ~401 million km. Allow a margin
# beyond that for other deep-space scenarios.
MAX_DISTANCE_KM = 1_000_000_000.0


def one_way_delay_s(distance_km: float) -> float:
    """Light travel time from Earth to the crew, in seconds (0.1 s precision)."""
    return round(distance_km / SPEED_OF_LIGHT_KM_S, 1)


def comms_fields(distance_km: float) -> dict[str, float]:
    """Fields stored on each mission day and returned by the API."""
    one_way = one_way_delay_s(distance_km)
    return {
        "earth_distance_km": distance_km,
        "comms_delay_seconds": one_way,
        "min_round_trip_seconds": round(2 * one_way, 1),
    }
