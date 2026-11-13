"""Offline checks for the portable VPN share policy; no host firewall calls."""
import json
import os
from pathlib import Path
import shlex
import string
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent


class ShareUnitTests(unittest.TestCase):
    def test_mount_requires_existing_vpn_and_stops_with_interface(self):
        values = {'VPN_SERVICE': 'openvpn-client@test.service', 'VPN_INTERFACE': 'tun99'}
        unit = string.Template((ROOT/'vpn-share-mount.conf.in').read_text()).substitute(values)
        self.assertIn('Requisite='+values['VPN_SERVICE'], unit)
        self.assertNotIn('Requires='+values['VPN_SERVICE'], unit)
        self.assertIn('PartOf='+values['VPN_SERVICE'], unit)
        self.assertIn('BindsTo=sys-subsystem-net-devices-tun99.device', unit)
        self.assertIn('Requires=vpn-share-vpn-guard.service', unit)
        self.assertIn('ConditionPathExists=/sys/class/net/tun99', unit)

    def test_guard_scope_idempotence_and_failure(self):
        unit = (ROOT/'vpn-share-vpn-guard.service').read_text()
        self.assertIn('EnvironmentFile=/etc/default/tablet-vpn-share', unit)
        self.assertNotIn('ExecStop=', unit)
        args = shlex.split(next(s.split('=', 1)[1] for s in unit.splitlines() if s.startswith('ExecStart=')))
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            folder = Path(directory)
            firewall = folder/'iptables'
            firewall.write_text('#!/usr/bin/python3\nimport json,os,sys\nwith open(os.environ["CALLS"],"a") as f: f.write(json.dumps(sys.argv[1:])+"\\n")\nsys.exit(int(os.environ["CHECK_EXIT"] if sys.argv[1]=="-C" else os.environ["INSERT_EXIT"]))\n')
            firewall.chmod(0o700)
            # systemd turns $$ into a literal $ before invoking the shell.
            args[-1] = args[-1].replace('$$', '$').replace('/usr/sbin/iptables', shlex.quote(str(firewall)))
            log = folder/'calls'
            for server, interface, check, insert, expected in [('', '', 0, 0, False), ('198.51.100.10', '', 0, 0, False), ('', 'tun99', 0, 0, False), ('198.51.100.10', 'tun99', 0, 0, True), ('198.51.100.10', 'tun99', 1, 0, True), ('198.51.100.10', 'tun99', 1, 2, False)]:
                with self.subTest(server=server, interface=interface, check=check, insert=insert):
                    log.write_text('')
                    env = {**os.environ, 'SMB_SERVER': server, 'VPN_INTERFACE': interface, 'CALLS': str(log), 'CHECK_EXIT': str(check), 'INSERT_EXIT': str(insert)}
                    result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=5)
                    self.assertEqual(result.returncode == 0, expected)
                    calls = [json.loads(line) for line in log.read_text().splitlines()]
                    if not server or not interface:
                        self.assertEqual(calls, [])
                    else:
                        rule = ['-d', server+'/32', '-p', 'tcp', '--dport', '445', '!', '-o', interface, '-j', 'DROP']
                        self.assertEqual(calls, [['-C', 'OUTPUT', *rule]] + ([['-I', 'OUTPUT', '1', *rule]] if check else []))


if __name__ == '__main__':
    unittest.main(verbosity=2)
