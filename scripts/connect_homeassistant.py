"""Connect this workspace to Home Assistant without saving an account password."""
import argparse
import getpass
import json
import sys

from printlab import core, homeassistant


def main(argv=None):
    parser = argparse.ArgumentParser(description='Connect Home Assistant for printer and cached-model viewing')
    parser.add_argument('--url', required=True, help='Home Assistant base URL')
    parser.add_argument('--username', required=True)
    args = parser.parse_args(argv)
    password = getpass.getpass('Home Assistant password: ')
    try:
        result = homeassistant.connect(args.url, args.username, password)
        del password
        listing = homeassistant.device_list()
        if not listing.get('devices'):
            raise core.WorkflowError('已登录 HA，但未读到拓竹设备；请核对 Bambu Lab 集成和账户权限')
        with core.lock('connection-config'):
            config = core.config()
            config['device_provider'] = 'homeassistant'
            core.write_json(core.ROOT / 'Runtime/config.json', config)
        print(json.dumps({**result, 'devices': listing['devices'], 'dispatch_available': False}, ensure_ascii=False, indent=2))
        return 0
    except core.WorkflowError as exc:
        print(json.dumps({'connected': False, 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
