import socket, hashlib, time
import sys; sys.path.insert(0, "/tmp"); sys.path.insert(0, __import__("os").path.dirname(__file__)); import btrc
c = btrc.listen("B4:B6:76:07:DC:25", 22); print("accepted", flush=True)
h = hashlib.sha256(); n = 0; t0 = time.time()
n_exp = int(c.recv(16).split(b"\n")[0])
while n < n_exp:
    d = c.recv(65536)
    if not d: break
    h.update(d); n += len(d)
dt = time.time() - t0
print(f"got {n} bytes in {dt:.2f}s = {n*8/dt/1000:.0f} kbit/s", flush=True)
c.sendall(h.hexdigest().encode()); time.sleep(1); c.close()
