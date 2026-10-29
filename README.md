# hikcam

Live view and virtual webcam on macOS for HIKMICRO USB thermal cameras
(USB ID `2bdf:0102`, reports itself as `HikCamera` / `UVC Camera VendorID_11231 ProductID_258`).

The camera shows up in System Information and in apps' camera lists, but on macOS the
feed stays black. The same camera works on Android with any USB webcam app. This tool
talks to it directly over USB (like those Android apps do via libuvc) and shows the
thermal image in a window and/or as an OBS virtual camera for Zoom, Meet, Teams, etc.

## Why the stock driver fails

Dumping the camera's UVC descriptors shows:

| Format | Sizes | Notes |
|---|---|---|
| YUY2 (format 1) | 256x392, 256x192, 256x196, 256x400, 256x200, 192x520, 192x400 | the real data |
| MJPEG (format 2) | 120x160, 240x320, 640x360 | advertised only |
| H264 frame-based (format 3) | 240x320 | advertised only |

- macOS only offers the MJPEG/H264 modes, and the camera never streams them. When asked
  for MJPEG it silently switches to **YUY2 256x192** (`camera says fmt=1 frame=2`).
- Video comes over a **bulk** endpoint (`0x81`, 8192-byte payloads, 2502 bytes per read).
- The UVC payload header **never sets EOF and never toggles FID**, so there is no frame
  boundary marker at all. Frames straddle payloads (98304 B/frame vs 2500 B/payload).

hikcam therefore strips headers, treats the data as a continuous YUY2 byte stream, and
recovers frame alignment from the image itself:

- **Phase**: a misaligned frame has a wrap seam — a column where every row jumps, and a
  row where the bottom of one frame meets the top of the next. Per-column/per-row
  *medians* of neighbour differences find these while ignoring natural scene edges.
  Three consecutive frames must agree.
- **Auto-resync**: every few frames it re-checks for a seam; after several bad checks it
  recalibrates (USB data loss shifts the phase).
- Only the Y (luma) channel is used — it carries the thermal intensity — and is rendered
  with an Inferno colormap.

## Requirements

- macOS on Apple Silicon (Homebrew in `/opt/homebrew`), Python 3.10+
- `libusb` (`brew install libusb`, done by `install.sh`)
- For the virtual camera: [OBS Studio](https://obsproject.com) (`brew install --cask obs`)
- `sudo` — detaching the macOS kernel driver from the camera requires root

## Install

```sh
git clone https://github.com/Akceptor/hikcam.git
cd hikcam
./install.sh          # creates .venv, symlinks bin/hikcam into ~/.local/bin
```

### One-time OBS setup (virtual camera only)

1. Open OBS and click **Start Virtual Camera**.
2. Approve the system extension in **System Settings → General → Login Items &
   Extensions → Camera Extensions** (or Privacy & Security).
3. Stop the virtual camera and quit OBS. OBS doesn't need to run afterwards.

## Usage

Run from a normal Terminal (it asks for your password):

```sh
hikcam                         # preview window
hikcam --vcam                  # OBS virtual camera + preview
hikcam --vcam --no-preview     # virtual camera only, Ctrl+C to stop
hikcam --size auto --scale 4   # detect frame size, upscale 4x
```

Then pick **OBS Virtual Camera** in your video app.

Preview window keys:

| Key | Action |
|---|---|
| `s` | resync frame alignment |
| `a` / `d` | shift 1 pixel left / right |
| `w` / `x` | shift 1 row up / down |
| `q` / Esc | quit |

## How it's split

```
sudo python src/capture.py  |  python src/sink.py [--vcam]
   (root: USB, raw bytes)        (user: sync, render, window, virtual cam)
```

- `src/capture.py` — runs as root. UVC probe/commit, bulk reads, strips payload headers,
  writes raw bytes to stdout. Nothing else runs as root.
- `src/sink.py` — runs as you. Frame sync, rendering, preview window, OBS output.
  The OBS virtual camera and GUI windows don't work from a root process.
- `src/hikcam_core.py` — period/phase detection and auto-resync.

## Tests

Offline test with synthetic YUY2 streams (random start phase, dropped chunks):

```sh
.venv/bin/python tests/test_sync.py
```

## Limitations

- Scenes with a strong, perfectly straight edge spanning the whole frame width or
  height can fool the seam detector. Press `s` or nudge with `a`/`d`/`w`/`x`.
- Only the 256x192 thermal image is shown; the camera's raw temperature data modes
  (e.g. 256x392) aren't decoded into temperatures.
- `capture.py` runs as root from wherever you cloned the repo. Keep the clone in a
  directory only you can write to.

## License

MIT
