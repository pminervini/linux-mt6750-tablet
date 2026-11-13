# Guarded standard audio and camera interfaces

This release includes hardware-backed ASoC and V4L2 drivers, guarded legacy
helpers, and saved-RAW software processing. On 2026-11-13, authorised silent
checks verified installation, idle enumeration, ordinary unload, and reload on
the unchanged native Linux 7.3.1 kernel. This is not media-stream acceptance.

- [Audio](tools/audio/README.md): the MT6755 card, speaker/FM endpoints, and
  software mixer enumerate. The owner supplies actual idle-register status,
  preserving the existing muted RF sweep. `allow_playback=0` and
  `allow_capture=0` keep both PCM directions disabled.
- [Camera](tools/camera/v4l2/README.md): both RAW nodes, format/control queries,
  and media topology enumerate after the ioctl/control-lock repair. Ordinary
  unload and reload complete. `allow_streaming=0` keeps sensors unpowered.
- [Saved RAW](tools/camera/raw-develop.py): offline unpack/demosaic/AWB reads
  saved files only. Synthetic CLI tests do not establish camera capture.
- The unchanged four-panel renderer still starts and closes the muted FM
  receiver. Selected volume and its display remain separate from playback.

Actual PCM/DMA, broadcast-derived audio, either camera's images/video/Bayer
phase, autofocus, microphone/jack support, and reboot persistence remain open.
No automatic driver-loading service or new kernel image was installed. The
legacy helpers/data are retained, but their raw hardware paths correctly refuse
access while the corresponding kernel driver owns that hardware; explicit
unload releases that exclusion. Never run old unguarded copies concurrently.

The new payload contains portable source/tests/documentation, not operator
settings, identifiers, credentials, firmware, calibration, captures, or trial
logs. Runtime and rollback receipts remain operator-local. Earlier failed
trials remain historical evidence; their failures are not represented as passes.
