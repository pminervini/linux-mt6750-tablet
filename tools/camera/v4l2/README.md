# MT6750 k50v1 V4L2 RAW candidate - idle deployment verified

The guarded driver and helper were installed on 2026-11-13. Both RAW nodes,
format/control queries, and media topology now enumerate; ordinary unload and
reload passed on the unchanged native kernel, with `allow_streaming=0` and
sensors unpowered. No real image or stream was captured. An earlier trial had
deadlocked QUERY_EXT_CTRL on shared ioctl/control locking and required an approved
recovery reboot; the separate-lock repair has now passed this idle runtime gate.

The bound platform-driver correction and core-compatible vb2 index limit build
and pass offline tests. Queue setup retains the total eight-buffer allocation
cap, including repeated CREATE_BUFS and partial-allocation checks. The control
handler now keeps its own mutex; S_FMT takes video/queue then control locks when
updating exposure limits. The control callback only updates atomic cached values
and never takes the queue or board-owner mutex. Matching-core query/ioctl and
production format/control bodies pass host tests with real nonrecursive locks;
reversing either repair fails its regression. The matching AArch64 module builds.
Keep the still helper/data as an unload fallback until capture acceptance.
Detailed trial receipts remain operator-local and are not included in this export.

## Design and exact interface

One GPL out-of-tree module, `mt6750_camera.ko`, registers a media device and two
V4L2 capture nodes (dynamic video numbers): `mt6750-rear-raw` and
`mt6750-front-raw`. Each immutable media path is
`sensor -> SENINFx-CAMSV0 exclusive -> video`. The two bridge entities represent
mutually exclusive configurations of **the same physical CAMSV0**, not two DMA
engines. A board-wide owner mutex admits only one STREAMON, including all shared
reset, power, TG1 clock and receiver programming. A failed stream keeps that
ownership until STREAMOFF/last close even though its worker powers down promptly.
There is no fabricated image producer and no bespoke character-device ABI.

| Path | Exact advertised sizes | Source timing registers | Exported buffer |
|---|---|---|---|
| rear OV8856, I2C2 0x36, 2 lanes CSI0/SENINF1 | 1632x1224; 3264x2448 | preview HTS=3744/VTS=1246; full HTS=1932/VTS=2482 | SBGGR10, LE16 |
| front GC2365, I2C2 0x37, 1 lane CSI1/SENINF2 | 1600x1200 | window 1208 + VB16 + overhead16 = VTS1240 | SBGGR10, LE16 |

No scaling/cropping, YUV, RGB or JPEG format is advertised. `TRY_FMT` chooses one
of these actual modes. `S_FMT` is rejected once vb2 buffers are allocated.
`bytesperline=width*2`, `sizeimage=width*height*2`, progressive BGGR, raw colour
space and no transfer function. The input orientation is not modified. The
legacy rear display flip is an optional **post-demosaic** software operation.
BGGR is the existing still helper's source-derived expectation, not a newly
measured Bayer-phase result. Rear preview/full and front each need independently
approved colour-target capture before their fourcc is accepted as board-validated.
Streaming remains disabled pending that controlled test plan.

The CAMSV DMA input is **not MIPI-packed RAW10**. It is a little-endian stream of
four consecutive 10-bit samples per five bytes, as decoded by the existing still
helper. The worker unpacks its private coherent staging buffer to genuine
`V4L2_PIX_FMT_SBGGR10` (16-bit LE words, high six bits zero). It never labels the
bytes `SBGGR10P`. This CPU conversion is intentional: standard userspace gets an
existing truthful format instead of an unsupported/falsely described fourcc.

V4L2 MMAP streaming uses vb2-vmalloc, two initially queued buffers, maximum eight,
with the vb2 core enforcing per-file queue ownership and mmap lifetime. DMA
never targets mapped userspace buffers. The worker waits for a queued buffer;
when starved it leaves CAMSV VF/DMA stopped while the sensor still runs. Each
buffer receives a new hardware capture with freshly programmed IMGO address,
FBC one-slot count/rearm, and explicit reset acknowledgement before conversion
and `vb2_buffer_done`. This repeats for as many queued buffers as supplied; it
is not a one-shot ioctl disguised as a streaming device. DMA is deliberately
rearmed frame-by-frame rather than using an unproven continuous IRQ ring.

`QBUF/DQBUF`, nonblocking operation, poll, STREAMON/STREAMOFF and last-close
cancellation are the standard vb2/V4L2 implementations. DMABUF import/export,
USERPTR, read(), media requests and simultaneous cameras are not advertised.
The worker polls raw PASS1_DONE every 0.5 ms, does not enable an unowned IRQ,
and has a 3-second frame deadline. A sensor/I2C error, CAMSV TG/DMA error, reset
timeout or cancelled frame is never delivered as valid image data. Completed
buffers have monotonic timestamps recorded at the polled PASS1_DONE observation
(before reset/unpack), sequential numbers and exact payload sizes.

Exposure controls are line counts (6 through fixed VTS-8); analogue gain uses
128 units per 1x for rear (128..1984), 64 for front (64..493). Controls are cached
while idle and applied by the capture worker, never by opening/enumerating the
node. They are not per-frame request controls; sensor exposure latency is not
measured. VTS stays fixed in this candidate; the old helper's longer-exposure
AE remains available in the unloaded legacy path.

**Timing is not a delivered-frame-rate guarantee.** Stock rear source declares
144 MHz line-timing clock; the front source declares 42 MHz/1123/1240 but also
`max_framerate=100` (10 fps), inconsistent with that clock arithmetic. Topck's
boot-established camtg parent and the still tables' TG1 divider are retained
(nominal sensor MCLK 24 MHz); no new PLL/parent choice is guessed. Reset/rearm,
I2C and CPU unpack can drop sensor frames and lower delivered throughput.
Accordingly there is no fabricated FPS enumeration or S_PARM promise: measure
actual DQBUF timestamp deltas in an approved capture. Sensor HTS is not blindly
exposed as pixel HBLANK (full rear HTS is smaller than its image width).

## Power, stop and coexistence

Registration maps/reserves resources and registers two dummy I2C clients to
reserve proven addresses. It performs **no sensor transfer, GPIO write, PMIC
write or domain-on operation**. The immutable module parameter `allow_streaming`
defaults false: STREAMON returns EPERM and returns queued buffers before any
pipeline admission, DMA allocation, worker creation or power operation. This
permits an idle enumeration trial even if another client attempts to stream.
Only a separately approved load with `allow_streaming=1` admits capture.
An admitted STREAMON allocates the staging DMA memory,
checks chip identity after controlled power-up and replays the existing tables.
The bus must be Linux adapter 2 backed by physical controller 0x11009000; it fails
closed if numbering/controller identity changes. There is no GPIO-bitbang I2C
fallback and no I2C scanning.

Shared rails VCAMIO/VCAMA/VCAMD use only exported
`mt6755_pmic_read/rmw` and their charger-shared lock. There is no competing pwrap
mapping/owner, AF rail operation, AUXADC operation, sound path or WMT interaction.
STREAMON refuses already-enabled camera supplies or an already-on ISP domain
rather than borrowing resources with an unknown owner. Camera GPIOs 107/108/110
and selected MCLK109/161 are manipulated only through SET/CLR aliases. Inactive
sensor is held in PDN and shared reset belongs to the board owner. I2C112/113 are
**not remuxed**. No GPIO mode word, including the reset/I2C2/CONSYS shared word,
is rewritten in full.

The currently supplied pinctrl's gpiolib mux path uses whole-word RMW. Rather
than call that unsafe path, this board-specific prototype uses guarded raw
pin aliases, snapshots/restores only its pins and reserves its camera MMIO
regions. **This is not complete gpiolib/regulator/genpd integration.** No other
kernel/raw-MMIO camera controller may coexist. General-purpose GPIO consumers
are not atomically excluded; proper upstream integration must first make the
board pinctrl safe and add GPIO/regulator/clock/genpd ownership. The explicit
`legacy_excluded=1` module parameter is an operator assertion, not a magical
kernel enforcement mechanism.

Every capture quiesces VF, CMOS and FBC, triggers IMGO reset, and requires its
acknowledgement before reusing staging memory. STREAMOFF wakes/stops/joins the
worker, which attempts sensor standby, asserts PDNs/reset, turns off MCLK,
powers off the ISP with SRAM/power acknowledgement, restores regulator fields
and pin state, and returns every owned vb2 buffer. Cleanup continues after
individual failures and poisons the board against another STREAMON. If neither
DMA reset nor domain-off acknowledgement proves a fence, private DMA staging
is **quarantined/leaked rather than freed**; user buffers are never DMA targets.
There is no unsafe buffer recycle fallback. Stop latency includes bounded reset
and ISP acknowledgement waits plus the I2C controller's own transfer timeout.

A registered PM notifier rejects suspend, hibernation and restore preparation
while a stream owns the board (including queue starvation and errored streams
awaiting STREAMOFF). Idle preparation gates a racing STREAMON until the matching
PM-post notification. This is an explicit refusal policy, not suspend/resume
support; actual system-PM behaviour has not been tested. A wakeup-source hold alone
would not establish exclusion for every explicit system-suspend request.

`tablet-camera` now obtains `/run/lock/tablet-camera.lock` before its existing
`/dev/mem` open and rejects `/sys/module/mt6750_camera` even while the candidate
is idle. Its entire former capture/development AST is tested unchanged after
removing the new guard. The guard is installed on the tablet. Every
loader **must hold the same flock across module loading**, and confirm the
installed helper is the guarded version; otherwise an already-running legacy
capture can race loading. Unguarded old experiment scripts/root /dev/mem access
remain outside this cooperative exclusion and must not be used. Unloading the
candidate releases the exclusion; the old still path/data are retained.

## Why this rather than a nominal upstream binding

Inspected the exact clean Linux 7.3.1 source
`976d4857a991174852ef3423262619723781d86e` before choosing the implementation:

* `drivers/media/i2c/ov8856.c` supports two/four lanes but its supplied clock is
  19.2 MHz and two-lane modes are 3280x2464 / 1640x1232 with default GRBG. These
  are not the already-demonstrated board's 24 MHz / 3264x2448 / 1632x1224 BGGR
  tables. Its independent regulator/reset runtime-PM model does not arbitrate
  this board's shared reset with GC2365. Binding it unchanged would substitute
  unvalidated modes and power ownership. This candidate instead preserves the
  stock known-board tables and uses V4L2's actual core interfaces.
* No GC2365 driver is in that tree's media/i2c. The existing front still table
  comes from the supplied xsh GPL BSP, not a guessed GC2145-compatible driver.
* `drivers/media/platform/mediatek/` contains jpeg/mdp/mdp3/vcodec/vpu, no SENINF
  or CAMSV capture bridge. The BayLibre MT8365 ISP3.0 v8 patch is useful for the
  matching CAMSV register layout/reset/FBC sequence, not an MT6750 compatible.
  Its normal 2-pixel/32-bit DMA settings are explicitly not copied: board uses
  one pixel/clock and 16-bit IMGO. The full patch was fetched read-only into
  `camera/upstream-camsv-v8.html` in evidence from:
  `https://lists.infradead.org/pipermail/linux-mediatek/2025-January/088709.html`.
  No claim is made that this mail patch is merged into the inspected tree.
  Its companion SENINF v8 patch (`088710.html` in the same archive, saved as
  `upstream-seninf-v8.html`) maps MT8365 CSI port 1 to SENINF3; this board's front
  recorded path is SENINF2. That mapping and its MT8365 PHY/clock integration
  must not be substituted for these exact SENINF/CSI dump tables. The standard
  LE16 format contract is also retained in evidence `v4l2-sbggr10.html` from
  `https://docs.kernel.org/userspace-api/media/v4l/pixfmt-srggb10.html`.

The preserved `../data/*` are authoritative inputs, never imported as Python
hardware code. SENINF/CSI analog values are the existing Android register-dump
subsets described in the parent README (`tools/regdiff/and-cam-*`, local/private).
No new live dumps were collected. Sensor lists derive from:

* `kernel/src/neffos-3.18.79/drivers/misc/mediatek/imgsensor/src/mt6755/ov8856_mipi_raw/ov8856mipiraw_Sensor.c`
* `kernel/src/xsh-3.18.140/drivers/misc/mediatek/imgsensor/src/mt6735m/gc2365_mipi_raw/gc2365mipi_Sensor.c`

`generate_tables.py` validates and embeds a SHA256 for every input. Ordered writes,
including bank changes/repeats, are retained. CAMSV constants and ISP/LDO sequence
trace directly to `../tablet-camera`'s `capture`, `power_up`, `power_down`,
`isp_power` and `ldo`. `cam-up.py` documents MCLK pin mode/boot parent; it was read,
not executed. The source-audit receipts remain operator-local.

### Autofocus investigation — no guessed actuator

The stock live DT/overlay has generic `camera_main_af@18` and
`camera_sub_af@0c`, not a proven fitted part identity or valid new probe address.
The supplied BSP's `lens/main/main_lens.c` selects the actual actuator through
userspace `AFIOC_S_SETDRVNAME`/`AF_SetMotorName`; the neffos tree enumerates many
motors, and xsh conditionally offers BU6424AF/DW9761AF. Those lists do not identify
this tablet's fitted motor. The existing guide names a rear focus supply VLDO28;
that does not establish an actuator model/protocol. No actuator client, address
probe, focus control, AF rail write or lens movement is implemented. Obtain the
exact-ROM userspace selected motor name / tuning configuration or equivalent
board-specific evidence first, then choose/port the matching V4L2 lens subdevice.

## Reproduce offline build and tests

From this directory, provide the matching kernel and prepared output:

```sh
KERNEL_SOURCE=/path/to/linux KERNEL_OUTPUT=/path/to/prepared-output \
LEGACY_CAMERA_SNAPSHOT=/path/to/preserved-unguarded/tablet-camera \
BUILD_OUTPUT=/path/to/separate-camera-build sh build-offline.sh
```

Use a separate owned build directory, never the original prepared output.
The preserved legacy helper is an explicit input for the AST-preservation test;
the public export does not require access to private Git history.
The original offline build recipe was executed before the later idle trials.
It checks the exact clean source HEAD and original prepared
config SHA256 `d18cfea64d6f7fcbaea7d800ca5473b6c612a6051a3107f6c6e862d887ff0a45`,
copies the prepared output into its own directory before configuration, builds
`vmlinux Image.gz dtbs modules` and the external module with **-j2**, runs W=1 module compilation
and all offline tests. Neither original Linux source nor `mainline/out` nor the
common `build.sh` is modified. Generated tables and compiler products stay out
of the worktree at:

```
$BUILD_OUTPUT/
  out/                     isolated media-enabled kernel/modules build
  module/mt6750_camera.ko   actual AArch64 candidate
  module/board-tables.h    generated tables with per-input SHA256
  configure.log, kernel-build.log, module-build.log, config-diff.txt
  tests.log, tests/tests.json
```

**The running #69 configuration has MEDIA_SUPPORT disabled.** The build changes
it to modular media/V4L2/vb2 plus MEDIA_CONTROLLER, and builds the whole vmlinux
and media dependencies together. Its release/vermagic is still
`7.3.1-mt6750 SMP preempt mod_unload aarch64`, which is **not ABI proof** with
MODVERSIONS=n. Do not load this candidate/dependency set into #69 on the basis
of vermagic. Parent integration must review configuration/layout differences
and use an approved coherent kernel/modules set or independently establish
binary compatibility. The build does not assemble a flashable boot image or
install anything. Offline integration built the coherent sound/media kernel,
all kernel modules, CONNSYS/FM/CIFS, and retained physbuf, then rebuilt the
reviewed candidates after later source corrections. These historical builds
do not themselves override earlier failed trials. Detailed hardware
trial receipts and source/artifact manifests remain operator-local.
The copied prepared `out/modules/` install-staging directory is not refreshed by
this build and must not be deployed. Fresh media dependencies are the individual
`.ko` products under `out/drivers/media/`; collect their hashes separately.

Tests: five ASan/UBSan C mock executables exercise shared production DMA/FBC/reset,
RAW10 unpack, bounded FIFO, and extracted production lifecycle/cleanup/GPIO
functions and the matching-core ioctl/control lock path. Ten Python tests cover exact table dimensions/timing/bounds, corrupt
tables, format conversion, old-helper AST preservation, mocked guard failures
and real flock contention on an offline temporary file. A saved synthetic RAW
file is processed by the real new CLI and its PNG signature checked. These are
**not real vb2-core, V4L2-compliance, I2C, sensor, DMA, FPS, suspend, AF or app
acceptance tests**. No hardware result is inferred from the mocked pass count.

## Saved-frame software integration

`../raw-develop.py` only reads saved files, never a device. It accepts truthful
SBGGR10 LE16 output or the old MediaTek packing, validates exact frame length and
high bits, performs software bilinear demosaic/grey-world AWB/gamma, and uses
front/rear black levels separately. It does not import the unsafe capture helper.
For an already saved frame (not a capture instruction):

```sh
python3 -B ../raw-develop.py saved.raw developed.png --width 1632 --height 1224 --format sbggr10 --flip-vertical
```

Ordinary applications that only accept processed YUV/RGB/JPEG will still need an
appropriate raw/software camera pipeline. This is not a libcamera integration,
hardware ISP or promise that every standard webcam app will accept RAW10.

## Remaining capture and persistence gates

The 2026-11-13 silent deployment passed real format/control queries, media
topology, ordinary unload, and reload after source/configuration/layout/import
checks. Both nodes remain idle-loaded with streaming disabled. The first checker
mistook a media directory for a device; guarded rollback unloaded the stack and
restored the helpers, then the corrected character-device check passed. Neither
that checker error nor earlier driver failures are hidden as successful trials.
This establishes the bounded idle gate, not capture or universal ABI compatibility.

1. Review this source, generated tables, full config diff, ABI plan, resource
   assumptions and failure/quarantine behaviour. Retain the exact deployed old
   helper and data; arrange recovery independently. No automatic service/startup
   changes, force-loading, flashing or reboot is authorised by this patch.
2. The reviewed idle helper/module installation and load are verified. For
   another approved load, the
   loader must acquire `/run/lock/tablet-camera.lock`, verify no old helper or
   physbuf/raw controller is active, load media dependencies and load the
   candidate with `legacy_excluded=1 allow_streaming=0` while still holding the lock. Do not use
   the parameter as a substitute for these checks. Default omission refuses load.
3. Separately approve a bounded first capture, including reload with `allow_streaming=1`.
   First inspect the registered media
   topology/format lists (these do not power sensors in this design), then test
   each sensor and each listed size one at a time. Validate chip identity,
   non-stale changing raw payload, exact length/packing/Bayer order, timestamps,
   STREAMOFF and forced application-close cleanup with independent state checks.
   A failure must leave the legacy fallback preserved; do not relax safety gates
   to make a demo pass.
4. **Sustained streaming requires a separate explicit approval**, then use a
   bounded duration/frame budget to measure delivered cadence, frame integrity,
   queue starvation/requeue, two-process EBUSY, timeout/disconnect, cancellation,
   CPU/memory use and cleanup. Never infer this milestone from one saved frame
   or the synthetic 384-frame test. V4L2-compliance and actual app acceptance,
   raw-software processing integration and then any persistence are later gates.
