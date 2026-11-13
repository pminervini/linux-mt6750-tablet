# VPN-only SMB mount

The guard retains one OUTPUT DROP rule for the selected IPv4 server's TCP port 445 outside its VPN interface. It neither clears firewall rules nor removes this rule when stopped. The mount drop-in requires an already-running VPN and stops the mount when that VPN or interface disappears; it does not start the VPN or remount automatically.

These are portable sources, not the device's installed configuration. Keep server addresses, VPN profiles, credentials, and generated units outside Git. Configure an existing SMB 3.1.1 mount with mandatory signing, `noauto,nofail,_netdev`, a root-only credential file, local permission checking, and the intended owner's UID/GID (directories 0700, files 0600). Keep the underlying mount directory root-owned mode 000. Do not apply a new network policy remotely without independent recovery access.

Create root-only `/etc/default/tablet-vpn-share` with `SMB_SERVER` (the server's IPv4 address), `VPN_INTERFACE` (an alphanumeric interface name), and `VPN_SERVICE` (the existing VPN service unit). Then, as root in this source directory, render the mount drop-in using those same values:

```sh
set -a
. /etc/default/tablet-vpn-share
set +a
# Set MOUNTPOINT to the mount path already configured in fstab.
: "${MOUNTPOINT:?set MOUNTPOINT}"
MOUNT_UNIT=$(systemd-escape --path --suffix=mount "$MOUNTPOINT")
install -d "/etc/systemd/system/$MOUNT_UNIT.d"
python3 -c 'import os,string,sys; sys.stdout.write(string.Template(sys.stdin.read()).substitute(os.environ))' < vpn-share-mount.conf.in > "/etc/systemd/system/$MOUNT_UNIT.d/vpn.conf"
install -m 0644 vpn-share-vpn-guard.service /etc/systemd/system/
systemd-analyze verify vpn-share-vpn-guard.service "$MOUNT_UNIT"
```

Review the rendered dependencies, then reload systemd and enable/start the guard before manually mounting with the VPN already connected. This changes no installed setup until explicitly applied. Test owner/root I/O, foreign-user denial, and VPN-loss unmount/firewall behaviour with an independent restoration path. `build.sh config` enables the loadable CIFS/UTF-8 client with insecure legacy dialects disabled; rebuild modules against the actual kernel after ABI changes. Offline unit tests: `python3 -B -m unittest discover -s tools/tablet-etc -v` from the repository root. They use a stand-in firewall command, never the host firewall.
