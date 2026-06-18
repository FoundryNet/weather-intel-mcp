from typing import Optional

import core


def register(mcp) -> None:
    @mcp.tool
    async def current_weather(
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        city: Optional[str] = None,
        state: Optional[str] = None,
        country: Optional[str] = None,
    ) -> dict:
        """Current conditions for a location — temperature, feels-like, humidity,
        wind, conditions, cloud cover, and visibility. FREE. Give either
        latitude+longitude or a city (optionally with state/country).

        Args:
            latitude: decimal latitude.
            longitude: decimal longitude.
            city: city name (geocoded), e.g. "Denver".
            state: optional state/region to disambiguate the city.
            country: optional country name or code to disambiguate the city.
        """
        return await core.do_current(latitude, longitude, city, state, country)
