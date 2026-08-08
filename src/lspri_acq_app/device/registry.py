"""Wires this app's Camera/IlluminationSource devices into lspr_acq_shell's
shared device lifecycle (architecture plan section 6.1): registers CAMERA
and ILLUMINATION as new device families, plus their driver connect
factories, so they get the same lifecycle management PUMP/SWITCH/SELECTOR
already get for free (BUSY state, canonical labels, connect/disconnect,
single discover_and_connect implementation).

This module's registrations are side effects of import - importing it (done
once, at app startup - see app.py) is what makes CAMERA/ILLUMINATION show up
in lspr_acq_shell.device_lifecycle.device_family_order(), the same way sLSPR
acq's own device_lifecycle.py shim registers its spectrometer stage at
import time.

**PUMP/SWITCH/SELECTOR reuse is deliberately NOT wired here yet** - the
architecture plan (section 6.1) assumes this app also drives fluidics
("confirm this against your actual setup"), but that's flagged as an open
question, not a settled fact - wiring it in without confirming would guess
at a real setup decision. Add it here (mirroring sLSPR acq's own
DeviceLifecycleController.shared() usage) once confirmed.

**Test-isolation warning**: register_device_family() mutates process-global
state in lspr_acq_shell.device_lifecycle (_DEVICE_FAMILIES/_DEVICE_FAMILY_ORDER)
with no unregister mechanism - importing this module registers CAMERA/
ILLUMINATION for the rest of that Python process. Confirmed by hitting it
directly: running `pytest tests/ apps/LSPRi/acq/tests/` in one invocation
makes tests/unit/test_device_lifecycle.py's exact-3-family assertions fail,
even though both suites pass cleanly run separately (which is how they're
meant to be run - see apps/sLSPR/acq/tests/ for the existing precedent of an
app keeping its own tests separate from the umbrella tests/ directory). Do
not merge apps/LSPRi/acq/tests/ into the same pytest invocation as tests/.
"""

from __future__ import annotations

from typing import Sequence

from lspr_acq_shell import DeviceLifecycleEvent, register_device_family, register_driver_connect_factory
from lspr_acq_shell.connection_registry import port_owners
from lspr_acq_shell.device_lifecycle import (
    STAGE_DISCOVERING,
    STAGE_MISSING,
    DeviceLifecycleController,
    EmitFn,
    ensure_device_profile,
)
from lspr_acq_shell.port_assignments import get_port_assignment

CAMERA = "camera"
ILLUMINATION = "illumination"

BASLER_DRIVER = "basler-camera"
VARISPEC_DRIVER = "varispec-lctf"


def _camera_driver_connect_factory(endpoint: str) -> tuple[object, dict[str, str]]:
    """endpoint here is the camera's serial number (see
    _discover_and_connect_camera below) - Basler cameras have no COM-port
    concept, so the serial number plays the role DeviceCommunicationService
    otherwise expects a port string to play."""
    from lspri_acq_app.device.basler_camera import BaslerCamera

    camera = BaslerCamera(serial_number=endpoint)
    camera.open()
    identity = {"model": camera.device_name(), "serial_number": endpoint}
    return camera, identity


register_driver_connect_factory(BASLER_DRIVER, _camera_driver_connect_factory)


def _discover_and_connect_camera(
    controller: DeviceLifecycleController, candidates: Sequence[object], emit: EmitFn
) -> DeviceLifecycleEvent:
    from lspri_acq_app.device.basler_camera import discover_basler_cameras

    devices = discover_basler_cameras()
    if not devices:
        event = DeviceLifecycleEvent(CAMERA, STAGE_MISSING, "No Basler camera discovered.")
        emit(event)
        return event

    emit(DeviceLifecycleEvent(CAMERA, STAGE_DISCOVERING, f"Found {len(devices)} Basler camera(s)..."))
    serial_number = str(devices[0].GetSerialNumber())
    # Always the fixed canonical label (camera_1) - never resolved by
    # searching other profiles for a fingerprint/endpoint match, matching
    # every existing built-in family's own comment on this exact point (see
    # device_lifecycle._discover_and_connect_pump and
    # apps/sLSPR/acq/docs/device-layer/DEVICE_LAYER_AUDIT_2026.md's incident
    # #31, the stale-duplicate-profile bug this rule exists to prevent).
    label = ensure_device_profile(controller._service, CAMERA, serial_number, driver=BASLER_DRIVER)
    # Calls the same private _connect_and_setup() the built-in pump/valve/
    # selector families call (device_lifecycle.py's own module docstring
    # frames discover_and_connect as the intended per-family extension
    # point) rather than the public request_connect() - request_connect()
    # adds a busy-guard meant for the manual on-demand "Connect" button path,
    # which run_full_cycle()'s startup scan doesn't use for any other family
    # either.
    return controller._connect_and_setup(CAMERA, label, serial_number, emit)


register_device_family(CAMERA, _discover_and_connect_camera, driver=BASLER_DRIVER, role="camera", label="camera_1")


# ── ILLUMINATION (VariSpec LCTF) ─────────────────────────────────────────────
#
# Unlike Basler (a vendor-SDK enumeration call that can only ever find real
# Basler cameras - safe to call unconditionally), a serial LCTF looks
# identical to any other "USB Serial Device" at the OS level. Candidate-port
# safety therefore matters here in a way it doesn't for CAMERA - see
# _candidate_illumination_ports()'s docstring for why
# should_probe_port_for_role() (lspr_acq_shell.port_assignments) is NOT a
# safe generic check to reach for here, and what's used instead.


def _candidate_illumination_ports() -> list[str]:
    """Real serial ports that are safe to probe for a VariSpec: not manually
    assigned to another role, and not currently claimed by a live
    connection.

    should_probe_port_for_role(port, role) (lspr_acq_shell.port_assignments)
    looks like the existing safety check to reuse here, but traced its real
    logic first rather than assuming: `if role_name not in {"pump",
    "switch"}: return True` - for any role name outside that hardcoded pair
    (including "illumination"), it unconditionally returns True. That's a
    silent no-op, not "no restriction needed" - calling it with
    role="illumination" would have looked like a safety check while
    providing none, and a port the user manually pinned to "pump" would still
    get probed with VariSpec-specific commands. Checking
    get_port_assignment(port) != "auto" directly sidesteps this: any manual
    assignment at all (today, only "pump"/"switch" are assignable - there's
    no "illumination" assignment for a user to set) means "not this one."
    port_owners() (lspr_acq_shell.connection_registry) is genuinely generic
    (no hardcoded role names) and adds the second layer: skip anything
    already claimed by a live connection right now, even if never manually
    pinned.
    """
    import serial.tools.list_ports

    candidates: list[str] = []
    for port_info in serial.tools.list_ports.comports():
        device = port_info.device
        if get_port_assignment(device) != "auto":
            continue
        if port_owners(device):
            continue
        candidates.append(device)
    return candidates


def _illumination_driver_connect_factory(endpoint: str) -> tuple[object, dict[str, str]]:
    from lspri_acq_app.device.variSpec_lctf import VariSpecLctf

    driver = VariSpecLctf(endpoint)
    driver.open()
    wavelength_range = driver.wavelength_range()
    identity = {
        "model": driver.device_name(),
        "wavelength_range_nm": f"{wavelength_range[0]:.1f}-{wavelength_range[1]:.1f}" if wavelength_range else "",
    }
    return driver, identity


register_driver_connect_factory(VARISPEC_DRIVER, _illumination_driver_connect_factory)


def _discover_and_connect_illumination(
    controller: DeviceLifecycleController, candidates: Sequence[object], emit: EmitFn
) -> DeviceLifecycleEvent:
    from lspri_acq_app.device.variSpec_lctf import discover_varispec_port

    safe_candidates = _candidate_illumination_ports()
    if not safe_candidates:
        event = DeviceLifecycleEvent(ILLUMINATION, STAGE_MISSING, "No candidate serial ports to probe for a VariSpec LCTF.")
        emit(event)
        return event

    emit(DeviceLifecycleEvent(ILLUMINATION, STAGE_DISCOVERING, f"Probing {len(safe_candidates)} candidate port(s) for a VariSpec LCTF..."))
    port = discover_varispec_port(safe_candidates)
    if port is None:
        event = DeviceLifecycleEvent(ILLUMINATION, STAGE_MISSING, "No VariSpec LCTF discovered.")
        emit(event)
        return event

    # Always the fixed canonical label (illumination_1) - see the matching
    # comment in _discover_and_connect_camera above.
    label = ensure_device_profile(controller._service, ILLUMINATION, port, driver=VARISPEC_DRIVER)
    return controller._connect_and_setup(ILLUMINATION, label, port, emit)


register_device_family(
    ILLUMINATION, _discover_and_connect_illumination, driver=VARISPEC_DRIVER, role="illumination", label="illumination_1"
)
