"""Tests for gui/plan_table_model.py - PlanTableModel itself (column
layout, get/set) plus the three real delegates added 2026-08-09
(ValveDelegate/SwitchSolutionDelegate/DirectionDelegate). See that file's
module docstring for why these are lean equivalents of sLSPR acq's 8
delegate classes, not ports of them.
"""

from __future__ import annotations

import unittest

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QComboBox

from lspr_acq_shell.pump_plan import ACTIVE_PUMP_CHANNELS, PumpChannelStep, PumpPlanStep
from lspri_acq_app.gui.plan_table_model import (
    COLUMN_CHANNEL_DIRECTION_START,
    COLUMN_COMMENT,
    COLUMN_STEP,
    COLUMN_SWITCH,
    COLUMN_VALVE,
    DirectionDelegate,
    PlanTableModel,
    SwitchSolutionDelegate,
    ValveDelegate,
)

_APP = QApplication.instance() or QApplication([])


def _step(**overrides) -> PumpPlanStep:
    defaults = dict(
        step=1, duration_s=60.0, color="#4E79A7", valve="Open", switch_position=1,
        description="", channels=[PumpChannelStep() for _ in range(ACTIVE_PUMP_CHANNELS)],
    )
    defaults.update(overrides)
    return PumpPlanStep(**defaults)


class PlanTableModelBasicsTests(unittest.TestCase):
    def test_step_column_is_not_editable(self) -> None:
        model = PlanTableModel([_step()])
        flags = model.flags(model.index(0, COLUMN_STEP))
        self.assertFalse(flags & Qt.ItemFlag.ItemIsEditable)

    def test_other_columns_are_editable(self) -> None:
        model = PlanTableModel([_step()])
        flags = model.flags(model.index(0, COLUMN_VALVE))
        self.assertTrue(flags & Qt.ItemFlag.ItemIsEditable)

    def test_comment_round_trips(self) -> None:
        model = PlanTableModel([_step()])
        model.setData(model.index(0, COLUMN_COMMENT), "hello")
        self.assertEqual(model.data(model.index(0, COLUMN_COMMENT)), "hello")


class _FakeWindow:
    """Minimal stand-in providing exactly the two methods
    ValveDelegate/SwitchSolutionDelegate call back into - not the real
    ExperimentControlWindow, so these tests can run without constructing
    the whole app; the real window's own use of these delegates is covered
    by test_experiment_control_window.py's real-widget tests."""

    def __init__(self) -> None:
        self.valve_labels = {"Open": "Open", "Close": "Close"}
        self.switch_labels = ["" for _ in range(12)]

    def _valve_state_label(self, valve: str) -> str:
        normalized = "Close" if str(valve).strip().lower() == "close" else "Open"
        return self.valve_labels.get(normalized, normalized)

    def _switch_display_text(self, position: int) -> str:
        label = self.switch_labels[position - 1].strip() or "empty"
        return f"{position}: {label}"


class ValveDelegateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.window = _FakeWindow()
        self.delegate = ValveDelegate(self.window)
        self.model = PlanTableModel([_step(valve="Open")])

    def test_display_text_uses_the_windows_custom_label(self) -> None:
        self.window.valve_labels["Open"] = "Loaded"
        index = self.model.index(0, COLUMN_VALVE)
        text = self.delegate.displayText(index.data(Qt.ItemDataRole.DisplayRole), None)
        self.assertEqual(text, "Loaded")

    def test_editor_round_trip_writes_close_back_to_the_model(self) -> None:
        index = self.model.index(0, COLUMN_VALVE)
        editor = self.delegate.createEditor(None, None, index)
        self.assertIsInstance(editor, QComboBox)
        self.delegate.setEditorData(editor, index)
        self.assertEqual(editor.currentData(), "Open")
        editor.setCurrentIndex(editor.findData("Close"))
        self.delegate.setModelData(editor, self.model, index)
        self.assertEqual(self.model.data(index), "Close")


class SwitchSolutionDelegateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.window = _FakeWindow()
        self.delegate = SwitchSolutionDelegate(self.window)
        self.model = PlanTableModel([_step(switch_position=1)])

    def test_display_text_shows_the_solution_name(self) -> None:
        self.window.switch_labels[2] = "Buffer A"
        self.model.setData(self.model.index(0, COLUMN_SWITCH), 3)
        index = self.model.index(0, COLUMN_SWITCH)
        text = self.delegate.displayText(index.data(Qt.ItemDataRole.DisplayRole), None)
        self.assertEqual(text, "3: Buffer A")

    def test_editor_round_trip_writes_the_selected_position(self) -> None:
        index = self.model.index(0, COLUMN_SWITCH)
        editor = self.delegate.createEditor(None, None, index)
        self.delegate.setEditorData(editor, index)
        self.assertEqual(editor.currentIndex(), 0)  # position 1
        editor.setCurrentIndex(6)  # position 7
        self.delegate.setModelData(editor, self.model, index)
        self.assertEqual(self.model.data(index), 7)


class DirectionDelegateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.delegate = DirectionDelegate()
        self.model = PlanTableModel([_step()])
        self.column = COLUMN_CHANNEL_DIRECTION_START

    def test_editor_offers_cw_and_ccw(self) -> None:
        index = self.model.index(0, self.column)
        editor = self.delegate.createEditor(None, None, index)
        self.assertEqual(editor.count(), 2)
        self.assertEqual({editor.itemData(i) for i in range(2)}, {"CW", "CCW"})

    def test_editor_round_trip_writes_ccw_back_to_the_model(self) -> None:
        index = self.model.index(0, self.column)
        editor = self.delegate.createEditor(None, None, index)
        self.delegate.setEditorData(editor, index)
        self.assertEqual(editor.currentData(), "CW")
        editor.setCurrentIndex(editor.findData("CCW"))
        self.delegate.setModelData(editor, self.model, index)
        self.assertEqual(self.model.data(index), "CCW")


if __name__ == "__main__":
    unittest.main()
