"""Persistent installation network policy shared by workers, HTTP and notifications."""
from __future__ import annotations

import ipaddress
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


def http_origin(value: str) -> tuple[str, str, int]:
    """Parse an explicit HTTP origin without accepting URLs that carry other data."""
    if not isinstance(value, str) or not value or any(c.isspace() for c in value):
        raise ValueError('invalid_web_origin')
    parsed = urlsplit(value)
    if (parsed.scheme != 'http' or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.path not in {'', '/'}
            or '?' in value or '#' in value or '\\' in value):
        raise ValueError('invalid_web_origin')
    host = parsed.hostname.lower()
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if (re.fullmatch(r'[0-9.]+', host) or len(host) > 253
                or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part)
                           for part in host.split('.'))):
            raise ValueError('invalid_web_host') from None
    port = 80 if parsed.port is None else parsed.port
    if not 1 <= port <= 65535:
        raise ValueError('invalid_web_port')
    return 'http', host, port


def client_ip(value: str):
    address = ipaddress.ip_address(value)
    return address.ipv4_mapped or address if isinstance(address, ipaddress.IPv6Address) else address


@dataclass(frozen=True)
class WebAccess:
    web_host: str = '127.0.0.1'
    web_port: int = 8765
    public_base_url: str = 'http://127.0.0.1:8765'
    allowed_client_cidrs: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, value: dict) -> 'WebAccess':
        if not isinstance(value, dict):
            raise ValueError('invalid_web_settings')
        host = value.get('web_host', '127.0.0.1')
        port = value.get('web_port', 8765)
        if host not in {'127.0.0.1', '0.0.0.0'} or type(port) is not int or not 1 <= port <= 65535:
            raise ValueError('invalid_web_binding')
        url = value.get('public_base_url')
        if url is None and host == '127.0.0.1':
            url = f'http://127.0.0.1:{port}'
        origin = http_origin(url)
        if origin[2] != port:
            raise ValueError('web_url_port_mismatch')
        public_host = origin[1]
        local = public_host == 'localhost' or public_host.endswith('.localhost')
        try:
            address = ipaddress.ip_address(public_host)
        except ValueError:
            address = None
        if address is not None:
            local |= address.is_loopback
            if address.is_unspecified or address.is_multicast or address.is_link_local or address.version != 4:
                raise ValueError('invalid_public_web_address')
        if (host == '0.0.0.0' and local) or (host == '127.0.0.1' and not local):
            raise ValueError('public_web_address_mismatch')
        raw = value.get('allowed_client_cidrs', [])
        if not isinstance(raw, (list, tuple)) or not all(isinstance(item, str) for item in raw):
            raise ValueError('invalid_web_subnets')
        networks = []
        for item in raw:
            network = ipaddress.ip_network(item, strict=True)
            if (network.version != 4 or network.prefixlen == 0 or network.is_multicast
                    or network.is_loopback or network.is_link_local):
                raise ValueError('invalid_web_subnet')
            networks.append(str(network))
        if host == '0.0.0.0' and not networks:
            raise ValueError('lan_subnet_required')
        if host == '127.0.0.1' and networks:
            raise ValueError('lan_subnet_requires_lan_binding')
        return cls(host, port, url.rstrip('/'), tuple(dict.fromkeys(networks)))

    def as_dict(self) -> dict:
        return {'web_host': self.web_host, 'web_port': self.web_port,
                'public_base_url': self.public_base_url, 'allowed_client_cidrs': list(self.allowed_client_cidrs)}

    def permits_client(self, address: str) -> bool:
        peer = client_ip(address)
        return peer.is_loopback or (self.web_host == '0.0.0.0'
            and any(peer in ipaddress.ip_network(item) for item in self.allowed_client_cidrs))


def load_web_access(control: Path | None = None) -> WebAccess:
    raw = str(control) if control is not None else os.environ.get('FBSCRAPER_CONTROL_DIR', '')
    if not raw:
        return WebAccess()
    try:
        return WebAccess.from_mapping(json.loads((Path(raw) / 'host.json').read_text(encoding='utf-8')))
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError('invalid_managed_web_settings') from exc
