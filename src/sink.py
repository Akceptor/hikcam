import argparse
import os
import sys

# -I (isolated mode) removes the script directory from sys.path.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cv2
import numpy as np

from hikcam_core import DEFAULT_SIZE, FrameSync, PipeSource, detect_size

WIN = "HikCamera"


def log(msg):
    print(f"[hikcam] {msg}", file=sys.stderr, flush=True)


def render(y, scale):
    if y.shape[0] > 300:
        y = y[: y.shape[0] // 2]
    img = cv2.applyColorMap(cv2.normalize(y, None, 0, 255, cv2.NORM_MINMAX), cv2.COLORMAP_INFERNO)
    return cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vcam", action="store_true", help="feed the OBS virtual camera")
    ap.add_argument("--no-preview", action="store_true", help="no window (Ctrl+C to stop)")
    ap.add_argument("--size", default="256x192", help="WxH of the camera frame, or 'auto'")
    ap.add_argument("--scale", type=int, default=3)
    ap.add_argument("--fps", type=int, default=25)
    args = ap.parse_args()
    preview = not args.no_preview
    if sys.stdin.isatty():
        log("expects the capture stream on stdin; run via the hikcam wrapper")
        return 2

    src = PipeSource(0)
    cam = None
    try:
        log("waiting for camera data...")
        if args.size == "auto":
            w, h = detect_size(src)
        else:
            w, h = map(int, args.size.split("x"))
        sync = FrameSync(src, w, h, timeout=30)
        sync.calibrate()
        sync.timeout = 0.5
        log(f"frame {w}x{h} locked")
        seen = 0
        while True:
            try:
                y = sync.next_frame()
            except TimeoutError:
                if preview and cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                    break
                continue
            if sync.resyncs != seen:
                seen = sync.resyncs
                log(f"auto-resync #{seen}")
            img = render(y, args.scale)
            if args.vcam:
                if cam is None:
                    import pyvirtualcam
                    cam = pyvirtualcam.Camera(width=img.shape[1], height=img.shape[0], fps=args.fps,
                                              fmt=pyvirtualcam.PixelFormat.BGR, backend="obs")
                    log(f"virtual camera: {cam.device}")
                cam.send(img)
            if preview:
                cv2.imshow(WIN, img)
                k = cv2.waitKey(1) & 0xFF
                if k in (ord("q"), 27) or cv2.getWindowProperty(WIN, cv2.WND_PROP_VISIBLE) < 1:
                    break
                if k == ord("s"):
                    sync.timeout = 30
                    sync.calibrate()
                    sync.timeout = 0.5
                shift = {ord("d"): 1, ord("a"): -1, ord("x"): w, ord("w"): -w}.get(k)
                if shift:
                    sync.nudge(shift)
    except EOFError:
        log("capture ended")
    except KeyboardInterrupt:
        pass
    finally:
        if cam is not None:
            cam.close()
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main())
