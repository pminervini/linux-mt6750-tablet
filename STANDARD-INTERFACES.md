# Guarded standard audio and camera interfaces

The MT6750/k50v1 tablet has hardware-backed ASoC and V4L2 drivers with guarded
legacy fallbacks. Bounded digital FM capture, camera streams, and ordinary photos
have passed on the unchanged native Linux 7.3.1 kernel. Results apply to this
qualified board/module combination, not every kernel with the same release name.

- [Audio](tools/audio/README.md): broadcast-derived stereo PCM passed DMA-progress,
  receiver/gain reversal, normal-close, and reopen checks with analogue outputs
  independently off. The deployed policy is `legacy_quiesced=1 allow_capture=1
  allow_playback=0`; defaults still deny capture and playback.
- [Camera](tools/camera/v4l2/README.md): rear preview 1632x1224, rear full 3264x2448,
  and front 1600x1200 passed real scene/pattern capture, starvation/competing-owner
  rejection, cancellation, and reopen. BGGR is supported per mode; delivered
  cadence is software-rearmed rather than a sensor-FPS guarantee.
- [Photos](tools/camera/README.md): the existing `tablet-camera` command uses V4L2
  when its kernel owner is loaded, producing JPEG/PNG with automatic or fixed
  exposure/gain. `--raw` retains MediaTek packed10 compatibility. Front is unchanged;
  rear uses the practical vertical flip, with no added horizontal mirror.
- The four-panel display consumes actual FM PCM for FFT/RMS/peak/clipping and age,
  clears stale measurements, and preserves input dismissal and console recovery.
  Missing or unsafe capture remains explicitly unavailable.

Camera close returns owned rails, pins, and DMA allocations to idle. The raw
legacy paths remain excluded while their kernel owner exists; loading and legacy
access share cooperative locks. Old unguarded copies must not run concurrently.

A guarded camera startup unit is enabled on the qualified installation, without
a reboot. Its manual loading path and enablement were checked; actual reboot
persistence remains untested. FM activation remains current-boot-only. Reproduce
camera startup with operator-local guards as described in the camera README;
this source release neither enables services nor authorises hardware operation.

Analogue playback, microphones/jack wiring, autofocus, calibrated colour/physical
chirality, standard-app/libcamera compliance, live suspend stress, and indefinite
soak remain unaccepted. No kernel image was replaced for these module trials.

Only portable source, tests, and documentation are published. Operator settings,
identifiers, credentials, firmware, calibration, captures, and trial logs remain
outside the repositories. Offline synthetic checks and hardware acceptance are
reported separately; earlier failed trials are retained privately.
