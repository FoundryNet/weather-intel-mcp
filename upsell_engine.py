"""
Contextual upsell engine — recommends relevant FoundryNet products
in every paid tool response. Zero-config, reads from server config.
"""
import os
from datetime import date

SERVER_NAME = os.environ.get("SERVER_NAME", "unknown")

# Map each server to its most relevant cross-sell
CROSS_SELL_MAP = {
    "financial-signals": {
        "sibling": "patent-intel",
        "sibling_tool": "company_patents",
        "reason": "Check patent filings for this company",
        "endpoint": "https://patent-intel-mcp-production.up.railway.app/mcp"
    },
    "crypto-intel": {
        "sibling": "financial-signals",
        "sibling_tool": "anomaly_alert",
        "reason": "Cross-reference traditional market anomalies",
        "endpoint": "https://financial-signals-mcp-production.up.railway.app/mcp"
    },
    "market-data": {
        "sibling": "financial-signals",
        "sibling_tool": "insider_activity",
        "reason": "Check insider trading activity for this ticker",
        "endpoint": "https://financial-signals-mcp-production.up.railway.app/mcp"
    },
    "cyber-intel": {
        "sibling": "oss-intel",
        "sibling_tool": "dependency_risk",
        "reason": "Check if affected packages are in your dependencies",
        "endpoint": "https://oss-intel-mcp-production.up.railway.app/mcp"
    },
    "compliance": {
        "sibling": "gov-contracts",
        "sibling_tool": "search_contracts",
        "reason": "Find related government contracts in this sector",
        "endpoint": "https://gov-contracts-mcp-production.up.railway.app/mcp"
    },
    "gov-contracts": {
        "sibling": "compliance",
        "sibling_tool": "compliance_alerts",
        "reason": "Check regulatory requirements for this NAICS sector",
        "endpoint": "https://compliance-mcp-production.up.railway.app/mcp"
    },
    "patent-intel": {
        "sibling": "financial-signals",
        "sibling_tool": "company_profile",
        "reason": "Check financial health of this assignee",
        "endpoint": "https://financial-signals-mcp-production.up.railway.app/mcp"
    },
    "brand-intel": {
        "sibling": "cyber-intel",
        "sibling_tool": "check_domain",
        "reason": "Check threat intelligence for this domain",
        "endpoint": "https://cyber-intel-mcp-production.up.railway.app/mcp"
    },
    "academic-intel": {
        "sibling": "patent-intel",
        "sibling_tool": "search_patents",
        "reason": "Find patents related to this research area",
        "endpoint": "https://patent-intel-mcp-production.up.railway.app/mcp"
    },
    "oss-intel": {
        "sibling": "cyber-intel",
        "sibling_tool": "search_cve",
        "reason": "Check known vulnerabilities for this package",
        "endpoint": "https://cyber-intel-mcp-production.up.railway.app/mcp"
    },
    "social-intel": {
        "sibling": "brand-intel",
        "sibling_tool": "domain_profile",
        "reason": "Get full company profile for trending brands",
        "endpoint": "https://brand-intel-mcp-production.up.railway.app/mcp"
    },
    "fact-check": {
        "sibling": "academic-intel",
        "sibling_tool": "search_papers",
        "reason": "Find academic sources supporting this claim",
        "endpoint": "https://academic-intel-mcp-production.up.railway.app/mcp"
    },
    "weather-intel": {
        "sibling": "compliance",
        "sibling_tool": "compliance_alerts",
        "reason": "Check environmental compliance for this region",
        "endpoint": "https://compliance-mcp-production.up.railway.app/mcp"
    },
    "email-verify": {
        "sibling": "brand-intel",
        "sibling_tool": "domain_profile",
        "reason": "Get full profile for this email domain",
        "endpoint": "https://brand-intel-mcp-production.up.railway.app/mcp"
    },
    "currency-intel": {
        "sibling": "financial-signals",
        "sibling_tool": "macro_dashboard",
        "reason": "Check macro indicators affecting exchange rates",
        "endpoint": "https://financial-signals-mcp-production.up.railway.app/mcp"
    }
}


def get_upsell(brief_price: float = None, brief_signal_count: int = None) -> dict:
    """Generate contextual upsell for the current server."""
    upsell = {
        "network": "FoundryNet Data Network (17 servers)",
        "catalog": "https://foundrynet-agents-production.up.railway.app/v1/daily-offerings",
        "live_feed": "https://mint.foundrynet.io/feed"
    }

    if brief_price and brief_signal_count:
        upsell["daily_brief"] = {
            "available": True,
            "signals_today": brief_signal_count,
            "price_usd": brief_price,
            "tool": "daily_brief",
            "note": f"Today's curated intelligence — {brief_signal_count} signals for ${brief_price}"
        }

    cross = CROSS_SELL_MAP.get(SERVER_NAME)
    if cross:
        upsell["related"] = {
            "server": cross["sibling"],
            "tool": cross["sibling_tool"],
            "reason": cross["reason"],
            "endpoint": cross["endpoint"]
        }

    return upsell
