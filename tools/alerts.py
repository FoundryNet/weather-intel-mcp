from typing import Optional

import core


def register(mcp) -> None:
    @mcp.tool
    async def weather_alerts(
        state: Optional[str] = None,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        radius_km: Optional[float] = None,
    ) -> dict:
        """Active NWS severe-weather alerts (US). FREE — public safety. Query by
        state code, by latitude+longitude (point), or with no args for nationwide.

        Args:
            state: 2-letter US state code, e.g. "TX".
            latitude: decimal latitude (point query).
            longitude: decimal longitude (point query).
            radius_km: reserved (point query uses the NWS point lookup).
        """
        return await core.do_alerts(state, latitude, longitude, radius_km)
