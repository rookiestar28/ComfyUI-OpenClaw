"""
Request IP Resolution Service (S6).
Handles safe extraction of client IP behind reverse proxies.
"""

import ipaddress
import logging
from typing import List, Optional

from .env_aliases import get_env_value

logger = logging.getLogger("ComfyUI-OpenClaw.services.request_ip")


def get_trusted_proxies() -> List[ipaddress.IPv4Network]:
    """
    Parse OPENCLAW_TRUSTED_PROXIES (or legacy MOLTBOT_TRUSTED_PROXIES) env var into list of networks.
    Example: "127.0.0.1,10.0.0.0/8"
    """
    raw = (get_env_value("OPENCLAW_TRUSTED_PROXIES", default="") or "").strip()
    if not raw:
        return []

    networks = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            # ip_network handles both single IPs (needs /32 implicitly or strict=False?)
            # Actually ip_network("1.2.3.4") fails in python < 3.8/3.9 depending on strict?
            # Better to try ip_address then ip_network
            try:
                # If it's a plain IP, treat as /32 (v4) or /128 (v6)
                ip = ipaddress.ip_address(part)
                networks.append(ipaddress.ip_network(ip))
            except ValueError:
                # Try as network CIDR
                networks.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            # IMPORTANT: never echo malformed proxy config verbatim into logs.
            logger.warning("Invalid trusted proxy entry ignored.")

    return networks


def is_trusted_proxy(
    ip_str: str, trusted_networks: List[ipaddress.IPv4Network]
) -> bool:
    """Check if IP belongs to a trusted network."""
    if not ip_str or not trusted_networks:
        return False

    try:
        ip = ipaddress.ip_address(ip_str)
        for net in trusted_networks:
            if ip in net:
                return True
    except ValueError:
        pass

    return False


def get_client_ip(request) -> str:
    """
    Resolve the real client IP, respecting trusted proxies.

    Strategy:
    1. Start with request.remote (direct connection)
    2. If OPENCLAW_TRUST_X_FORWARDED_FOR=1 (or legacy MOLTBOT_TRUST_X_FORWARDED_FOR=1) AND request.remote is trusted:
       - Parse X-Forwarded-For (right-to-left)
       - The first IP that is NOT trusted is the client IP.

    Default: Returns request.remote
    """
    remote = request.remote or ""

    # Check opt-in
    trust_xf_raw = get_env_value("OPENCLAW_TRUST_X_FORWARDED_FOR", default="0")
    trust_xf = trust_xf_raw in ("1", "true", "True", "yes")
    if not trust_xf:
        return remote

    # Check if direct source is trusted
    trusted_nets = get_trusted_proxies()
    if not is_trusted_proxy(remote, trusted_nets):
        return remote

    # Parse X-Forwarded-For
    xff = request.headers.get("X-Forwarded-For", "")
    if not xff:
        return remote

    # Split and process right-to-left (nearest proxies first)
    # X-Forwarded-For: client, proxy1, proxy2
    hops = [x.strip() for x in xff.split(",")]
    hops.reverse()

    # The last hop is request.remote (already known trusted).
    # We walk back up the chain.
    candidate = remote

    for hop in hops:
        if is_trusted_proxy(hop, trusted_nets):
            continue
        else:
            # Found the first non-trusted IP -> This is the client
            return hop

    # If all hops are trusted, return the "furthest" one (original client)
    # usually hops[-1]
    if hops:
        return hops[-1]

    return remote
