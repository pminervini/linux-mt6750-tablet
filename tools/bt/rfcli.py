import socket, hashlib, os, time
data = os.urandom(1 << 20)
import sys; sys.path.insert(0, __import__("os").path.dirname(__file__)); import btrc
t0 = time.time(); c = btrc.connect("B4:B6:76:07:DC:25", 22); print(f"connected in {time.time()-t0:.2f}s")
c.sendall(f"{len(data)}\n".ljust(16).encode()); t0 = time.time()
for i in range(0, len(data), 1000): c.sendall(data[i:i+1000])
r = c.recv(64).decode(); dt = time.time() - t0
print(f"sent {len(data)} bytes in {dt:.2f}s = {len(data)*8/dt/1000:.0f} kbit/s; sha256 {'MATCH' if r == hashlib.sha256(data).hexdigest() else 'MISMATCH ' + r}")
