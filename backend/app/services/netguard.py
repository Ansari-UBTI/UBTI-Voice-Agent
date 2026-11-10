"""Outbound HTTP to tenant-supplied URLs (custom functions, knowledge URLs, webhooks).

Blocks private, loopback, link-local (cloud metadata 169.254.169.254), and other reserved addresses, and checks
every redirect hop again, so a tenant cannot make the server reach internal services."""
import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import httpx

from ..config import settings


class BlockedURL(ValueError):
    pass


def _bad_ip(ip: str) -> bool:
    a = ipaddress.ip_address(ip)
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped:
        a = a.ipv4_mapped
    return (a.is_private or a.is_loopback or a.is_link_local or a.is_multicast or a.is_reserved
            or a.is_unspecified or (isinstance(a, ipaddress.IPv4Address) and a in ipaddress.ip_network("100.64.0.0/10")))


async def check(url: str):
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise BlockedURL("Only http(s) URLs are allowed")
    if settings.ALLOW_PRIVATE_URLS:
        return
    host = u.hostname
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, u.port or (443 if u.scheme == "https" else 80),
                                                             type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise BlockedURL(f"Cannot resolve {host}")
    for info in infos:
        if _bad_ip(info[4][0]):
            raise BlockedURL(f"{host} points to a private or internal address, which is not allowed")


async def request(method: str, url: str, *, timeout: float = 10, max_redirects: int = 3, **kw) -> httpx.Response:
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as c:
        for _ in range(max_redirects + 1):
            await check(url)
            r = await c.request(method, url, **kw)
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                url = urljoin(url, r.headers["location"])
                if r.status_code == 303 or (r.status_code in (301, 302) and method.upper() == "POST"):
                    method, kw = "GET", {k: v for k, v in kw.items() if k not in ("json", "content", "data")}
                continue
            return r
    raise BlockedURL("Too many redirects")
