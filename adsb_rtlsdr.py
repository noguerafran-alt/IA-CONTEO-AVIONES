"""Read ADS-B directly from an RTL-SDR dongle: no separate GUI decoder needed.

This is the no-extra-software path. `rtl_adsb.exe` (from the official RTL-SDR
Blog release -- the one INSTALAR-ADSB.bat fetches, which bundles the drivers
the V4 dongle actually needs) does only one job: turn radio into raw AVR hex
lines on stdout, one per received message, like:

    *8D4840D6202CC371C32CE0576098;

Everything past that -- turning those hex messages into an aircraft's icao,
callsign, altitude, speed and position -- is decoded in Python by pyModeS's
PipeDecoder, which is stateful: a single message rarely carries a full
picture (position needs two paired CPR frames, for instance), so it holds
recent messages per aircraft and fills in what it can as they arrive.

Why this instead of RTL1090 or another GUI tool: those are a second program to
install, configure and keep running, each expecting its own port for the
BaseStation feed (adsb_sbs.py has to guess which). This needs only the single
official binary plus a Python decoder already in requirements.txt.

Uso:
  python adsb_rtlsdr.py --watch          escuchar y mostrar en vivo
  python adsb_rtlsdr.py --exe ruta.exe   si rtl_adsb.exe no esta en tools/
"""
from __future__ import annotations

import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import pyModeS as pms

from adsb import DEFAULT_HISTORY_S, Observation

DEFAULT_EXE = Path(__file__).parent / "tools" / "rtlsdr" / "rtl_adsb.exe"
# *hex...; with optional leading @timestamp, both formats rtl_adsb can emit.
AVR_LINE_RE = re.compile(r"^(?:@[0-9A-Fa-f]+)?\*([0-9A-Fa-f]+);?$")


def parse_avr_line(line: str) -> str | None:
    """Extract the raw hex message from one AVR-format line, or None."""
    match = AVR_LINE_RE.match(line.strip())
    return match.group(1) if match else None


def decoded_to_observation(icao24: str, decoded: dict, timestamp: float) -> Observation:
    """Merge one PipeDecoder result into an Observation.

    PipeDecoder tracks state per aircraft internally, but each call only
    returns what THIS message updated -- so decoded here is a partial view,
    same as one SBS-1 line. That is fine: the rolling history in AdsbRecorder/
    SbsRecorder/here already handles partial Observations by accumulating them
    over time per aircraft, and match_adsb.py treats missing fields as
    "unknown" rather than requiring a complete record.
    """
    altitude = decoded.get("altitude")
    on_ground = decoded.get("altitude") == 0 or decoded.get("vr_source") == "ground"
    return Observation(
        timestamp=timestamp,
        icao24=icao24,
        callsign=(decoded.get("callsign") or "").strip() or None,
        altitude_ft=float(altitude) if altitude is not None else (0.0 if on_ground else None),
        ground_speed_kt=(float(decoded["groundspeed"]) if "groundspeed" in decoded
                        else (float(decoded["ias"]) if "ias" in decoded else None)),
        vertical_rate_fpm=(float(decoded["vertical_rate"]) if "vertical_rate" in decoded else None),
        latitude=decoded.get("lat"),
        longitude=decoded.get("lon"),
        registration=None,   # raw ADS-B carries ICAO24, not the tail number
    )


@dataclass
class RtlAdsbRecorder:
    """Runs rtl_adsb.exe as a subprocess and decodes its output with pyModeS.

    Mirrors AdsbRecorder/SbsRecorder's public surface (start/stop/around/
    snapshot/is_receiving/last_error/poll_count) so match_adsb.py and the
    rest of the pipeline do not need to know which physical source feeds them.
    """
    exe_path: Path = DEFAULT_EXE
    device_index: int = 0
    history_seconds: float = DEFAULT_HISTORY_S
    restart_delay: float = 3.0
    local_ref_window: float = 30.0

    _history: list[Observation] = field(default_factory=list, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _stop: threading.Event = field(default_factory=threading.Event, repr=False)
    _thread: threading.Thread | None = field(default=None, repr=False)
    _process: subprocess.Popen | None = field(default=None, repr=False)
    _decoder: "pms.PipeDecoder | None" = field(default=None, repr=False)
    last_error: str | None = field(default=None, repr=False)
    poll_count: int = 0     # messages successfully decoded (CRC valid)
    corrupt_count: int = 0  # messages with a bad checksum, discarded
    waiting_for_device: bool = field(default=False, repr=False)

    def start(self) -> "RtlAdsbRecorder":
        if self._thread and self._thread.is_alive():
            return self
        # Resolved to absolute: a relative path here was observed to make
        # CreateProcess intermittently report "file not found" even though
        # the file exists, depending on the working directory the launcher
        # (a .bat, a different shell) started from. Absolute sidesteps that.
        self.exe_path = Path(self.exe_path).resolve()
        if not self.exe_path.exists():
            self.last_error = f"no se encontro {self.exe_path}"
            return self
        self._decoder = pms.PipeDecoder(local_ref_window=self.local_ref_window)
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._process:
            self._process.terminate()
        if self._thread:
            self._thread.join(timeout=3)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._run_once()
            except Exception as exc:
                # A dongle unplugged, a driver hiccup, rtl_adsb crashing: log
                # and retry rather than dying, since the camera-side counting
                # keeps running regardless and should not be starved by a
                # flaky receiver.
                self.last_error = f"{type(exc).__name__}: {exc}"
            self._stop.wait(self.restart_delay)

    def _run_once(self) -> None:
        # stderr is captured, not discarded: rtl_adsb prints its actual reason
        # for exiting there (no device, wrong driver, device busy), and that
        # text is what turns "exited with code 1" into a message someone can
        # act on instead of a bare error code.
        self._process = subprocess.Popen(
            [str(self.exe_path), "-d", str(self.device_index)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
        self.last_error = None
        self.waiting_for_device = False
        try:
            for line in self._process.stdout:
                if self._stop.is_set():
                    break
                self._handle_line(line)
            code = self._process.wait(timeout=2)
            stderr_text = (self._process.stderr.read() or "").strip()
            if code not in (0, None) and not self._stop.is_set():
                message = self._describe_exit(code, stderr_text)
                self.waiting_for_device = message is None
                if message is not None:
                    self.last_error = message
                    raise RuntimeError(message)
        finally:
            if self._process.poll() is None:
                self._process.terminate()

    @staticmethod
    def _describe_exit(code: int, stderr_text: str) -> str | None:
        """Turn rtl_adsb's own stderr into a message that says what to do.

        Returns None for "no device connected" specifically: with no antenna
        plugged in yet, that is not a malfunction, it is the honest current
        state, and reporting it as a RuntimeError makes a normal situation
        read like a crash.
        """
        lowered = stderr_text.lower()
        if "no supported devices" in lowered:
            return None
        if "usb_claim_interface" in lowered or "already in use" in lowered:
            return ("el dongle esta en uso por otro programa (SDR#, RTL1090, etc). "
                    "Cerralo y volve a intentar.")
        if "error accessing" in lowered or "libusb" in lowered:
            return ("no se pudo abrir el dongle. Si es la primera vez, instala el "
                    "driver WinUSB con Zadig (ver INSTALAR-ADSB.bat).")
        detail = f": {stderr_text}" if stderr_text else ""
        return f"rtl_adsb.exe termino con codigo {code}{detail}"

    def _handle_line(self, line: str) -> None:
        hex_message = parse_avr_line(line)
        if not hex_message:
            return
        decoded = self._decoder.decode(hex_message, timestamp=time.time())
        if not decoded.get("crc_valid"):
            self.corrupt_count += 1
            return
        icao24 = decoded.get("icao")
        if not icao24:
            return
        self.poll_count += 1
        self.add(decoded_to_observation(icao24.lower(), decoded, time.time()))

    def add(self, observation: Observation) -> None:
        with self._lock:
            self._history.append(observation)
            self._prune()

    def _prune(self) -> None:
        if not self._history:
            return
        cutoff = max(o.timestamp for o in self._history) - self.history_seconds
        self._history = [o for o in self._history if o.timestamp >= cutoff]

    def around(self, moment: float, window_s: float = 60.0) -> list[Observation]:
        with self._lock:
            near = [o for o in self._history if abs(o.timestamp - moment) <= window_s]
        return sorted(near, key=lambda o: abs(o.timestamp - moment))

    def snapshot(self) -> list[Observation]:
        with self._lock:
            return list(self._history)

    @property
    def is_receiving(self) -> bool:
        return self.poll_count > 0 and self.last_error is None


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--exe", type=Path, default=DEFAULT_EXE)
    parser.add_argument("--device", type=int, default=0)
    args = parser.parse_args()

    recorder = RtlAdsbRecorder(exe_path=args.exe, device_index=args.device).start()
    if recorder.last_error and recorder.poll_count == 0 and not recorder._thread:
        print(f"No se pudo iniciar: {recorder.last_error}")
        print("Ejecuta INSTALAR-ADSB.bat primero, o pasa --exe con la ruta correcta.")
        raise SystemExit(1)

    print(f"Escuchando via {args.exe} (Ctrl+C para salir)\n")
    try:
        while True:
            time.sleep(3)
            recent = recorder.around(time.time(), window_s=15)
            estado = f" | ERROR: {recorder.last_error}" if recorder.last_error else ""
            print(f"--- {len(recent)} recientes, {recorder.poll_count} validos, "
                  f"{recorder.corrupt_count} descartados{estado}")
            for o in recent[:10]:
                alt = "suelo" if o.is_on_ground else (f"{o.altitude_ft:.0f}ft" if o.altitude_ft else "-")
                print(f"  {o.icao24}  {o.callsign or '-':9s}  {alt:>7s}")
    except KeyboardInterrupt:
        print("\ncortado")
    finally:
        recorder.stop()
