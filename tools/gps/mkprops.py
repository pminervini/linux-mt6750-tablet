#!/usr/bin/python3
"""Write a legacy (single-file) bionic property area: mkprops.py <out> name=value...

Layout (bionic system_properties, Android 8.1): prop_area header
{bytes_used, serial, magic 'PROP', version 0xfc6ed0ab, reserved[28]} then a trie of
prop_bt {namelen, prop, left, right, children, name[]} nodes (one per dotted name
component, siblings as a binary search tree ordered by (len, name)) and prop_info
{serial, value[92], name[]} records. Offsets are relative to the data area.
"""
import struct, sys

PA_SIZE = 128 * 1024
HDR = 4 * 4 + 28 * 4

class Node:
    def __init__(self, name):
        self.name, self.kids, self.value, self.off = name, {}, None, 0

def align(n): return (n + 3) & ~3

def build(props):
    root = Node(b"")
    for k, v in props:
        n = root
        for part in k.split(b"."):
            n = n.kids.setdefault(part, Node(part))
        n.value = (k, v)
    data = bytearray()
    def alloc(size):
        off = len(data); data.extend(b"\0" * align(size)); return off
    def emit(n):
        n.off = alloc(20 + len(n.name) + 1)
        struct.pack_into("<I", data, n.off, len(n.name))
        data[n.off + 20:n.off + 20 + len(n.name)] = n.name
        if n.value:
            k, v = n.value
            assert len(v) < 92
            pi = alloc(4 + 92 + len(k) + 1)
            struct.pack_into("<I", data, pi, len(v) << 24)        # serial: value length in top byte
            data[pi + 4:pi + 4 + len(v)] = v
            data[pi + 96:pi + 96 + len(k)] = k
            struct.pack_into("<I", data, n.off + 4, pi)
        kids = sorted(n.kids.values(), key=lambda c: (len(c.name), c.name))
        for c in kids:
            emit(c)
        def bst(lst):                                             # balanced BST of siblings
            if not lst: return 0
            m = len(lst) // 2
            struct.pack_into("<I", data, lst[m].off + 8, bst(lst[:m]))
            struct.pack_into("<I", data, lst[m].off + 12, bst(lst[m + 1:]))
            return lst[m].off
        struct.pack_into("<I", data, n.off + 16, bst(kids))
    emit(root)
    return data

def main():
    out, props = sys.argv[1], [a.encode().split(b"=", 1) for a in sys.argv[2:]]
    data = build(props)
    hdr = struct.pack("<IIII", len(data), 0, 0x504f5250, 0xfc6ed0ab) + b"\0" * 112
    blob = (hdr + data).ljust(PA_SIZE, b"\0")
    open(out, "wb").write(blob)

main()
