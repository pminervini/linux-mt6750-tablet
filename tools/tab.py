#!/usr/bin/env python3
"""tab.py up | tab.py run '<cmd>' : talk to the mainline test kernel over USB CDC-Ethernet.
up:  find the g_ether interface (0525:a4a2), give it 192.168.77.1/24.
run: execute a shell command via the tablet's telnetd (192.168.77.2:23), print output."""
import glob, os, re, socket, subprocess, sys, time

HOST = '192.168.77.2'


def iface():
    for n in glob.glob('/sys/class/net/*'):
        d = os.path.realpath(n + '/device/..')
        try:
            v = open(d + '/idVendor').read().strip(); p = open(d + '/idProduct').read().strip()
        except OSError:
            continue
        if v == '0525' and p in ('a4a1', 'a4a2'):
            return os.path.basename(n)


def up(timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        i = iface()
        if i:
            subprocess.run(['sudo', 'nmcli', 'dev', 'set', i, 'managed', 'no'], capture_output=True)
            subprocess.run(['sudo', 'ip', 'addr', 'replace', '192.168.77.1/24', 'dev', i], check=True)
            subprocess.run(['sudo', 'ip', 'link', 'set', i, 'up'], check=True)
            print('iface', i); return i
        time.sleep(1)
    sys.exit('no gadget interface')


def strip_iac(b):
    out, i = bytearray(), 0
    while i < len(b):
        if b[i] == 255 and i + 1 < len(b):
            i += 3 if b[i + 1] in (251, 252, 253, 254) else 2
        else:
            out.append(b[i]); i += 1
    return bytes(out)


def run(cmd, timeout=60):
    s = socket.create_connection((HOST, 23), timeout=10)
    s.settimeout(1)
    time.sleep(0.5)
    try:
        s.recv(4096)
    except socket.timeout:
        pass
    tag = 'E%d' % os.getpid()
    # marker is built at runtime so the echoed command line never matches it
    s.sendall(f"{cmd}\necho __{tag}_RC=$?__\n".encode())
    want = f'__{tag}_RC='.encode()
    buf, t0 = b'', time.time()
    while time.time() - t0 < timeout:
        try:
            d = s.recv(65536)
            if not d:
                break
            buf += d
            if re.search(rb'__' + tag.encode() + rb'_RC=\d+__', strip_iac(buf)):
                break
        except socket.timeout:
            pass
    s.close()
    txt = strip_iac(buf).decode('utf-8', 'replace').replace('\r', '')
    m = re.search(r'__' + tag + r'_RC=(\d+)__', txt)
    body = txt[:m.start()] if m else txt
    lines = [l for l in body.split('\n') if tag not in l]
    while lines and (lines[0].strip() == cmd.split('\n')[0].strip() or not lines[0].strip()):
        lines = lines[1:]
    lines = [l for l in lines if not re.fullmatch(r'[~/ ]*[#$] ?', l)]
    print('\n'.join(lines).rstrip())
    return int(m.group(1)) if m else 1


if __name__ == '__main__':
    if sys.argv[1] == 'up':
        up()
    else:
        sys.exit(run(' '.join(sys.argv[2:])))
