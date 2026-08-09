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
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication

from lspr_acq_shell.device_io_pool import device_io_pool
from lspr_acq_shell.experiment_control_builders import set_step_valve_button_state_for_button
from lspr_acq_shell.experiment_control_step_decision import plan_step_commands
from lspr_acq_shell.pump_plan import ACTIVE_PUMP_CHANNELS, DEFAULT_TUBE_MM, TUBE_DIAMETER_OPTIONS
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

    def test_one_tube_diameter_control_per_channel_defaulting_to_default_tube_mm(self) -> None:
        self.assertEqual(len(self.window.tube_diameter_spins), ACTIVE_PUMP_CHANNELS)
        for spin in self.window.tube_diameter_spins:
            self.assertEqual(spin.value(), DEFAULT_TUBE_MM)

    def test_pause_template_starts_as_a_single_all_stop_row(self) -> None:
        self.assertEqual(self.window.pause_template_table.rowCount(), 1)
        pause_step = self.window._pause_row_step()
        self.assertEqual(pause_step.valve, "Close")
        self.assertTrue(all(channel.flow_ul_min == 0.0 for channel in pause_step.channels))


class PauseTemplateEditingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.window = ExperimentControlWindow()
        self.addCleanup(_close_and_flush, self.window)

    def test_editing_the_pause_template_table_changes_what_pause_row_step_returns(self) -> None:
        model = self.window._pause_template_model
        model.setData(model.index(0, 2), "Open")  # valve column
        model.setData(model.index(0, 4), "35")  # CH1 flow column

        pause_step = self.window._pause_row_step()

        self.assertEqual(pause_step.valve, "Open")
        self.assertEqual(pause_step.channels[0].flow_ul_min, 35.0)

    def test_pause_row_step_returns_a_deepcopy_not_the_live_template(self) -> None:
        first = self.window._pause_row_step()
        first.valve = "mutated"
        self.assertNotEqual(self.window._pause_row_step().valve, "mutated")

    def test_editing_the_pause_template_does_not_affect_the_main_plan_table(self) -> None:
        # Default main-table step already starts with valve="Open" - use a
        # comment string instead, which starts empty on the main step, to
        # actually distinguish "isolated" from "coincidentally the same".
        model = self.window._pause_template_model
        model.setData(model.index(0, 13), "pause-only comment")  # comment column
        self.assertEqual(len(self.window._read_experiment_control_steps()), 1)
        self.assertNotEqual(self.window._read_experiment_control_steps()[0].description, "pause-only comment")

    def test_pause_uses_the_edited_template_when_actually_pausing(self) -> None:
        model = self.window._pause_template_model
        model.setData(model.index(0, 2), "Open")
        model.setData(model.index(0, 4), "42")

        self.window._run_experiment_control()
        _drain_device_io()

        captured_steps = []

        def _spy(step, previous, context, *, start):
            if not start:
                captured_steps.append(step)
            return plan_step_commands(step, previous, context, start=start)

        with patch("lspri_acq_app.gui.experiment_control_window.plan_step_commands", side_effect=_spy):
            self.window._pause_experiment_control()
            _drain_device_io()

        self.window._stop_experiment_control()
        _drain_device_io()

        self.assertTrue(captured_steps)
        self.assertEqual(captured_steps[0].valve, "Open")
        self.assertEqual(captured_steps[0].channels[0].flow_ul_min, 42.0)


class TubeDiameterWiringTests(unittest.TestCase):
    """Confirms a tube-diameter combobox's value actually reaches the shared
    plan_step_commands() decision function - not just that the widgets
    exist, since a disconnected pump (no hardware in this environment)
    means no pump.set_flow command is ever built to inspect the value in,
    so the wiring itself is what's asserted here, via the same
    StepCommandContext plan_step_commands() is always called with."""

    def setUp(self) -> None:
        self.window = ExperimentControlWindow()
        self.addCleanup(_close_and_flush, self.window)

    def test_changed_tube_diameter_flows_into_the_step_command_context(self) -> None:
        distinct_mm = [option.mm for option in TUBE_DIAMETER_OPTIONS if option.mm != DEFAULT_TUBE_MM][0]
        self.window.tube_diameter_spins[2].setValue(distinct_mm)
        expected = [spin.value() for spin in self.window.tube_diameter_spins]
        self.assertEqual(expected[2], distinct_mm)

        captured_contexts = []

        def _spy(step, previous, context, *, start):
            captured_contexts.append(context)
            return plan_step_commands(step, previous, context, start=start)

        with patch("lspri_acq_app.gui.experiment_control_window.plan_step_commands", side_effect=_spy):
            self.window._run_experiment_control()
            _drain_device_io()

        self.assertTrue(captured_contexts)
        self.assertEqual(captured_contexts[0].tube_mm_by_channel, expected)


class StepEditingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.window = ExperimentControlWindow()
        self.addCleanup(_close_and_flush, self.window)

    def test_add_step_appends_a_new_row(self) -> None:
        self.window.plan_table.selectRow(0)
        self.window._add_experiment_control_step_from_editor()
        self.assertEqual(len(self.window._read_experiment_control_steps()), 2)

    def test_add_step_inserts_after_the_selected_row_not_always_at_the_end(self) -> None:
        self.window.plan_table.selectRow(0)
        self.window._add_experiment_control_step_from_editor()  # -> row 1
        self.window.plan_table.selectRow(0)
        self.window.step_comment_edit.setText("inserted-second")
        self.window._add_experiment_control_step_from_editor()
        steps = self.window._read_experiment_control_steps()
        self.assertEqual(len(steps), 3)
        self.assertEqual(steps[1].description, "inserted-second")


class ManualEditorRowTests(unittest.TestCase):
    """The manual single-step editor row (Duration/Valve/Color/Switch/Comment
    plus per-channel Flow/Direction) - added 2026-08-09, matching sLSPR
    acq's own _current_editor_step field mapping exactly."""

    def setUp(self) -> None:
        self.window = ExperimentControlWindow()
        self.addCleanup(_close_and_flush, self.window)

    def test_add_step_uses_the_editors_current_values(self) -> None:
        self.window.step_duration_spin.setValue(42.0)
        self.window.step_comment_edit.setText("editor comment")
        self.window.step_switch_spin.setValue(7)
        self.window.manual_flow_spins[1].setValue(123.0)
        set_step_valve_button_state_for_button(self.window, self.window.step_valve_button, "Close")

        self.window.plan_table.selectRow(0)
        self.window._add_experiment_control_step_from_editor()
        new_step = self.window._read_experiment_control_steps()[1]

        self.assertEqual(new_step.duration_s, 42.0)
        self.assertEqual(new_step.description, "editor comment")
        self.assertEqual(new_step.switch_position, 7)
        self.assertEqual(new_step.channels[1].flow_ul_min, 123.0)
        self.assertEqual(new_step.valve, "Close")

    def test_add_step_uses_the_selected_colors_hex_value(self) -> None:
        self.window.step_color_combo.setCurrentIndex(2)  # "Red", per PLAN_COLOR_OPTIONS
        self.window.plan_table.selectRow(0)
        self.window._add_experiment_control_step_from_editor()
        new_step = self.window._read_experiment_control_steps()[1]
        self.assertEqual(new_step.color, self.window.step_color_combo.currentData())

    def test_direction_button_toggles_between_cw_and_ccw(self) -> None:
        button = self.window.manual_direction_buttons[0]
        self.assertEqual(self.window._direction_button_value(button), "CW")
        button.click()
        self.assertEqual(self.window._direction_button_value(button), "CCW")

    def test_add_step_captures_the_toggled_direction(self) -> None:
        self.window.manual_direction_buttons[0].click()  # -> CCW
        self.window.plan_table.selectRow(0)
        self.window._add_experiment_control_step_from_editor()
        new_step = self.window._read_experiment_control_steps()[1]
        self.assertEqual(new_step.channels[0].direction, "CCW")

    def test_toggle_step_valve_button_flips_open_and_close(self) -> None:
        self.assertEqual(self.window.step_valve_button.property("valve"), "Open")
        self.window._on_toggle_step_valve_button()
        self.assertEqual(self.window.step_valve_button.property("valve"), "Close")
        self.window._on_toggle_step_valve_button()
        self.assertEqual(self.window.step_valve_button.property("valve"), "Open")

    def test_color_combo_is_populated_from_the_shared_default_palette(self) -> None:
        from lspr_acq_shell.pump_plan import PLAN_COLOR_OPTIONS

        self.assertEqual(self.window.step_color_combo.count(), len(PLAN_COLOR_OPTIONS))
        self.assertEqual(self.window.step_color_combo.itemText(0), PLAN_COLOR_OPTIONS[0][0])

    def test_duplicate_step_clones_the_selected_row(self) -> None:
        self.window.plan_table.selectRow(0)
        self.window._on_duplicate_step_clicked()
        steps = self.window._read_experiment_control_steps()
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0].duration_s, steps[1].duration_s)

    def test_delete_step_removes_the_selected_row(self) -> None:
        self.window.plan_table.selectRow(0)
        self.window._add_experiment_control_step_from_editor()
        self.window.plan_table.selectRow(0)
        self.window._on_delete_step_clicked()
        self.assertEqual(len(self.window._read_experiment_control_steps()), 1)

    def test_delete_step_is_ignored_while_plan_is_running(self) -> None:
        self.window.plan_table.selectRow(0)
        self.window._add_experiment_control_step_from_editor()
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
