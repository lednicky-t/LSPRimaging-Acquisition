"""Tests for the VariSpec LCTF driver.

Protocol logic is tested against a fake serial port modeling VariSpec's real
echo-then-reply framing (every write is echoed back first; only queries get
a real reply after the echo) - mirrors the _FakeSerial pattern already used
for RegloICCClient (tests/unit/test_reglo_icc_calibration.py), adapted to
this device's echo behavior, which RegloICCClient's protocol doesn't have.

open()/close() against a real (nonexistent) port are tested for real - no
mocking of pyserial itself, same "test the real no-hardware-found path"
approach used for the Basler driver's tests.
"""

from __future__ import annotations

import unittest

from lspri_acq_app.device.illumination_base import IlluminationSourceError
from lspri_acq_app.device.variSpec_lctf import VariSpecLctf


class _FakeVariSpecSerial:
    """Stand-in for serial.Serial modeling VariSpec's echo-then-reply
    framing: every write's bytes are echoed back automatically; queue_reply()
    appends what should follow the echo for the *next* write (nothing, for a
    plain set command - a real query's reply otherwise)."""

    def __init__(self) -> None:
        self.written: list[bytes] = []
        self._reply_queue: list[bytes] = []
        self._read_buffer = bytearray()
        self.is_open = True

    def queue_reply(self, reply: bytes) -> None:
        self._reply_queue.append(reply)

    def write(self, data: bytes) -> None:
        self.written.append(data)
        self._read_buffer += data  # the echo
        # Only queries ("?") get a real reply after the echo - a plain set
        # command only ever echoes, matching the real device. Without this
        # distinction, a queued reply meant for e.g. "R ?" could get
        # consumed by an unrelated set command's write() first.
        if b"?" in data and self._reply_queue:
            self._read_buffer += self._reply_queue.pop(0)

    def read(self, size: int = 1) -> bytes:
        if not self._read_buffer:
            return b""
        chunk = bytes(self._read_buffer[:size])
        del self._read_buffer[:size]
        return chunk

    def reset_input_buffer(self) -> None:
        self._read_buffer.clear()

    def close(self) -> None:
        self.is_open = False


def _driver_with_fake_serial() -> tuple[VariSpecLctf, _FakeVariSpecSerial]:
    driver = VariSpecLctf(port="FAKE")
    fake_serial = _FakeVariSpecSerial()
    driver._serial = fake_serial  # bypass open(); no real port needed
    return driver, fake_serial


class OpenCloseTests(unittest.TestCase):
    def test_open_with_nonexistent_port_raises(self) -> None:
        driver = VariSpecLctf(port="COM9999")
        with self.assertRaises(Exception):
            driver.open()

    def test_is_connected_false_before_open(self) -> None:
        driver = VariSpecLctf(port="COM9999")
        self.assertFalse(driver.is_connected())

    def test_close_before_open_is_a_no_op(self) -> None:
        driver = VariSpecLctf(port="COM9999")
        driver.close()  # must not raise
        self.assertFalse(driver.is_connected())

    def test_claim_owner_is_per_instance(self) -> None:
        first = VariSpecLctf(port="COM1")
        second = VariSpecLctf(port="COM1")
        self.assertNotEqual(first._claim_owner, second._claim_owner)
        self.assertTrue(first._claim_owner.startswith("varispec-lctf:"))


class SetWavelengthTests(unittest.TestCase):
    def test_successful_set_updates_current_wavelength(self) -> None:
        driver, fake = _driver_with_fake_serial()
        fake.queue_reply(b"0\r")  # R ? -> no error

        driver.set_wavelength(550.0)

        self.assertEqual(driver.current_wavelength(), 550.0)
        # Two writes: "W 550.000\r" (set) then "R ?\r" (error check).
        self.assertEqual(fake.written, [b"W 550.000\r", b"R ?\r"])

    def test_error_reply_raises_with_decoded_message_and_queries_real_value(self) -> None:
        driver, fake = _driver_with_fake_serial()
        fake.queue_reply(b"12\r")  # R ? -> error 12, "Wavelength out of range"
        fake.queue_reply(b"488.000\r")  # W ? -> filter stayed at last legal value

        with self.assertRaises(IlluminationSourceError) as ctx:
            driver.set_wavelength(9000.0)

        self.assertIn("Wavelength out of range", str(ctx.exception))
        self.assertEqual(driver.current_wavelength(), 488.0)

    def test_error_is_cleared_so_it_does_not_leak_into_the_next_step(self) -> None:
        # Regression test for the exact bug the manual's "retrieve then
        # clear" guidance prevents: an uncleared error would make the next,
        # otherwise-successful step's R ? check see the same stale code.
        driver, fake = _driver_with_fake_serial()
        fake.queue_reply(b"12\r")
        fake.queue_reply(b"488.000\r")
        with self.assertRaises(IlluminationSourceError):
            driver.set_wavelength(9000.0)
        self.assertIn(b"R 1\r", fake.written)  # the clear command was sent

        fake.queue_reply(b"0\r")  # next step's R ? - genuinely no error now
        driver.set_wavelength(500.0)  # must not raise
        self.assertEqual(driver.current_wavelength(), 500.0)

    def test_set_wavelength_before_open_raises(self) -> None:
        driver = VariSpecLctf(port="COM9999")
        with self.assertRaises(IlluminationSourceError):
            driver.set_wavelength(500.0)


class SettleTimeTests(unittest.TestCase):
    def test_first_move_uses_worst_case_margin(self) -> None:
        driver, fake = _driver_with_fake_serial()
        self.assertEqual(driver.settle_time_ms(), 80.0)  # unknown direction yet

        fake.queue_reply(b"0\r")
        driver.set_wavelength(500.0)
        # Still unknown-direction margin: this was the *first* successful
        # move, so there's no previous wavelength to compare against.
        self.assertEqual(driver.settle_time_ms(), 80.0)

    def test_ascending_move_uses_the_faster_margin(self) -> None:
        driver, fake = _driver_with_fake_serial()
        fake.queue_reply(b"0\r")
        driver.set_wavelength(500.0)
        fake.queue_reply(b"0\r")
        driver.set_wavelength(550.0)  # ascending

        self.assertEqual(driver.settle_time_ms(), 40.0)

    def test_descending_move_uses_the_slower_margin(self) -> None:
        driver, fake = _driver_with_fake_serial()
        fake.queue_reply(b"0\r")
        driver.set_wavelength(550.0)
        fake.queue_reply(b"0\r")
        driver.set_wavelength(500.0)  # descending

        self.assertEqual(driver.settle_time_ms(), 80.0)


class InfoParsingTests(unittest.TestCase):
    def test_wavelength_range_unknown_before_info_is_read(self) -> None:
        driver, _fake = _driver_with_fake_serial()
        self.assertIsNone(driver.wavelength_range())

    def test_read_info_parses_brief_mode_version_reply(self) -> None:
        driver, fake = _driver_with_fake_serial()
        # Brief-mode "V ?" reply per the manual (p.26): "<rev> <min> <max> <serial>".
        fake.queue_reply(b"117 400.00 720.00 52366\r")

        driver._read_info()

        self.assertEqual(driver.wavelength_range(), (400.0, 720.0))
        self.assertEqual(driver._firmware_revision, 117)
        self.assertEqual(driver._serial_number, "52366")
        self.assertIn("52366", driver.device_name())


if __name__ == "__main__":
    unittest.main()
