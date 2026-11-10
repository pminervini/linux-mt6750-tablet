"""RFCOMM sockets via libc (Python built without AF_BLUETOOTH)."""
import ctypes, socket, os
libc = ctypes.CDLL(None, use_errno=True)
AF_BT, RFCOMM = 31, 3
class sockaddr_rc(ctypes.Structure):
    _fields_ = [("family", ctypes.c_ushort), ("bdaddr", ctypes.c_ubyte * 6), ("channel", ctypes.c_ubyte)]
def _addr(mac, ch):
    a = sockaddr_rc(); a.family = AF_BT; a.channel = ch
    for i, b in enumerate(reversed(mac.split(":"))): a.bdaddr[i] = int(b, 16)
    return a
def _chk(r):
    if r < 0: e = ctypes.get_errno(); raise OSError(e, os.strerror(e))
    return r
def _sock(): return _chk(libc.socket(AF_BT, socket.SOCK_STREAM, RFCOMM))
def listen(mac, ch):
    fd = _sock(); a = _addr(mac, ch)
    _chk(libc.bind(fd, ctypes.byref(a), ctypes.sizeof(a))); _chk(libc.listen(fd, 1))
    c = _chk(libc.accept(fd, None, None)); os.close(fd)
    return socket.socket(fileno=c)
def connect(mac, ch):
    fd = _sock(); a = _addr(mac, ch)
    _chk(libc.connect(fd, ctypes.byref(a), ctypes.sizeof(a)))
    return socket.socket(fileno=fd)
