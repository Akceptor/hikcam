import threading
import os

import numpy as np

FORMATS = {
    1: {1: (256, 392), 2: (256, 192), 3: (256, 196), 4: (256, 400),
        5: (256, 196), 6: (256, 200), 7: (192, 520), 8: (192, 400)},
    2: {1: (120, 160), 2: (240, 320), 3: (640, 360)},
}
YUYV_SIZES = {w * h * 2: (w, h) for fr in FORMATS.values() for (w, h) in fr.values()}
DEFAULT_SIZE = (256, 192)


def y_plane(raw):
    return np.frombuffer(raw, np.uint8)[0::2].astype(np.int16)


class PipeSource:
    """Drains a pipe on a thread so the root capture process never blocks on us."""

    def __init__(self, fd):
        self.fd = fd
        self.buf = bytearray()
        self.cv = threading.Condition()
        self.eof = False
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            while True:
                chunk = os.read(self.fd, 1 << 16)
                if not chunk:
                    break
                with self.cv:
                    self.buf += chunk
                    self.cv.notify_all()
        except OSError:
            pass
        with self.cv:
            self.eof = True
            self.cv.notify_all()

    def take(self, n, timeout=None):
        with self.cv:
            if not self.cv.wait_for(lambda: len(self.buf) >= n or self.eof, timeout):
                raise TimeoutError
            if len(self.buf) < n:
                raise EOFError
            out = bytes(self.buf[:n])
            del self.buf[:n]
            return out

    def trim_backlog(self, unit, keep):
        # Only whole frame periods are discarded so the frame phase is preserved.
        with self.cv:
            extra = (len(self.buf) // unit - keep) * unit
            if extra > 0:
                del self.buf[:extra]


class BufferSource:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def take(self, n, timeout=None):
        if self.pos + n > len(self.data):
            raise EOFError
        out = bytes(self.data[self.pos:self.pos + n])
        self.pos += n
        return out

    def trim_backlog(self, unit, keep):
        pass


def find_period(a):
    scores = {}
    for size in YUYV_SIZES:
        n = size // 2
        if len(a) >= 3 * n:
            scores[size] = np.abs(a[n:3 * n] - a[:2 * n]).mean()
    return min(scores, key=scores.get)


def detect_size(source):
    raw = source.take(4 * max(YUYV_SIZES))
    return YUYV_SIZES[find_period(y_plane(raw))]


def _col_profile(a, w, n):
    return np.median(np.abs(np.diff(a[:n + 1])).reshape(-1, w), axis=0)


def _row_profile(a, w, h):
    n = w * h
    return np.median(np.abs(a[w:w + n] - a[:n]).reshape(h, w), axis=1)


def _vote(values):
    vals, counts = np.unique(values, return_counts=True)
    i = counts.argmax()
    return int(vals[i]), int(counts[i])


def find_offset(a, w, h):
    """Pixel offset of the first true frame start in `a` (4 frame periods of Y), or None.

    A real wrap seam has a large neighbour difference in *every* row (or column), so
    the median across rows rejects natural edges that only cover part of the image.
    Three independent frames must agree.
    """
    n = w * h
    col, cnt = _vote([int(_col_profile(a[j * n:], w, n).argmax()) for j in range(3)])
    if cnt < 2:
        return None
    c = (col + 1) % w
    a2 = a[c:]
    row, cnt = _vote([int(_row_profile(a2[j * n:], w, h).argmax()) for j in range(3)])
    if cnt < 2:
        return None
    return ((row + 1) % h) * w + c


def misaligned(y, w, h):
    """Key of a seam that should not exist inside an aligned frame, or None."""
    f = y.astype(np.int16)
    flat = f.ravel()
    cols = np.median(np.abs(flat[1:(h - 1) * w + 1] - flat[:(h - 1) * w]).reshape(h - 1, w), axis=0)
    rows = np.median(np.abs(f[1:] - f[:-1]), axis=1)
    for name, prof, seam_ok in (("col", cols, w - 1), ("row", rows, None)):
        k = int(prof.argmax())
        if k != seam_ok and prof[k] >= 8 and prof[k] > 4 * (np.median(prof) + 1):
            return (name, k)
    return None


class FrameSync:
    CHECK_EVERY = 3
    BAD_LIMIT = 3
    COOLDOWN = 30

    def __init__(self, source, w, h, timeout=0.5):
        self.src, self.w, self.h, self.timeout = source, w, h, timeout
        self.period = w * h * 2
        self.pending = bytearray()
        self.frames = 0
        self.bad = 0
        self.cooldown = 0
        self.false_seams = set()
        self.last_key = None
        self.resyncs = 0

    def _take(self, n):
        need = n - len(self.pending)
        if need > 0:
            self.pending += self.src.take(need, self.timeout)
        out = bytes(self.pending[:n])
        del self.pending[:n]
        return out

    def calibrate(self, attempts=6):
        """Re-find the frame start in the live stream; returns the shift in pixels or None."""
        for _ in range(attempts):
            raw = self._take(4 * self.period)
            off = find_offset(y_plane(raw), self.w, self.h)
            if off is not None:
                self.pending[:0] = raw[off * 2:]
                return off
        return None

    def nudge(self, px):
        if px > 0:
            self._take(px * 2)
        elif px < 0:
            self.pending[:0] = bytes(-px * 2)

    def _frame(self):
        raw = self._take(self.period)
        return np.frombuffer(raw, np.uint8)[0::2].reshape(self.h, self.w)

    def next_frame(self):
        self.src.trim_backlog(self.period, 8)
        y = self._frame()
        self.frames += 1
        self.cooldown = max(0, self.cooldown - 1)
        if self.frames % self.CHECK_EVERY == 0:
            key = misaligned(y, self.w, self.h)
            if key in self.false_seams:
                key = None
            self.bad = self.bad + 1 if key else 0
            if key:
                self.last_key = key
            if self.bad >= self.BAD_LIMIT and self.cooldown == 0:
                self.bad = 0
                off = self.calibrate()
                self.resyncs += 1
                # No shift means the seam is real scene content; stop flagging that exact spot.
                if not off:
                    self.false_seams.add(self.last_key)
                self.cooldown = self.COOLDOWN
                y = self._frame()
        return y
