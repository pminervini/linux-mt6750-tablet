# MT6755 tablet ASoC candidate - guarded idle deployment verified

The board-specific k50v1 MT6750/MT6755 + MT6353 candidate exposes a real ALSA card.
The approved idle trial verified MT6755 speaker/FM digital endpoint listings,
PCM Playback Volume range 0..100, the expected initial 30%, and clean unload.
Callbacks now use dedicated card drvdata because ASoC card registration owns the
device drvdata slot; matching-core host API tests reproduce the old wrong read.
The guarded helper and module set were installed on 2026-11-13. Idle card/mixer
enumeration, ordinary unload, and reload passed without changing the running
kernel. Playback and capture are disabled in this deployment. Actual playback,
recording, DMA/XRUN recovery and genuine received FM PCM remain unverified.
Detailed hardware-trial receipts remain operator-local and are not shipped
with this source checkpoint. Earlier build receipts do not identify current
module bytes or establish PCM acceptance.

## Interface contract

* Module: `snd-soc-mt6755-tablet.ko`; module/sysfs name
  `snd_soc_mt6755_tablet`; card name/expected ALSA ID `MT6755`.
* Link/device 0: `DL2-MT6353-AW8736`, playback only. Real AFE DL2 DMA reads a
  coherent, below-4-GiB physical buffer; I07/I08 -> O03/O04 -> ADDA -> MT6353
  HPL/HPR -> existing AW8736 speaker wiring. The module uses the BSP's DRAM
  DL2 path, not a fabricated PCM or snd_dummy/loopback. The preserved raw
  fallback still uses the previously deployed SRAM path.
* Link/device 1: `CONNSYS-ASRC-AWB`, capture only. Internal CONNSYS I2S input,
  slave/I2S/16-bit, expected 32 kHz input, ASRC 32 -> 48 kHz, I00/I01 ->
  O05/O06 -> real AFE AWB DMA. This branch does not call the PMIC, ADDA,
  audio-pin mux or amplifier routines. It cannot tune or unmute the receiver.
* Both streams: interleaved S16_LE, stereo, 48000 Hz, read/write API only,
  buffer 8192..65536 bytes in 32-byte steps, periods 1920..16384 bytes in
  32-byte steps. No mmap, pause, resume or microphone stream is advertised.
  Playback and FM capture may coexist under this one AFE owner.
* `PCM Playback Volume`: integer 0..100, initial 30, linear signed PCM gain.
  Copy into a temporary CPU buffer, scale, then publish to DMA; unscaled user
  samples are never temporarily exposed in an active DMA buffer. Zero also
  clears all queued DMA PCM. The hardware FIFO may still contain a small tail;
  this is PCM mute, not an analogue power interlock. Other changes affect newly
  copied samples, with up to one application-buffer of queued old gain.
* `tablet-audio volume [up|down|0..100]` retains the deployed per-boot
  `/run/tablet-volume`, default 30%, +/-5 **percentage points**, bounds, corrupt
  state -> zero and flock-serialized updates. With this module present it also
  updates the named software ALSA control using amixer, under the same volume
  lock. No stream/DAC/amp is started. Failed amixer synchronization returns an
  error; the file retains the desired level for retry/startup.
* Existing side-button controller remains unchanged (including hold repeat).
  `tablet-audio play` uses ffmpeg -> aplay when the module exists; it never
  falls back to raw access after a card/playback error. Without the module it
  retains deployed ffmpeg/SRAM playback and PCM volume; initialization cleanup
  now covers partial codec/AFE/amp setup, and GPIO modes use SET/CLR aliases.

FM tuning/RDS remain solely in the existing, untouched fm-connsys receiver.
Its `fmradio/core/fm_config.c` selects I2S master/32k/internal-CONNSYS for a SoC
chip. The receiver must remain open while capturing. A muted receiver may
supply zero PCM: zero, missing clocks or an XRUN must not be called successful
broadcast-audio capture. No generated samples are returned by this driver.
A future approved digital stream may be consumed by arecord on device 1, or
piped to aplay device 0 **only after separate analogue-playback approval**.

## FM dashboard backend

`tablet-audio fm-check` checks module ownership, disabled playback, asserted
legacy exclusion, explicit `allow_capture=1`, the MT6755 capture endpoint, and an
available `arecord` without opening PCM. It reports unavailable when those
prerequisites are missing; idle loading alone does not opt the dashboard into PCM.
`tablet-audio fm-record SECONDS FILE|-` captures raw stereo S16_LE at 48 kHz for
1..300 integer seconds from device 1. It holds the hardware-admission flock
across exec into the recorder, so the caller owns and can stop the actual PID.
Neither command loads modules, tunes a receiver, or enables analogue outputs.

The helper is installed; the four-panel renderer/controller remain byte-identical.
An actual eye appearance retained all four panels and real muted RF samples,
then closed the receiver and restored the dark console. Genuine broadcast PCM,
capture termination/recovery, and displayed live audio metrics remain unverified.

## Ownership and approval boundary

`legacy_quiesced` defaults false, is read-only while loaded, and must be true
for module initialization. Binding is restricted to
`mediatek,k50v1-tablet`. The module reserves the AFE aperture for its whole
binding lifetime; playback-enabled binding also reserves the six evidenced
GPIO lines through gpiolib. One stream per direction is admitted, both sharing
one domain/AFE owner. A first open rejects already enabled foreign memifs.

The candidate helper checks `/sys/module/snd_soc_mt6755_tablet` **before**
opening `/dev/mem`. Even an idle card or failed probe blocks raw start, amp,
stop until explicit module unload. `status` instead reads the owner's read-only
`analogue_status` platform attribute: real idle SPM/AFE fields and serialized PMIC
reads, with errors/busy state propagated and no userspace `/dev/mem` access. The
renderer retains its independent amplifier-pin checks. The helper holds
`/run/tablet-audio-hardware.lock` for a whole hardware invocation. An approved
administrator must hold that same flock while loading/unloading the module,
with old unguarded deployed processes stopped and persistent raw `start`
state quiesced first. This admission protocol closes the helper/load race.
Merely setting `legacy_quiesced=1` is not proof of that precondition.

`allow_playback` defaults false, is read-only while loaded, and rejects playback
open with EPERM. With it false, the module does not even map/request the
speaker GPIOs. Digital FM capture has no codec/amp enable path. These parameters
are not authorization to execute hardware operations.

`allow_capture` also defaults false and is immutable while loaded. Capture open
returns EPERM before power/clock/DMA setup unless it is explicitly enabled in a
separately approved load. The deployed idle configuration keeps both stream
permissions false; endpoint enumeration does not authorise automatic capture.

This cooperative guard cannot stop arbitrary root programs using `/dev/mem`
(the running kernel has STRICT_DEVMEM disabled), old copies of the helper, or
an administrator bypassing the load-lock protocol. Replace/retire those copies
before deployment. Do not claim kernel resource reservation alone blocks them.

## Source audit and reuse decision

Inspected the actual prepared kernel at
`$KERNEL_SOURCE`, HEAD
`976d4857a991174852ef3423262619723781d86e`:

* `sound/soc/mediatek/mt6797/{mt6797-afe-pcm.c,mt6797-reg.h}`: DL2/AWB base,
  current, end and rate fields overlap, but HD format is at `0x03f8` (DL2
  shift 4, AWB shift 8), unlike this BSP's `0x03d8` shifts 18/20. Its routing
  mixers also use different connection registers. It is not register-compatible.
* `sound/soc/mediatek/mt8365/{mt8365-afe-pcm.c,mt8365-reg.h}`: closest memif
  match (DL2 enable 2, AWB enable 6; format at `0x03d8`), but its AWB high
  address selection is MEMIF_MSB bit 17, versus this BSP's bit 3. Its probe
  requires MT8365 clock topology/runtime PM and its compatible is
  `mediatek,mt8365-afe-pcm`; this board has neither that consumer topology nor
  a reusable MT6353/board/CONNSYS-FM route. Pretending it is MT8365 would be
  unsafe. A future in-tree common-memif port remains possible, but adds more
  integration changes than this scoped standalone candidate permits.
* `include/sound/{soc.h,soc-component.h,pcm.h}` and `sound/soc/soc-ops.c`:
  used this tree's current component copy/iov_iter, hrtimer, mixer and DAI APIs,
  not obsolete BSP snd_soc_platform APIs. The actual builds caught obsolete
  kcontrol/gpio lookup names and a collision with maple-tree `mt_init`; fixed.
* `drivers/soc/mediatek/mt6755-pmic.c`: exact exports are
  `int mt6755_pmic_read(u16, u16 *)`,
  `int mt6755_pmic_write(u16, u16)`,
  `int mt6755_pmic_rmw(u16, u16 mask, int shift, u16 value)`.
  Codec writes use these serialized exports; there is no competing PWRAP MMIO.

BSP root `$BSP_SOURCE/`, subdirectories
`xsh-3.18.140/sound/soc/mediatek/mt6750/` and
`neffos-3.18.79/sound/soc/mediatek/mt6750/`:

* `mt_soc_pcm_fm_i2s_awb.c`: StartAudioFMI2SAWBHardware and stop sequence.
* `mt_soc_afe_control.c`: Set2ndI2SIn, SetI2SASRCConfig/Enable,
  SetSampleRate, SetMemoryPathEnable, SetMemIfFetchFormatPerSample,
  SetHighAddr, SetI2SDacOut/SetDLSrc2/CleanPreDistortion.
* `mt_soc_afe_connection.c`: I00/O05 bit 16 and I01/O06 bit 22 in CONN2;
  masks for all alternate/shifted O05/O06 inputs are cleared before capture.
* `AudDrv_Afe.h`: AFE/AWB/DL2/ASRC offsets. `AudDrv_Clk.c`: AFE/I2S clock
  gates and EMI idle exclusion (not a second capture data producer).
* `mt_soc_pcm_dl2.c`: coherent DRAM DL2 path, base/end programming.
* `mt_soc_codec_6353.c` plus the byte-exact deployed helper snapshot:
  known speaker codec/AW8736 routing and sequencing. The original deployed
  helper SHA256 is
  `dfdc0505f25c44f68acc9173f7aab1d1bc14ea2f888701e64b2938a3a0d19d15`.

Operator-local comparison receipts record the primary BSP file hashes.
FM PCM source, connection tables and register headers are
byte-identical across the two BSPs; the three FM configuration functions above
are also byte-identical despite unrelated control-file differences.

## Executed offline build and tests

The offline recipe requires explicit, operator-local inputs:

```sh
KERNEL_SOURCE=/path/to/linux KERNEL_OUTPUT=/path/to/prepared-output \
BUILD_OUTPUT=/path/to/separate-audio-build \
PWRKEY_SNAPSHOT=/path/to/deployed/tablet-pwrkey \
VOLUME_TESTS=/path/to/button-volume/test_volume.py \
sh tools/audio/build-offline.sh
```

Use a separate owned build directory, never the original prepared output.
The separately maintained button/volume suite and its matching controller
snapshot are required inputs; the recipe does not skip that regression suite.

The script copies `$KERNEL_OUTPUT` with `cp -a --reflink=auto` only if the owned
`$BUILD_OUTPUT/kernel-out` does not exist. Generated configuration, objects,
and logs stay in the separate build directory.
It sets SOUND/SND/SND_PCM/SND_TIMER/SND_SOC to modules, resolves olddefconfig,
runs modules_prepare, builds real sound-core modules with `M=linux/sound` and
`MO=audio/sound-modules`, then builds this module with its own MO directory and
`KBUILD_EXTRA_SYMBOLS=audio/sound-modules/Module.symvers`. Every make uses `-j2`.
SOUND must also be modular: the initial SOUND=y attempt correctly failed
modpost on the absent built-in `sound_class`; no fabricated symbol exports or
forced unresolved-symbol success were used.

Results: complete AArch64 module link + modpost with `-Wall -Werror`; five real
support modules (`soundcore`, `snd`, `snd-timer`, `snd-pcm`, `snd-soc-core`).
Compiler: aarch64-linux-gnu-gcc Debian 16.2.0-3. All have vermagic
`7.3.1-mt6750 SMP preempt mod_unload aarch64`.

* `test_driver_offline.py`: extracts the actual callback bodies/tables from
  this C file, compiles a host executable with ASan + UBSan + warnings-as-errors,
  executes 1,376,256 signed gain cases, wrap/period calculations, missed-wrap
  and stalled-clock XRUNs, real callback start/stop/duplex/close behavior,
  short/invalid PCM copies, queued/new zero mute and **every one of 79 codec
  startup write failure points**. Cleanup continues after errors and latches
  a fault if output shutdown cannot be confirmed. Its FM prepare/start/stop/
  close/XRUN tests assert zero PMIC/GPIO/DAC-enable effects in memory stubs.
  Supply output directory and matching kernel source arguments; real pthread
  locks test duplex STOP/START, and extracted ALSA intervals test open refinement.
* `test_audio.py`: 19 helper tests covering FM backend prerequisites, bounded
  recorder exec and failure paths, guarded idle ownership, explicit
  ALSA selection, ordered mixer updates, invalid gain, decoder/sink setup
  failure, interruption, timeout kill/reap, cleanup errors and raw partial
  initialization cleanup, real-flock startup/mute ordering and a failed sink
  with a real host decoder child blocked on empty live stdin.
  Owner-status tests retain actual register values, reject unavailable status,
  and prove the helper never falls back to raw access. Extracted driver tests
  cover default-denied opens and read-only status/busy/PMIC-error paths.
* Original `volume-validation/test_volume.py`, unchanged, run against this
  helper plus the byte-exact deployed controller in an owned evidence fixture:
  all 20 volume/button/hold/debounce/controller tests pass.

The source-extracted tests replace MMIO/PMIC and ALSA orchestration with memory
stubs; SPM power polling is stubbed. Host locks do not prove kernel concurrency,
physical DMA coherency, clocking, pop behavior, GPIO pulse timing, firmware
reception or card registration. Those require independently approved tests.

Evidence directory `$BUILD_OUTPUT/`:

* `module-build.log`, `sound-modules-build.log`, `kernel-config.log`,
  `kernel-prepare.log`, `driver-tests.log`, `helper-tests.log`, `volume-tests.log`.
* `module-build/snd-soc-mt6755-tablet.ko`, `sound-modules/**/*.ko`.
* `host-tests/driver-callbacks-host{.c,}`: generated, explicitly offline host
  harness/executable, not a simulated broadcast recording.

## Limitations and next approval

**Idle-only runtime evidence.** The original running kernel has CONFIG_SOUND disabled and
MODVERSIONS disabled. The unchanged prepared config hash is
`d18cfea64d6f7fcbaea7d800ca5473b6c612a6051a3107f6c6e862d887ff0a45`;
this build has a different, explicitly recorded sound-enabled config.
Matching vermagic and successful modpost are not proof that these modules can
be loaded safely into the unchanged running kernel. The component-only build above did not produce a full kernel. Subsequent
parent integration built a coherent sound/media-enabled vmlinux, Image.gz, DTB,
all kernel modules, and CONNSYS/FM/CIFS/legacy-physbuf against that configuration.
The reviewed audio and camera modules were rebuilt against that coherent
output after their later source corrections. Earlier combined/per-component
receipts describe historical source snapshots, not the current bytes. No
flashable boot image was assembled. Earlier temporary trials restored the old
setup. The 2026-11-13 deployment then checked the unchanged source/configuration,
framework-private layout differences, and complete imported-symbol/dependency
closure before a bounded silent load. Card/endpoints/mixer, owner status,
ordinary unload/reload, and coexistence with the RF display passed. These results
do not establish PCM operation or general ABI compatibility on another kernel.
Modules/helpers are installed and idle-loaded; no boot-autoload policy was added.

The board lacks an audio genpd/CCF consumer in the current DTS, so this candidate
retains narrowly scoped BSP-derived AUDIO MTCMOS and AFE gates plus the
bootloader's existing parent clock setup. It is not a generic MT6755 clock/
power-domain driver. Playback still uses the known codec shutdown sequence
(which leaves upstream PMIC audio clock references as the deployed helper did),
not a new complete codec DAPM/regulator/runtime-PM implementation. No suspend/
resume, long-running DMA stability, thermal/power or audible acceptance exists.
The ALSA path is S16, unlike the raw fallback's 24-in-32 samples. DMA/ASRC input
clocking and actual RDS-preserving digital audio remain unverified on silicon.

No microphone or headphone-jack detection/playback capability is implemented
or promised. HPL/HPR in the speaker sequence is evidence for the AW8736 feed,
not proof of a fitted usable headphone jack. Wiring, mic source/type/bias,
ACCDET/jack polarity and headphone protection remain blockers pending separate
board evidence. No guessed microphone/jack register writes were added.

Next ask is **not permission to enable sound**: obtain explicit authorization
for a bounded **digital-only** FM DMA test, under the admission lock, with
allow_playback=0 and explicit allow_capture=1
and independently established codec/amplifier-off state before/after. Keep WMT
and receiver/RDS ownership intact. Do not advertise captured broadcast PCM
until real changing hardware DMA and broadcast-derived samples are evidenced.
Analogue speaker testing needs a separate approval; mic/jack work needs wiring
before implementation. Rootfs deployment and idle loading are now verified;
persistent loading/services, common build integration, and kernel/DT changes
remain separate actions. A separately authorised recovery
reboot restored the original setup after the camera trial; it was not a
persistence test. This source checkpoint does not authorise hardware activation.
