"""Cheap motion gate to avoid running the detector on idle frames.

On a 24/7 runway camera the overwhelming majority of frames contain nothing
moving. Frame differencing on a downscaled grayscale image costs a fraction of
a millisecond, versus ~25-150 ms for a YOLO forward pass, so skipping idle
frames is where the real compute savings are.
"""

import cv2
import numpy as np


class MotionGate:
    """Reports whether enough pixels changed since the last checked frame.

    Args:
        threshold: fraction of changed pixels (0-1) required to call it motion.
        downscale_width: width the frame is shrunk to before differencing.
        pixel_delta: per-pixel intensity change counted as "changed".
        roi: optional (x1, y1, x2, y2) in full-frame coords to watch.
    """

    def __init__(self, threshold: float = 0.002, downscale_width: int = 160,
                 pixel_delta: int = 25, roi: tuple[int, int, int, int] | None = None):
        self.threshold = threshold
        self.downscale_width = downscale_width
        self.pixel_delta = pixel_delta
        self.roi = roi
        self._previous: np.ndarray | None = None

    def _prepare(self, frame: np.ndarray) -> np.ndarray:
        if self.roi is not None:
            x1, y1, x2, y2 = self.roi
            frame = frame[y1:y2, x1:x2]
        height, width = frame.shape[:2]
        if width > self.downscale_width:
            scale = self.downscale_width / width
            frame = cv2.resize(frame, (self.downscale_width, max(1, int(height * scale))),
                               interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return cv2.GaussianBlur(gray, (5, 5), 0)

    def changed_fraction(self, frame: np.ndarray) -> float:
        """Fraction of pixels that changed since the previous call (0-1).

        Returns 1.0 for the very first frame so callers treat it as active.
        """
        current = self._prepare(frame)
        if self._previous is None:
            self._previous = current
            return 1.0

        delta = cv2.absdiff(self._previous, current)
        self._previous = current
        return np.count_nonzero(delta > self.pixel_delta) / delta.size

    def has_motion(self, frame: np.ndarray) -> bool:
        return self.changed_fraction(frame) >= self.threshold
