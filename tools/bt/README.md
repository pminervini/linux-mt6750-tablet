# Bluetooth link test (tablet <-> laptop, RFCOMM)

Verified 2026-11-08 against the laptop's Intel adapter (B4:B6:76:07:DC:25):
SSP/Secure Connections pairing + bonding OK; 1 MiB RFCOMM transfer with SHA-256 check
tablet->laptop ~1.1-1.3 Mbit/s, laptop->tablet ~0.9 Mbit/s.

- `btrc.py`: RFCOMM sockets via libc (Debian's Python here lacks AF_BLUETOOTH).
- `rfsrv.py` on the receiver (binds its own BD address, channel 22), `rfcli.py` on the sender.
- Pairing without prompts: `bluetoothctl --agent NoInputNoOutput` on both sides, answer "yes";
  the tablet needs `btmgmt bondable on`.
- Note: one huge write (1 MiB in a single sendall) on the tablet stalls the RFCOMM socket until
  close; normal-sized writes are fine.
