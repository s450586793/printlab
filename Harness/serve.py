#!/usr/bin/env python3
"""One service process; loopback by default, with explicit container listeners."""
import argparse
import ipaddress
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
import uvicorn

SOURCE_ROOT = Path(os.environ.get('PRINTLAB_SOURCE_ROOT', Path(__file__).resolve().parent.parent)).expanduser().resolve()
ROOT = Path(os.environ.get('PRINTLAB_ROOT', SOURCE_ROOT)).expanduser().resolve()


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('service', choices=['studio', 'bambuddy'])
    p.add_argument('--port', type=int, help='Override the default service port')
    p.add_argument('--host', default=os.environ.get('PRINTLAB_HOST', '127.0.0.1'), help='IPv4 listener; container deployments may explicitly use 0.0.0.0')
    args = p.parse_args(argv)
    try:
        port = args.port if args.port is not None else int(os.environ.get('PRINTLAB_PORT', 8018 if args.service == 'studio' else 8017))
    except ValueError:
        p.error('PRINTLAB_PORT must be an integer')
    if not 1 <= port <= 65535:
        p.error('port must be between 1 and 65535')
    try:
        primary = str(ipaddress.IPv4Address(args.host))
    except ipaddress.AddressValueError:
        p.error('host must be an IPv4 address')
    if args.service == 'studio':
        os.environ['PRINTLAB_STUDIO_PORT'] = str(port)
    module = 'printlab.api:app' if args.service == 'studio' else 'backend.app.main:app'
    sys.path.insert(0, str(SOURCE_ROOT / 'Harness' if args.service == 'studio' else ROOT / 'Runtime/vendor/bambuddy'))
    hosts = [primary]
    try:
        # macOS app CLI can be unavailable to launchd even while Tailscale is online.
        # The installer records the observed address; it is not a public bind.
        cfg = json.loads((ROOT/'Runtime/config.json').read_text(encoding="utf-8"))
        ip = cfg.get('tailscale_ip')
        if not ip:
            raise ValueError('No remote listener configured')
        if primary != '0.0.0.0' and ipaddress.ip_address(ip) in ipaddress.ip_network('100.64.0.0/10') and ip not in hosts:
            hosts.append(ip)
    except (OSError, ValueError, subprocess.SubprocessError):
        print(f'Listening on {primary}.',flush=True)
    sockets=[]
    try:
        for host in hosts:
            sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            try:
                sock.bind((host,port));sock.listen(2048);sockets.append(sock)
            except OSError:
                sock.close()
                if host == primary:raise
                print('Private Tailscale address not available; restart when network returns.',flush=True)
        config=uvicorn.Config(module,loop='asyncio',host=primary,port=port,
            proxy_headers=os.environ.get('PRINTLAB_PROXY_HEADERS') == '1',
            forwarded_allow_ips=os.environ.get('PRINTLAB_FORWARDED_ALLOW_IPS', '127.0.0.1'))
        uvicorn.Server(config).run(sockets=sockets)
    finally:
        for sock in sockets:sock.close()


if __name__=='__main__':
    main()
