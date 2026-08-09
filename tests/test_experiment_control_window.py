"""Qt widget tests for gui/experiment_control_window.py.

Real ExperimentControlWindow, real PlanRunLoopMixin (inherited from
lspr_acq_shell) - the state-machine logic itself already has 63 tests
against sLSPR acq's own window (tests/unit/test_experiment_control_run_loop_characterization.py
and test_experiment_control_step_navigation.py in the umbrella repo); this
file instead covers the parts specific to *this* app's window: that it
constructs without a real device connection, that the table/toolbar wiring
works, and that Run/Hold/Pause/Stop drive the inherited state machine
correctly end to end (including the real async dispatch onto
device_io_pool(), not a mock).

No real pump/valve/selector hardware is attached in this environment -
DeviceCommunicationService.shared() reports every device disconnected,
which is itself worth covering: Run must not crash or hang when nothing is
actually connected, just report status messages saying so.
"""

from __future__ import annotations

import unittest

from PyQt6.QtWidgets import QApplication

from lspr_acq_shell.device_io_pool import device_io_pool
from lspri_acq_app.gui.experiment_control_window import ExperimentControlWindow

_APP = QApplication.instance() or QApplication([])


def _close_and_flush(widget) -> None:
    widget.close()
    widget.deleteLater()
    QApplication.processEvents()


def _drain_device_io() -> None:
    """Wait for any in-flight async step-apply dispatch to complete and its
    `done` signal to be delivered, so assertions made right after a state-
    machine call see the post-dispatch state, not a still-in-flight one."""
    device_io_pool().waitForDone(2000)
    for _ in range(5):
        QApplication.processEvents()


class ExperimentControlWindowConstructionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.window = ExperimentControlWindow()
        self.addCleanup(_close_and_flush, self.window)

    def test_starts_with_one_default_step(self) -> None:
        self.assertEqual(len(self.window._read_experiment_control_steps()), 1)

    def test_starts_idle_with_only_run_enabled(self) -> None:
        self.assertTrue(self.window.run_button.isEnabled())
        self.assertFalse(self.window.hold_button.isEnabled())
        self.assertFalse(self.window.pause_button.isEnabled())
        self.assertFalse(self.window.stop_button.isEnabled())

    def test_plan_table_row_count_matches_steps(self) -> None:
        self.assertEqual(self.window.plan_table.rowCount(), 1)


class StepEditingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.window = ExperimentControlWindow()
        self.addCleanup(_close_and_flush, self.window)

    def test_add_step_appends_a_new_row(self) -> None:
        self.window.plan_table.selectRow(0)
        self.window._on_add_step_clicked()
        self.assertEqual(len(self.window._read_experiment_control_steps()), 2)

    def test_duplicate_step_clones_the_selected_row(self) -> None:
        self.window.plan_table.selectRow(0)
        self.window._on_duplicate_step_clicked()
        steps = self.window._read_experiment_control_steps()
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0].duration_s, steps[1].duration_s)

    def test_delete_step_removes_the_selected_row(self) -> None:
        self.window.plan_table.selectRow(0)
        self.window._on_add_step_clicked()
        self.window.plan_table.selectRow(0)
        self.window._on_delete_step_clicked()
        self.assertEqual(len(self.window._read_experiment_control_steps()), 1)

    def test_delete_step_is_ignored_while_plan_is_running(self) -> None:
        self.window.plan_table.selectRow(0)
        self.window._on_add_step_clicked()
        self.window._run_experiment_control()
        try:
            self.window.plan_table.selectRow(0)
            self.window._on_delete_step_clicked()
            self.assertEqual(len(self.window._read_experiment_control_steps()), 2)
        finally:
            self.window._stop_experiment_control()
            _drain_device_io()


class RunHoldPauseStopIntegrationTests(unittest.TestCase):
    """Drives the real inherited state machine against this window's real
    (no-hardware) device wiring - not a mock of PlanRunLoopMixin's methods."""

    def setUp(self) -> None:
        self.window = ExperimentControlWindow()
        self.addCleanup(_close_and_flush, self.window)

    def test_run_starts_the_plan_and_dispatches_without_crashing(self) -> None:
        self.window._run_experiment_control()
        self.assertTrue(self.window._plan_running)
        _drain_device_io()
        # No real pump attached - status must say so, not silently succeed.
        self.assertIn("not connected", self.window.status_label.text().lower())
        self.window._stop_experiment_control()
        _drain_device_io()

    def test_run_already_running_is_a_no_op(self) -> None:
        self.window._run_experiment_control()
        _drain_device_io()
        self.window._run_experiment_control()
        self.assertTrue(self.window._plan_running)
        self.window._stop_experiment_control()
        _drain_device_io()

    def test_hold_then_run_resumes(self) -> None:
        self.window._run_experiment_control()
        _drain_device_io()
        self.window._hold_experiment_control()
        self.assertTrue(self.window._plan_holding)
        self.window._run_experiment_control()
        self.assertTrue(self.window._plan_running)
        self.assertFalse(self.window._plan_holding)
        self.window._stop_experiment_control()
        _drain_device_io()

    def test_pause_then_stop_returns_to_idle(self) -> None:
        self.window._run_experiment_control()
        _drain_device_io()
        self.window._pause_experiment_control()
        self.assertTrue(self.window._plan_paused)
        _drain_device_io()
        self.window._stop_experiment_control()
        self.assertFalse(self.window._plan_running)
        self.assertFalse(self.window._plan_holding)
        self.assertFalse(self.window._plan_paused)
        _drain_device_io()

    def test_toggle_buttons_reflect_state_through_a_full_cycle(self) -> None:
        self.window._run_experiment_control()
        self.assertFalse(self.window.run_button.isEnabled())
        self.assertTrue(self.window.hold_button.isEnabled())
        self.window._hold_experiment_control()
        self.assertTrue(self.window.run_button.isEnabled())
        self.assertFalse(self.window.hold_button.isEnabled())
        self.window._stop_experiment_control()
        self.assertFalse(self.window.stop_button.isEnabled())
        _drain_device_io()

    def test_stop_on_idle_plan_does_not_raise(self) -> None:
        self.window._stop_experiment_control()  # must not raise
        _drain_device_io()


if __name__ == "__main__":
    unittest.main()
