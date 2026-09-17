"""Network admission for the direct, single-origin office HTTP service."""
from __future__ import annotations

import os

from starlette.requests import Request
from starlette.responses import JSONResponse

from core.web_access import client_ip, http_origin, load_web_access

DENIED = {'code': 'access_denied', 'detail': '当前地址或来源未获允许，请使用公布的审校台入口，或联系技术人员检查访问配置。'}


class AccessMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        try:
            policy = load_web_access()
        except ValueError:
            response = JSONResponse({'code': 'access_config_invalid',
                'detail': '服务访问配置需要技术人员检查，请保留当前内容。'}, status_code=503)
            return await response(scope, receive, send)
        try:
            request = Request(scope)
            peer = client_ip(request.client.host) if request.client else None
            if peer is None or not policy.permits_client(str(peer)):
                raise ValueError('source')
            if len(request.headers.getlist('host')) != 1 or len(request.headers.getlist('origin')) > 1:
                raise ValueError('ambiguous headers')
            origin = http_origin(request.url.scheme + '://' + request.headers['host'])
            local_host = origin[1] in {'127.0.0.1', 'localhost', '::1'}
            local = peer.is_loopback and local_host
            if local:
                if os.environ.get('FBSCRAPER_CONTROL_DIR') and origin[2] != policy.web_port:
                    raise ValueError('local port')
            elif origin != http_origin(policy.public_base_url):
                raise ValueError('host')
            if scope['path'] == '/api/health' and not local:
                raise ValueError('local health only')
            supplied = request.headers.get('origin')
            if supplied is not None and http_origin(supplied) != origin:
                raise ValueError('cross origin')
            mutation = request.method not in {'GET', 'HEAD', 'OPTIONS'}
            if mutation and not peer.is_loopback:
                if supplied is None:
                    raise ValueError('origin required')
                if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
                    raise ValueError('json required')
        except (ValueError, TypeError):
            return await JSONResponse(DENIED, status_code=403)(scope, receive, send)
        return await self.app(scope, receive, send)
