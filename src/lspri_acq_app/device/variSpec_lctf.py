"""VariSpec LCTF driver (CRi VariSpec, USB-virtual-COM, ASCII 8N1).

Protocol source: apps/LSPRi/acq/docs/manuals/cri-varispec-lctf-manual.pdf,
read directly (Chapter 3, "Controlling VariSpec Filters with Direct Serial
Commands"), not just the architecture plan's paraphrase of it.

Framing (write+read pattern, echo-then-reply split, the "*" W-query
sentinel) is adapted from the Phase 0 spike's VariSpecClient
(spikes/lspri_acq_phase0/illumination_probe.py), which WAS run against the
real connected unit (VIS model, 400-720nm, firmware rev 117, serial 52366,
baudrate 115200 - see the auto-memory note on this device). That spike
surfaced a real bug this driver is built to avoid by construction: every
command - even a plain, non-query set command - gets its input echoed back
first; leaving that echo unread desyncs the *next* command's echo/reply
split (the spike's fire_wavelength()/drain_echo() split existed only to
avoid that echo-read's ~15-20ms cost on a hyper-critical timing path -
set_wavelength() below always drains its own echo synchronously, since
that cost is negligible next to the ~40-80ms settle time it's followed by).

**Not yet verified against real hardware as this specific class** - the
underlying protocol/framing logic IS hardware-verified (via the spike and
its settle-time/passband-calibration measurements), but this file
(VariSpecLctf, matching the IlluminationSource ABC) has not itself been run
against the physical unit yet.
"""

from __future__ import annotations

import time

import serial

from lspri_acq_app.device.illumination_base import IlluminationSource, IlluminationSourceError

_BAUD_RATE = 115200  # USB virtual COM port - verified against the real unit (Phase 0 spike)

# Table 5, page 28 of the manual ("Error Codes").
_ERROR_MESSAGES: dict[int, str] = {
    0: "No errors pending",
    1: "Syntax error",
    2: "Attempt was made to set a read-only parameter",
    3: "'E' (exercise) command was issued with illegal <arg> value",
    4: "Attempt to set wavelength or palette while filter is uninitialized",
    5: "'I' (initialize) command issued with illegal <arg> value",
    6: "Mode error",
    7: "'M' (mode) command issued with illegal <arg> value",
    8: "Error calculating liquid crystal drive levels (internal)",
    9: "Palette not defined",
    10: "Palette not prepared (internal)",
    11: "Palette element out of range",
    12: "Wavelength out of range",
    13: "Liquid crystal drive level out of range (internal)",
    14: "Jump wavelength step too large",
    17: "'G' (go) command issued with illegal <arg> value",
}

# Phase 0 empirical measurement (direction-aware, real VIS unit, 792
# optically-measured transitions) - see spikes/lspri_acq_phase0/docs/
# settle_time_analysis.md's "Recommended settle-time margins" table.
# Deliberately NOT the manual's own generic "50-150ms" figure (Appendix B,
# "Response Time" glossary entry) - that's a broad range covering the whole
# VariSpec product family, not a measurement of this specific unit; the
# empirical numbers are both more accurate for this hardware and
# direction-aware, which the manual's figure isn't.
_SETTLE_MS_ASCENDING = 40.0
_SETTLE_MS_DESCENDING = 80.0  # also the "first move, direction unknown" fallback


class VariSpecLctf(IlluminationSource):
    """Talks to one VariSpec LCTF over its USB-virtual-COM serial port."""

    def __init__(self, port: str) -> None:
        self._port_name = port
        self.port = port
        self._claim_owner = f"varispec-lctf:{id(self)}"
        self._serial: serial.Serial | None = None
        self._current_wavelength_nm: float | None = None
        self._last_move_direction: str | None = None  # "up" / "down" / None (unknown)
        self._wavelength_range_nm: tuple[float, float] | None = None
        self._serial_number: str | None = None
        self._firmware_revision: int | None = None

    # -- connection lifecycle --------------------------------------------------

    def open(self) -> None:
        self._serial = serial.Serial(
            port=self._port_name,
            baudrate=_BAUD_RATE,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.05,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )
        time.sleep(0.2)  # let the virtual COM port settle before first write
        self._serial.reset_input_buffer()

        self._send_set("B 1")  # Brief format - cuts per-command overhead (manual, p.21-22)
        self._read_info()
        # Filter self-initializes at power-up (<1s for current-gen USB
        # units, per the manual's "Initialize" description) - only force a
        # re-init if it reports not-initialized, rather than unconditionally
        # re-initializing (which the manual notes can take 30s+ on older
        # units) on every open().
        if self._send_query("I ?") != "1":
            self._send_set("I 1")

    def close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            finally:
                self._serial = None

    def is_connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    # -- IlluminationSource -----------------------------------------------------

    def set_wavelength(self, nm: float) -> None:
        if self._serial is None:
            raise IlluminationSourceError("VariSpecLctf.set_wavelength() called before open()")
        previous = self._current_wavelength_nm
        self._send_set(f"W {nm:.3f}")

        error_code = self._read_and_clear_error()
        if error_code:
            # The manual's own example (Programming Examples, "Example 1")
            # shows that after an out-of-range W, the filter silently stays
            # at its last legal wavelength rather than moving - query it so
            # the recorded "achieved" wavelength reflects reality, not the
            # rejected request (architecture-plan section 9's HDF5 schema
            # wants exactly this: "record what was actually achieved, not
            # just requested").
            self._current_wavelength_nm = self._read_wavelength()
            message = _ERROR_MESSAGES.get(error_code, f"Unknown error code {error_code}")
            raise IlluminationSourceError(f"VariSpec error {error_code}: {message}")

        self._current_wavelength_nm = nm
        if previous is not None:
            self._last_move_direction = "up" if nm >= previous else "down"

    def current_wavelength(self) -> float | None:
        return self._current_wavelength_nm

    def wavelength_range(self) -> tuple[float, float] | None:
        return self._wavelength_range_nm

    def settle_time_ms(self) -> float:
        if self._last_move_direction == "up":
            return _SETTLE_MS_ASCENDING
        return _SETTLE_MS_DESCENDING

    def device_name(self) -> str:
        if self._serial_number:
            return f"VariSpec LCTF ({self._serial_number})"
        return f"VariSpec LCTF ({self._port_name})"

    # -- protocol framing --------------------------------------------------------
    #
    # The filter always echoes a command back first (even a plain, non-query
    # set command), then - only for queries ("?" argument) - sends its own
    # <cr>-terminated reply. A set command's read must stop after exactly
    # ONE terminator (the echo) - waiting for a second one that will never
    # arrive would stall for the full timeout on every single set_wavelength()
    # call, since a sweep calls this every step. See this module's docstring
    # for how this was discovered against the real unit.

    def _send_set(self, cmd: str, timeout_s: float = 0.5) -> None:
        assert self._serial is not None
        self._serial.write((cmd + "\r").encode("ascii"))
        self._read_until_terminator(timeout_s)  # discard the echo

    def _send_query(self, cmd: str, timeout_s: float = 0.5) -> str:
        assert self._serial is not None
        self._serial.write((cmd + "\r").encode("ascii"))
        self._read_until_terminator(timeout_s)  # discard the echo
        return self._read_until_terminator(timeout_s).strip()

    def _read_until_terminator(self, timeout_s: float) -> str:
        assert self._serial is not None
        deadline = time.perf_counter() + timeout_s
        buf = bytearray()
        while time.perf_counter() < deadline:
            chunk = self._serial.read(1)
            if not chunk:
                continue
            buf += chunk
            if chunk == b"\r":
                break
        return buf.decode("ascii", errors="replace")

    def _read_info(self) -> None:
        # Brief-mode reply: "<rev> <min_nm> <max_nm> <serial>" (manual, p.26).
        reply = self._send_query("V ?", timeout_s=1.0)
        tokens = reply.split()
        if len(tokens) < 4:
            return
        try:
            self._firmware_revision = int(tokens[0])
            self._wavelength_range_nm = (float(tokens[1]), float(tokens[2]))
        except ValueError:
            self._wavelength_range_nm = None
        self._serial_number = tokens[3]

    def _read_wavelength(self) -> float | None:
        reply = self._send_query("W ?")
        try:
            return float(reply)
        except ValueError:
            # "*" is a documented legal W ? reply (manual, Table 5 footnote):
            # "Appears when the filter is set out of bounds, when
            # initialization has not occurred, or when the filter cannot
            # tune to a specified wavelength." A real batch sweep hit this
            # for real during the Phase 0 spike's development (see
            # illumination_probe.py's get_wavelength() docstring) - surfaced
            # as None here rather than raising, since the caller
            # (set_wavelength(), already mid-error-handling) is the one
            # deciding what to do about an unknown current value.
            return None

    def _read_and_clear_error(self) -> int:
        """Read the pending error code, then clear it (R 1) if nonzero.

        The manual explicitly recommends this order ("first retrieving the
        Error Code and then clearing the error condition before
        proceeding") - and it's not just good practice here, it's required
        for correctness: an error code persists until cleared or replaced
        by a new one, so a step that raised once and was never cleared
        would make every subsequent successful step's error check see the
        same stale nonzero code and incorrectly raise again.
        """
        reply = self._send_query("R ?")
        if not reply:
            return 0
        try:
            code = int(reply)
        except ValueError:
            code = -1
        if code:
            self._send_set("R 1")
        return code
