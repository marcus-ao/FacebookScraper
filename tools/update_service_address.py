"""Update the source LAN policy and Feishu card URL together."""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import sys
import tomllib

from core.paid_model import atomic_write_text
from core.web_access import WebAccess

ROOT = Path(__file__).resolve().parents[1]
NETWORK = Path('ops/service-machine.network.json')


def address_policy(current: WebAccess, address: str, cidrs=None, port=None) -> WebAccess:
    address = address.strip()
    if '/' in address:
        if cidrs is not None:
            raise ValueError('IP/前缀和 --allow-client-subnet 不能同时使用。')
        interface = ipaddress.IPv4Interface(address)
        if interface.network.prefixlen < 31 and interface.ip in (
                interface.network.network_address, interface.network.broadcast_address):
            raise ValueError('服务地址不能是该子网的网络地址或广播地址。')
        ip = interface.ip
        cidrs = [str(interface.network)]
    else:
        ip = ipaddress.IPv4Address(address)
        if cidrs is None:
            if not current.permits_client(str(ip)):
                raise ValueError('新 IP 不在现有允许网段内；请提供现场确认的 IP/前缀或 --allow-client-subnet。')
            cidrs = list(current.allowed_client_cidrs)
    if not ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast:
        raise ValueError('请输入服务机的内网 IPv4 地址。')
    port = current.web_port if port is None else port
    return WebAccess.from_mapping({'web_host': '0.0.0.0', 'web_port': port,
        'public_base_url': f'http://{ip}:{port}', 'allowed_client_cidrs': cidrs})


def update_address(root: Path, address: str, *, cidrs=None, port=None, dry_run=False) -> WebAccess:
    if os.environ.get('FBSCRAPER_CONTROL_DIR') or (root / 'release.json').exists():
        raise ValueError('请在源码检出的非受管终端运行；受管实例须另行维护 control/host.json。')
    network_path, config_path = root / NETWORK, root / 'config.toml'
    originals = {path: path.read_bytes().decode('utf-8') for path in (network_path, config_path)}
    network = json.loads(originals[network_path])
    current = WebAccess.from_mapping(network)
    policy = address_policy(current, address, cidrs, port)
    config_text = originals[config_path].replace('\r\n', '\n')
    config = tomllib.loads(config_text)
    if not isinstance(config.get('feishu', {}).get('base_url'), str):
        raise ValueError('config.toml 缺少 [feishu].base_url；未写入任何文件。')
    section = re.search(r'(?m)^[ \t]*\[feishu\][ \t]*(?:#[^\n]*)?\n(?P<body>[\s\S]*?)(?=^[ \t]*\[|\Z)', config_text)
    if section is None:
        raise ValueError('找不到独立的 [feishu] 配置段；未写入任何文件。')
    body, count = re.subn(
        r'''(?m)^([ \t]*base_url[ \t]*=[ \t]*)(?:"[^"\n]*"|'[^'\n]*')([ \t]*(?:#[^\n]*)?)$''',
        lambda match: match[1] + json.dumps(policy.public_base_url) + match[2], section['body'])
    if count != 1:
        raise ValueError('[feishu].base_url 须为单行字符串；未写入任何文件。')
    updated_config = config_text[:section.start('body')] + body + config_text[section.end('body'):]
    config['feishu']['base_url'] = policy.public_base_url
    if tomllib.loads(updated_config) != config:
        raise ValueError('TOML 更新影响了其他配置；未写入任何文件。')
    updates = {}
    if current != policy:
        updates[network_path] = json.dumps({**network, **policy.as_dict()}, ensure_ascii=False, indent=2) + '\n'
    if updated_config != originals[config_path]:
        updates[config_path] = updated_config
    print(f'入口：{current.public_base_url} -> {policy.public_base_url}')
    print(f'监听：{policy.web_host}:{policy.web_port}；允许来源：{", ".join(policy.allowed_client_cidrs)}')
    for path in updates:
        print(('预览：' if dry_run else '更新：') + str(path.relative_to(root)))
    if not dry_run:
        written = []
        try:
            for path, text in updates.items():
                atomic_write_text(path, text, newline='')
                written.append(path)
        except OSError:
            # Both files are one edit: undo completed writes when the second replacement fails.
            for path in reversed(written):
                atomic_write_text(path, originals[path], newline='')
            raise
    return policy


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('address', nargs='?', help='服务机 IPv4 或 IP/前缀，例如 10.66.6.3/24')
    parser.add_argument('--allow-client-subnet', action='append', dest='cidrs', help='明确允许的 IPv4 CIDR，可重复')
    parser.add_argument('--port', type=int, help='不指定时保留当前端口')
    parser.add_argument('--dry-run', action='store_true', help='只校验和展示，不写文件')
    args = parser.parse_args(argv)
    try:
        if not args.address:
            current = WebAccess.from_mapping(json.loads((ROOT / NETWORK).read_text('utf-8')))
            print(f'当前入口：{current.public_base_url}；允许来源：{", ".join(current.allowed_client_cidrs)}')
            args.address = input('新服务机 IPv4（可带现场确认的 /前缀）：').strip()
            if '/' not in args.address and args.cidrs is None:
                ip = ipaddress.IPv4Address(args.address)
                if not current.permits_client(str(ip)):
                    value = input('新 IP 不在现有网段内。请输入允许来源 CIDR（多个以逗号分隔）：').strip()
                    args.cidrs = [item.strip() for item in value.split(',')]
        update_address(ROOT, args.address, cidrs=args.cidrs, port=args.port, dry_run=args.dry_run)
        if not args.dry_run:
            print('配置已同步。检查 git diff，提交并合并到 main 后，在服务机拉取并重启 Web 和调度器。')
            print('局域网启动：scripts\\run_web_lan.bat；防火墙改址与客户端验收见 docs/MANUAL_STEPS.md §13.1。')
            print('已发出或已冻结在发件箱中的飞书链接保持原样。')
        return 0
    except (OSError, ValueError, EOFError, KeyboardInterrupt) as exc:
        print('更新未完成：' + (str(exc) or '输入已取消。'), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
