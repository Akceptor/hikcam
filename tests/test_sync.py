import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from hikcam_core import BufferSource, FrameSync, find_period

W, H, N = 256, 192, 140
CHUNK = 2500


def make_frames(rng):
    yy, xx = np.mgrid[0:H, 0:W]
    base = 20 + 0.7 * xx + 0.5 * yy
    for _ in range(6):
        cx, cy, r = rng.integers(0, W), rng.integers(0, H), rng.integers(10, 40)
        base += rng.integers(20, 60) * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * r * r))
    base[:, 90:] += 25
    base[120:, :100] -= 20
    frames = []
    for i in range(N):
        f = base + 3 * np.sin(i / 7) + rng.normal(0, 1.5, base.shape)
        frames.append(np.clip(f, 0, 255).astype(np.uint8))
    return frames


def yuy2(frames):
    out = np.full((len(frames), H, W, 2), 128, np.uint8)
    for i, f in enumerate(frames):
        out[i, :, :, 0] = f
    return out.tobytes()


def run(seed, drop):
    rng = np.random.default_rng(seed)
    frames = make_frames(rng)
    truth = {f.tobytes(): i for i, f in enumerate(frames)}
    off = int(rng.choice([0, 1, W - 1, W, 5 * W + 17, int(rng.integers(0, W * H))]))
    stream = bytearray(yuy2(frames)[off * 2:])
    if drop:
        k = len(stream) // CHUNK // 2
        del stream[k * CHUNK:(k + 1) * CHUNK]
    sync = FrameSync(BufferSource(stream), W, H)
    shift = sync.calibrate()
    good, bad_after_lock, last_bad = 0, 0, -1
    n = 0
    while True:
        try:
            y = sync.next_frame()
        except EOFError:
            break
        ok = y.tobytes() in truth
        good += ok
        if not ok:
            bad_after_lock += 1
            last_bad = n
        n += 1
    return off, shift, n, bad_after_lock, last_bad, sync.resyncs


def main():
    fails = 0
    rng = np.random.default_rng(0)
    f = make_frames(rng)
    a = np.frombuffer(yuy2(f[:3] * 2), np.uint8)[0::2].astype(np.int16)
    print("find_period 256x192:", find_period(a) == W * H * 2)

    print("--- clean stream, random start phase: every frame must be exact")
    for seed in range(25):
        off, shift, n, bad, last, rs = run(seed, drop=False)
        ok = bad == 0 and shift == (-off) % (W * H)
        fails += not ok
        print(f"seed {seed:2d} start_off={off:6d} found={shift} frames={n} bad={bad} resyncs={rs} {'ok' if ok else 'FAIL'}")

    print("--- dropped 2500 B chunk mid-stream: must recover and stay locked")
    for seed in range(100, 112):
        off, shift, n, bad, last, rs = run(seed, drop=True)
        tail_clean = last < n - 40
        ok = rs >= 1 and bad <= 25 and tail_clean
        fails += not ok
        print(f"seed {seed} start_off={off:6d} frames={n} bad_frames={bad} last_bad_idx={last} resyncs={rs} {'ok' if ok else 'FAIL'}")
    print("FAILED" if fails else "ALL PASSED", fails)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
