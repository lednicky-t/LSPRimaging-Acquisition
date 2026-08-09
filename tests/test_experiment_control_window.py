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

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QCheckBox, QDialog, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem

from lspr_acq_shell.device_io_pool import device_io_pool
from lspr_acq_shell.experiment_control_builders import set_step_valve_button_state_for_button
from lspr_acq_shell.experiment_control_step_decision import plan_step_commands
from lspr_acq_shell.pump_plan import ACTIVE_PUMP_CHANNELS, DEFAULT_TUBE_MM, PLAN_COLOR_OPTIONS, TUBE_DIAMETER_OPTIONS
from lspri_acq_app.gui.experiment_control_window import ExperimentControlWindow

_APP = QApplication.instance() or QApplication([])


def _close_and_flush(widget) -> None:
    widget.close()
    widget.deleteLater()
    QApplication.processEvents()


def _make_window(testcase: unittest.TestCase, *, settings_path: Path | None = None) -> ExperimentControlWindow:
    """Construct a real ExperimentControlWindow with settings persistence
    redirected to an isolated temp file, not the real per-user
    lspri_acq_settings.json - every dialog-accept path in this window calls
    _save_experiment_control_settings(), so without this every test run
    would read/write the maintainer's real settings file and pollute later
    tests in this same file with whatever an earlier test saved."""
    if settings_path is None:
        tmp_dir = tempfile.TemporaryDirectory()
        testcase.addCleanup(tmp_dir.cleanup)
        settings_path = Path(tmp_dir.name) / "lspri_acq_settings.json"
    patcher = patch.object(ExperimentControlWindow, "_settings_path", lambda self: settings_path)
    patcher.start()
    testcase.addCleanup(patcher.stop)
    window = ExperimentControlWindow()
    testcase.addCleanup(_close_and_flush, window)
    return window


def _drain_device_io() -> None:
    """Wait for any in-flight async step-apply dispatch to complete and its
    `done` signal to be delivered, so assertions made right after a state-
    machine call see the post-dispatch state, not a still-in-flight one."""
    device_io_pool().waitForDone(2000)
    for _ in range(5):
        QApplication.processEvents()


class ExperimentControlWindowConstructionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.window = _make_window(self)

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
        self.window = _make_window(self)

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
        self.window = _make_window(self)

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
        self.window = _make_window(self)

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
        self.window = _make_window(self)

    def test_add_step_uses_the_editors_current_values(self) -> None:
        self.window.step_duration_spin.setValue(42.0)
        self.window.step_comment_edit.setText("editor comment")
        self.window.step_switch_combo.setCurrentIndex(6)  # position 7
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
        self.window = _make_window(self)

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


class ValveLabelDialogTests(unittest.TestCase):
    """_edit_valve_state_labels - a lean QDialog (not sLSPR acq's custom
    frameless-bordered one), same editable data. Drives the dialog by
    patching QDialog.exec to inspect/mutate its already-constructed child
    widgets before returning Accepted/Rejected, simulating a user editing
    the fields then clicking OK/Cancel - not a mock of the method itself."""

    def setUp(self) -> None:
        self.window = _make_window(self)

    def test_accepting_edited_labels_updates_state_and_the_valve_button(self) -> None:
        def _exec(dialog_self):
            edits = dialog_self.findChildren(QLineEdit)
            edits[0].setText("Loaded")
            edits[1].setText("Sealed")
            return QDialog.DialogCode.Accepted

        with patch.object(QDialog, "exec", _exec):
            self.window._edit_valve_state_labels()

        self.assertEqual(self.window._valve_state_labels["Open"], "Loaded")
        self.assertEqual(self.window._valve_state_labels["Close"], "Sealed")
        self.assertEqual(self.window.step_valve_button.text(), "Loaded")

    def test_cancelling_leaves_labels_unchanged(self) -> None:
        def _exec(dialog_self):
            dialog_self.findChildren(QLineEdit)[0].setText("Should not stick")
            return QDialog.DialogCode.Rejected

        with patch.object(QDialog, "exec", _exec):
            self.window._edit_valve_state_labels()

        self.assertEqual(self.window._valve_state_labels["Open"], "Open")

    def test_blank_label_falls_back_to_the_raw_state_name(self) -> None:
        def _exec(dialog_self):
            dialog_self.findChildren(QLineEdit)[0].setText("   ")
            return QDialog.DialogCode.Accepted

        with patch.object(QDialog, "exec", _exec):
            self.window._edit_valve_state_labels()

        self.assertEqual(self.window._valve_state_labels["Open"], "Open")


class ColorPaletteDialogTests(unittest.TestCase):
    """_edit_color_palette_entries - a lean QDialog (QTableWidget + add/
    remove buttons), not sLSPR acq's custom themed one. Same driving
    approach as ValveLabelDialogTests."""

    def setUp(self) -> None:
        self.window = _make_window(self)

    def test_color_combo_starts_populated_from_the_shared_default_palette(self) -> None:
        self.assertEqual(self.window.step_color_combo.count(), len(PLAN_COLOR_OPTIONS))

    def test_accepting_unchanged_keeps_the_same_entries(self) -> None:
        def _exec(dialog_self):
            return QDialog.DialogCode.Accepted

        with patch.object(QDialog, "exec", _exec):
            self.window._edit_color_palette_entries()

        self.assertEqual(len(self.window._color_palette_entries), len(PLAN_COLOR_OPTIONS))
        self.assertEqual(self.window.step_color_combo.count(), len(PLAN_COLOR_OPTIONS))

    def test_removing_a_row_shrinks_the_combo(self) -> None:
        def _exec(dialog_self):
            table = dialog_self.findChild(QTableWidget)
            table.setCurrentCell(0, 0)
            remove_button = [b for b in dialog_self.findChildren(QPushButton) if b.text() == "Remove selected"][0]
            remove_button.click()
            return QDialog.DialogCode.Accepted

        with patch.object(QDialog, "exec", _exec):
            self.window._edit_color_palette_entries()

        self.assertEqual(len(self.window._color_palette_entries), len(PLAN_COLOR_OPTIONS) - 1)

    def test_renaming_an_entry_is_reflected_in_the_combo(self) -> None:
        def _exec(dialog_self):
            table = dialog_self.findChild(QTableWidget)
            table.setItem(0, 0, QTableWidgetItem("Renamed"))
            return QDialog.DialogCode.Accepted

        with patch.object(QDialog, "exec", _exec):
            self.window._edit_color_palette_entries()

        self.assertEqual(self.window.step_color_combo.itemText(0), "Renamed")

    def test_cancelling_leaves_the_palette_unchanged(self) -> None:
        def _exec(dialog_self):
            table = dialog_self.findChild(QTableWidget)
            table.setItem(0, 0, QTableWidgetItem("Should not stick"))
            return QDialog.DialogCode.Rejected

        with patch.object(QDialog, "exec", _exec):
            self.window._edit_color_palette_entries()

        self.assertEqual(self.window.step_color_combo.itemText(0), PLAN_COLOR_OPTIONS[0][0])


class SwitchSolutionDialogTests(unittest.TestCase):
    """_edit_switch_solution_labels - a lean 12-row QTableWidget dialog
    (Solution column only - sLSPR acq's own dialog also has Concentration/
    Unit/Notes columns, but nothing in this app reads them yet, so they
    were deliberately left out rather than ported unused)."""

    def setUp(self) -> None:
        self.window = _make_window(self)

    def test_switch_combo_starts_with_12_positions_all_labeled_empty(self) -> None:
        self.assertEqual(self.window.step_switch_combo.count(), 12)
        self.assertEqual(self.window.step_switch_combo.itemText(0), "1: empty")
        self.assertEqual(self.window.step_switch_combo.itemText(11), "12: empty")

    def test_accepting_edited_labels_updates_the_combo_text(self) -> None:
        def _exec(dialog_self):
            table = dialog_self.findChild(QTableWidget)
            table.setItem(2, 1, QTableWidgetItem("Buffer A"))  # position 3
            return QDialog.DialogCode.Accepted

        with patch.object(QDialog, "exec", _exec):
            self.window._edit_switch_solution_labels()

        self.assertEqual(self.window.step_switch_combo.itemText(2), "3: Buffer A")
        self.assertEqual(self.window._switch_solution_labels[2], "Buffer A")

    def test_cancelling_leaves_labels_unchanged(self) -> None:
        def _exec(dialog_self):
            table = dialog_self.findChild(QTableWidget)
            table.setItem(0, 1, QTableWidgetItem("Should not stick"))
            return QDialog.DialogCode.Rejected

        with patch.object(QDialog, "exec", _exec):
            self.window._edit_switch_solution_labels()

        self.assertEqual(self.window.step_switch_combo.itemText(0), "1: empty")

    def test_add_step_captures_the_selected_switch_position_and_solution(self) -> None:
        def _exec(dialog_self):
            table = dialog_self.findChild(QTableWidget)
            table.setItem(4, 1, QTableWidgetItem("Sample 1"))  # position 5
            return QDialog.DialogCode.Accepted

        with patch.object(QDialog, "exec", _exec):
            self.window._edit_switch_solution_labels()
        self.window.step_switch_combo.setCurrentIndex(4)

        self.window.plan_table.selectRow(0)
        self.window._add_experiment_control_step_from_editor()
        new_step = self.window._read_experiment_control_steps()[1]

        self.assertEqual(new_step.switch_position, 5)

    def test_position_column_is_not_editable(self) -> None:
        def _exec(dialog_self):
            table = dialog_self.findChild(QTableWidget)
            position_item = table.item(0, 0)
            self.assertFalse(position_item.flags() & Qt.ItemFlag.ItemIsEditable)
            return QDialog.DialogCode.Rejected

        with patch.object(QDialog, "exec", _exec):
            self.window._edit_switch_solution_labels()


class PumpDisplaySettingsDialogTests(unittest.TestCase):
    """_edit_pump_display_settings - unlike the other three dialogs added
    this session, this one is wired to a setting with a real effect on
    real hardware dispatch: StepCommandContext.pump_display_enabled was
    hardcoded False before this dialog existed."""

    def setUp(self) -> None:
        self.window = _make_window(self)

    def test_starts_disabled(self) -> None:
        self.assertFalse(self.window._pump_display_enabled)

    def test_accepting_checked_enables_it(self) -> None:
        def _exec(dialog_self):
            checkbox = dialog_self.findChild(QCheckBox)
            checkbox.setChecked(True)
            return QDialog.DialogCode.Accepted

        with patch.object(QDialog, "exec", _exec):
            self.window._edit_pump_display_settings()

        self.assertTrue(self.window._pump_display_enabled)

    def test_cancelling_leaves_it_unchanged(self) -> None:
        def _exec(dialog_self):
            dialog_self.findChild(QCheckBox).setChecked(True)
            return QDialog.DialogCode.Rejected

        with patch.object(QDialog, "exec", _exec):
            self.window._edit_pump_display_settings()

        self.assertFalse(self.window._pump_display_enabled)

    def test_enabling_it_flows_into_the_step_command_context(self) -> None:
        def _exec(dialog_self):
            dialog_self.findChild(QCheckBox).setChecked(True)
            return QDialog.DialogCode.Accepted

        with patch.object(QDialog, "exec", _exec):
            self.window._edit_pump_display_settings()

        captured_contexts = []

        def _spy(step, previous, context, *, start):
            captured_contexts.append(context)
            return plan_step_commands(step, previous, context, start=start)

        with patch("lspri_acq_app.gui.experiment_control_window.plan_step_commands", side_effect=_spy):
            self.window._run_experiment_control()
            _drain_device_io()

        self.assertTrue(captured_contexts)
        self.assertTrue(captured_contexts[0].pump_display_enabled)


class SettingsPersistenceTests(unittest.TestCase):
    """Confirms settings actually survive across window instances, not just
    that _save_experiment_control_settings() runs without raising - two
    windows sharing the same (isolated, temp-file) settings path, the
    second constructed after the first, simulating an app restart."""

    def setUp(self) -> None:
        tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(tmp_dir.cleanup)
        self.settings_path = Path(tmp_dir.name) / "lspri_acq_settings.json"

    def _make(self) -> ExperimentControlWindow:
        return _make_window(self, settings_path=self.settings_path)

    def test_valve_labels_survive_a_restart(self) -> None:
        first = self._make()

        def _exec(dialog_self):
            dialog_self.findChildren(QLineEdit)[0].setText("Loaded")
            return QDialog.DialogCode.Accepted

        with patch.object(QDialog, "exec", _exec):
            first._edit_valve_state_labels()

        second = self._make()
        self.assertEqual(second._valve_state_labels["Open"], "Loaded")
        self.assertEqual(second.step_valve_button.text(), "Loaded")

    def test_color_palette_survives_a_restart(self) -> None:
        first = self._make()

        def _exec(dialog_self):
            table = dialog_self.findChild(QTableWidget)
            table.setItem(0, 0, QTableWidgetItem("Restart-safe"))
            return QDialog.DialogCode.Accepted

        with patch.object(QDialog, "exec", _exec):
            first._edit_color_palette_entries()

        second = self._make()
        self.assertEqual(second.step_color_combo.itemText(0), "Restart-safe")

    def test_switch_solution_labels_survive_a_restart(self) -> None:
        first = self._make()

        def _exec(dialog_self):
            table = dialog_self.findChild(QTableWidget)
            table.setItem(0, 1, QTableWidgetItem("Buffer A"))
            return QDialog.DialogCode.Accepted

        with patch.object(QDialog, "exec", _exec):
            first._edit_switch_solution_labels()

        second = self._make()
        self.assertEqual(second._switch_solution_labels[0], "Buffer A")
        self.assertEqual(second.step_switch_combo.itemText(0), "1: Buffer A")

    def test_pump_display_enabled_survives_a_restart(self) -> None:
        first = self._make()

        def _exec(dialog_self):
            dialog_self.findChild(QCheckBox).setChecked(True)
            return QDialog.DialogCode.Accepted

        with patch.object(QDialog, "exec", _exec):
            first._edit_pump_display_settings()

        second = self._make()
        self.assertTrue(second._pump_display_enabled)

    def test_tube_diameter_survives_a_restart(self) -> None:
        first = self._make()
        distinct_mm = [option.mm for option in TUBE_DIAMETER_OPTIONS if option.mm != DEFAULT_TUBE_MM][0]
        first.tube_diameter_spins[1].setValue(distinct_mm)

        second = self._make()
        self.assertEqual(second.tube_diameter_spins[1].value(), distinct_mm)
        self.assertEqual(second.tube_diameter_spins[0].value(), DEFAULT_TUBE_MM)

    def test_a_missing_settings_file_falls_back_to_defaults_without_raising(self) -> None:
        window = self._make()  # settings_path does not exist yet
        self.assertEqual(window._valve_state_labels, {"Open": "Open", "Close": "Close"})
        self.assertEqual(len(window._color_palette_entries), len(PLAN_COLOR_OPTIONS))


def _drain_thread_pool() -> None:
    from PyQt6.QtCore import QThreadPool

    QThreadPool.globalInstance().waitForDone(2000)
    for _ in range(5):
        QApplication.processEvents()


class ImportExportTests(unittest.TestCase):
    """Real ExperimentPlanImportTask/ExperimentPlanExportTask dispatched onto
    the real QThreadPool.globalInstance() and drained, not mocked - proves
    an actual round trip through real file I/O, using tempfile paths so
    nothing touches real user files. QFileDialog is patched only for the
    path it returns (not the import/export logic itself)."""

    def setUp(self) -> None:
        self.window = _make_window(self)
        tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(tmp_dir.cleanup)
        self.tmp_dir = Path(tmp_dir.name)

    def test_export_writes_a_real_yaml_file(self) -> None:
        export_path = self.tmp_dir / "plan.flow.yaml"
        with patch("lspri_acq_app.gui.experiment_control_window.QFileDialog.getSaveFileName", return_value=(str(export_path), "")):
            self.window._on_export_plan_clicked()
            _drain_thread_pool()

        self.assertTrue(export_path.exists())
        content = export_path.read_text(encoding="utf-8")
        self.assertIn("steps:", content)
        self.assertIn("format:", content)

    def test_export_with_no_steps_does_not_write_a_file(self) -> None:
        self.window.plan_table.selectRow(0)
        self.window._on_delete_step_clicked()
        self.assertEqual(len(self.window._read_experiment_control_steps()), 0)

        export_path = self.tmp_dir / "plan.flow.yaml"
        with patch("lspri_acq_app.gui.experiment_control_window.QFileDialog.getSaveFileName", return_value=(str(export_path), "")):
            self.window._on_export_plan_clicked()
            _drain_thread_pool()

        self.assertFalse(export_path.exists())

    def test_round_trip_export_then_import_preserves_step_values(self) -> None:
        self.window.step_duration_spin.setValue(77.0)
        self.window.step_comment_edit.setText("round-trip comment")
        self.window.manual_flow_spins[2].setValue(55.0)
        self.window.plan_table.selectRow(0)
        self.window._add_experiment_control_step_from_editor()

        export_path = self.tmp_dir / "plan.flow.yaml"
        with patch("lspri_acq_app.gui.experiment_control_window.QFileDialog.getSaveFileName", return_value=(str(export_path), "")):
            self.window._on_export_plan_clicked()
            _drain_thread_pool()

        second = _make_window(self)
        with patch("lspri_acq_app.gui.experiment_control_window.QFileDialog.getOpenFileName", return_value=(str(export_path), "")):
            second._on_import_plan_clicked()
            _drain_thread_pool()

        imported_steps = second._read_experiment_control_steps()
        self.assertEqual(len(imported_steps), 2)
        self.assertEqual(imported_steps[1].duration_s, 77.0)
        self.assertEqual(imported_steps[1].description, "round-trip comment")
        self.assertEqual(imported_steps[1].channels[2].flow_ul_min, 55.0)

    def test_import_merges_new_colors_into_the_palette(self) -> None:
        yaml_path = self.tmp_dir / "plan.flow.yaml"
        yaml_path.write_text(
            "format:\n  name: LSPR Experiment Plan\n  version: 1\n"
            "steps:\n"
            "  - id: 1\n"
            "    duration_s: 10.0\n"
            "    color: '#123456'\n"
            "    comment: ''\n"
            "    devices:\n"
            "      pump_1: {}\n"
            "      valve_1: {state: open}\n"
            "      switch_1: {port: 1}\n",
            encoding="utf-8",
        )
        with patch("lspri_acq_app.gui.experiment_control_window.QFileDialog.getOpenFileName", return_value=(str(yaml_path), "")):
            self.window._on_import_plan_clicked()
            _drain_thread_pool()

        colors = {color for _name, color in self.window._color_palette_entries}
        self.assertIn("#123456", colors)

    def test_import_updates_tube_diameters(self) -> None:
        distinct_mm = [option.mm for option in TUBE_DIAMETER_OPTIONS if option.mm != DEFAULT_TUBE_MM][0]
        yaml_path = self.tmp_dir / "plan.flow.yaml"
        yaml_path.write_text(
            "format:\n  name: LSPR Experiment Plan\n  version: 1\n"
            "devices:\n"
            f"  pumps:\n    pump_1:\n      channels:\n        ch1: {{tube_mm: {distinct_mm}}}\n"
            "steps:\n"
            "  - id: 1\n    duration_s: 10.0\n    color: '#4E79A7'\n    comment: ''\n"
            "    devices:\n      pump_1: {}\n      valve_1: {state: open}\n      switch_1: {port: 1}\n",
            encoding="utf-8",
        )
        with patch("lspri_acq_app.gui.experiment_control_window.QFileDialog.getOpenFileName", return_value=(str(yaml_path), "")):
            self.window._on_import_plan_clicked()
            _drain_thread_pool()

        self.assertEqual(self.window.tube_diameter_spins[0].value(), distinct_mm)

    def test_import_of_a_nonexistent_file_reports_status_without_raising(self) -> None:
        missing_path = self.tmp_dir / "does_not_exist.flow.yaml"
        with patch("lspri_acq_app.gui.experiment_control_window.QFileDialog.getOpenFileName", return_value=(str(missing_path), "")):
            self.window._on_import_plan_clicked()  # must not raise
            _drain_thread_pool()

        self.assertIn("Could not import", self.window.status_label.text())

    def test_cancelling_the_export_dialog_does_nothing(self) -> None:
        with patch("lspri_acq_app.gui.experiment_control_window.QFileDialog.getSaveFileName", return_value=("", "")):
            self.window._on_export_plan_clicked()  # must not raise
            _drain_thread_pool()

    def test_cancelling_the_import_dialog_does_nothing(self) -> None:
        with patch("lspri_acq_app.gui.experiment_control_window.QFileDialog.getOpenFileName", return_value=("", "")):
            self.window._on_import_plan_clicked()  # must not raise
            _drain_thread_pool()
        self.assertEqual(len(self.window._read_experiment_control_steps()), 1)


class PlanTableDelegateWiringTests(unittest.TestCase):
    """Confirms the real plan_table/pause_template_table (not a bare
    PlanTableModel/delegate pair) actually got the real delegates
    installed - the delegate behavior itself is covered directly in
    test_plan_table_model.py."""

    def setUp(self) -> None:
        self.window = _make_window(self)

    def test_plan_table_has_real_delegates_not_the_default_one(self) -> None:
        from lspri_acq_app.gui.plan_table_model import COLUMN_SWITCH, COLUMN_VALVE, SwitchSolutionDelegate, ValveDelegate

        self.assertIsInstance(self.window.plan_table.itemDelegateForColumn(COLUMN_VALVE), ValveDelegate)
        self.assertIsInstance(self.window.plan_table.itemDelegateForColumn(COLUMN_SWITCH), SwitchSolutionDelegate)

    def test_pause_template_table_also_has_real_delegates(self) -> None:
        from lspri_acq_app.gui.plan_table_model import COLUMN_VALVE, ValveDelegate

        self.assertIsInstance(self.window.pause_template_table.itemDelegateForColumn(COLUMN_VALVE), ValveDelegate)

    def test_valve_delegate_display_text_reflects_a_custom_label(self) -> None:
        from lspri_acq_app.gui.plan_table_model import COLUMN_VALVE

        def _exec(dialog_self):
            dialog_self.findChildren(QLineEdit)[0].setText("Loaded")
            return QDialog.DialogCode.Accepted

        with patch.object(QDialog, "exec", _exec):
            self.window._edit_valve_state_labels()

        delegate = self.window.plan_table.itemDelegateForColumn(COLUMN_VALVE)
        index = self.window._table_model.index(0, COLUMN_VALVE)
        text = delegate.displayText(index.data(Qt.ItemDataRole.DisplayRole), None)
        self.assertEqual(text, "Loaded")


class AssignmentTableStateTests(unittest.TestCase):
    """assignment_table_state()/apply_assignment_table_state() - the
    2026-08-09 public seam storage/hdf5_export.py's ImagingMeasurementWriter
    uses to read/restore this window's valve/switch/color-palette state for
    session save/load, kept separate from _save_experiment_control_settings'
    own per-user JSON persistence."""

    def setUp(self) -> None:
        self.window = _make_window(self)

    def test_state_reflects_current_defaults(self) -> None:
        state = self.window.assignment_table_state()
        self.assertEqual(state["valve_state_labels"]["Open"], "Open")
        self.assertEqual(len(state["switch_solution_labels"]), 12)
        self.assertEqual(state["color_palette_entries"], list(PLAN_COLOR_OPTIONS))

    def test_apply_valve_labels_updates_state_and_combo_source(self) -> None:
        self.window.apply_assignment_table_state(
            valve_state_labels={"Open": "Loaded", "Close": "Waste"},
            valve_state_colors={"Open": "#123456"},
        )
        self.assertEqual(self.window._valve_state_label("Open"), "Loaded")
        self.assertEqual(self.window.assignment_table_state()["valve_state_colors"]["Open"], "#123456")

    def test_apply_color_palette_repopulates_the_combo(self) -> None:
        self.window.apply_assignment_table_state(color_palette_entries=[("Blue", "#0000FF")])
        self.assertEqual(self.window.step_color_combo.count(), 1)
        self.assertEqual(self.window.step_color_combo.itemText(0), "Blue")

    def test_apply_switch_solution_labels_updates_display_text(self) -> None:
        self.window.apply_assignment_table_state(switch_solution_labels=["Buffer A"])
        self.assertEqual(self.window._switch_display_text(1), "1: Buffer A")

    def test_apply_state_persists_to_settings(self) -> None:
        with patch.object(type(self.window), "_save_experiment_control_settings") as save_mock:
            self.window.apply_assignment_table_state(color_palette_entries=[("Blue", "#0000FF")])
        save_mock.assert_called_once()

    def test_round_trip_through_state_dict(self) -> None:
        self.window.apply_assignment_table_state(
            valve_state_labels={"Open": "Loaded", "Close": "Waste"},
            color_palette_entries=[("Blue", "#0000FF")],
            switch_solution_labels=["Buffer A", "", "Buffer C"],
        )
        state = self.window.assignment_table_state()

        other = _make_window(self)
        other.apply_assignment_table_state(**state)
        self.assertEqual(other.assignment_table_state(), state)


if __name__ == "__main__":
    unittest.main()
