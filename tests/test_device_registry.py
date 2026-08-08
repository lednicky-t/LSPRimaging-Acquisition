"""Tests for device/registry.py's registration side effects - importing it
wires CAMERA and ILLUMINATION into lspr_acq_shell's shared device family
registry (see the module's own docstring).

Deliberately does NOT construct a real DeviceLifecycleController/
DeviceCommunicationService here: those touch real settings files and, via
run_full_cycle()'s PUMP/SWITCH/SELECTOR family scan, real serial ports on
whatever machine runs this test - out of scope for a device-registration
unit test and not something to risk poking at. _discover_and_connect_camera/
_discover_and_connect_illumination are exercised directly instead, which is
safe because with no Basler camera or VariSpec attached (the real, current
state of this environment) both return before ever touching the controller
argument.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from lspr_acq_shell.device_lifecycle import STAGE_MISSING, device_family_order

from lspri_acq_app.device import registry  # noqa: F401 - import triggers registration


class DeviceRegistryTests(unittest.TestCase):
    def test_camera_family_is_registered(self) -> None:
        self.assertIn(registry.CAMERA, device_family_order())

    def test_illumination_family_is_registered(self) -> None:
        self.assertIn(registry.ILLUMINATION, device_family_order())

    def test_discover_and_connect_camera_reports_missing_with_no_camera_attached(self) -> None:
        events: list = []

        event = registry._discover_and_connect_camera(None, [], events.append)

        self.assertEqual(event.device_key, registry.CAMERA)
        self.assertEqual(event.stage, STAGE_MISSING)
        self.assertFalse(event.connected)
        self.assertEqual(events, [event])

    def test_discover_and_connect_illumination_reports_missing_with_no_candidate_ports(self) -> None:
        events: list = []

        with patch.object(registry, "_candidate_illumination_ports", return_value=[]):
            event = registry._discover_and_connect_illumination(None, [], events.append)

        self.assertEqual(event.device_key, registry.ILLUMINATION)
        self.assertEqual(event.stage, STAGE_MISSING)
        self.assertFalse(event.connected)
        self.assertEqual(events, [event])

    def test_discover_and_connect_illumination_reports_missing_when_no_varispec_found(self) -> None:
        events: list = []

        with (
            patch.object(registry, "_candidate_illumination_ports", return_value=["COM4"]),
            patch("lspri_acq_app.device.variSpec_lctf.discover_varispec_port", return_value=None),
        ):
            event = registry._discover_and_connect_illumination(None, ["COM4"], events.append)

        self.assertEqual(event.stage, STAGE_MISSING)
        # Two events: the "probing N candidates" progress event, then "missing".
        self.assertEqual([e.stage for e in events][-1], STAGE_MISSING)


class _FakePortInfo:
    def __init__(self, device: str) -> None:
        self.device = device


class CandidateIlluminationPortsTests(unittest.TestCase):
    """get_port_assignment()/port_owners() are the real safety filters here -
    should_probe_port_for_role() is deliberately NOT used (see
    _candidate_illumination_ports()'s docstring for why it wouldn't actually
    protect anything for a role outside {"pump","switch"})."""

    def test_excludes_ports_manually_assigned_to_another_role(self) -> None:
        with (
            patch("serial.tools.list_ports.comports", return_value=[_FakePortInfo("COM4"), _FakePortInfo("COM6")]),
            patch.object(registry, "get_port_assignment", side_effect=lambda p: "pump" if p == "COM4" else "auto"),
            patch.object(registry, "port_owners", return_value=()),
        ):
            candidates = registry._candidate_illumination_ports()

        self.assertEqual(candidates, ["COM6"])

    def test_excludes_ports_currently_claimed_by_a_live_connection(self) -> None:
        with (
            patch("serial.tools.list_ports.comports", return_value=[_FakePortInfo("COM4"), _FakePortInfo("COM6")]),
            patch.object(registry, "get_port_assignment", return_value="auto"),
            patch.object(registry, "port_owners", side_effect=lambda p: ("pump_1",) if p == "COM4" else ()),
        ):
            candidates = registry._candidate_illumination_ports()

        self.assertEqual(candidates, ["COM6"])

    def test_includes_unassigned_unclaimed_ports(self) -> None:
        with (
            patch("serial.tools.list_ports.comports", return_value=[_FakePortInfo("COM4"), _FakePortInfo("COM6")]),
            patch.object(registry, "get_port_assignment", return_value="auto"),
            patch.object(registry, "port_owners", return_value=()),
        ):
            candidates = registry._candidate_illumination_ports()

        self.assertEqual(candidates, ["COM4", "COM6"])


if __name__ == "__main__":
    unittest.main()
