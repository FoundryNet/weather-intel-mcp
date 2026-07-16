import core


def register(mcp) -> None:
    @mcp.tool
    async def mint_info() -> dict:
        """FoundryNet Data Network info + provenance attestation details. FREE.

        Returns how to attest your agent's weather/climate analysis for verifiable
        provenance, the attestation endpoint, and the sister data servers
        (gov-contracts, brand-intel, patent-intel, financial-signals).
        """
        return core.mint_info()
