"""Lean, editable table model + delegates over a pump-plan step list for
LSPRi acq's experiment-control panel.

`PlanTableModel` is deliberately simpler than sLSPR acq's
`flow_plan_model.ExperimentPlanTableModel` (1,123 lines total) - but traced
that real file rather than assuming: the *model* class there takes no
`window` reference at all (configured via plain setters -
`set_theme_palette`/`set_valve_state_colors`/etc.), so it's already
portable. The complexity is entirely in its 8 delegate classes, each
constructed with `window` and calling back into it for theme colors, combo
population, and editor-lifecycle hooks (event filters, wheel-scroll
suppression, auto-opening popups).

Given that, and that `PlanTableModel` here already has 58 tests built
around its own column layout (different from sLSPR acq's), swapping in the
real model would mean reworking column indices and delegate wiring across
already-working, tested code for uncertain benefit - so this file instead
keeps its own model and adds lean, real delegates (`ValveDelegate`,
`SwitchSolutionDelegate`, `DirectionDelegate`) built from pieces already in
`ExperimentControlWindow` (`_valve_state_label`, `_switch_display_text`,
`direction_glyph`) rather than porting the 8 sLSPR acq classes verbatim.
No custom popup width/wheel-scroll/auto-open behavior - a `QComboBox`
editor plus a `displayText()` override for friendly read-only rendering.

Pairs with the already-shared
`lspr_acq_shell.experiment_control_widgets.ExperimentControlTableView` and
`PlanColorDelegate` (Tier 1) - those only ever needed a model exposing the
standard `QAbstractTableModel` API plus `rowCount()`/`columnCount()`/
`currentRow()`/`selectRow()` on the *view*, which `ExperimentControlTableView`
already provides.
"""

from __future__ import annotations

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PyQt6.QtWidgets import QComboBox, QStyledItemDelegate

from lspr_acq_shell.experiment_control_builders import direction_glyph
from lspr_acq_shell.pump_plan import (
    PumpChannelStep,
    PumpPlanStep,
    normalized_pump_direction,
    normalized_valve_state,
)
from lspr_acq_shell.reglo_icc import ACTIVE_PUMP_CHANNELS

COLUMN_STEP = 0
COLUMN_DURATION = 1
COLUMN_VALVE = 2
COLUMN_SWITCH = 3
COLUMN_CHANNEL_FLOW_START = 4
COLUMN_CHANNEL_DIRECTION_START = COLUMN_CHANNEL_FLOW_START + ACTIVE_PUMP_CHANNELS
COLUMN_COLOR = COLUMN_CHANNEL_DIRECTION_START + ACTIVE_PUMP_CHANNELS
COLUMN_COMMENT = COLUMN_COLOR + 1
COLUMN_COUNT = COLUMN_COMMENT + 1

_HEADERS = (
    ["Step", "Duration (s)", "Valve", "Switch"]
    + [f"CH{i + 1} Flow" for i in range(ACTIVE_PUMP_CHANNELS)]
    + [f"CH{i + 1} Dir" for i in range(ACTIVE_PUMP_CHANNELS)]
    + ["Color", "Comment"]
)


class PlanTableModel(QAbstractTableModel):
    """Editable table over `list[PumpPlanStep]`.

    Emits no dedicated "steps changed" signal - callers read the current
    steps back via :meth:`steps` after any edit/insert/delete, matching how
    `ExperimentControlTableView`'s own signals (`step_move_requested`, etc.)
    are handled by its owner rather than by the model itself.
    """

    def __init__(self, steps: list[PumpPlanStep] | None = None, parent=None) -> None:
        super().__init__(parent)
        self._steps: list[PumpPlanStep] = list(steps) if steps else []

    def steps(self) -> list[PumpPlanStep]:
        return list(self._steps)

    def set_steps(self, steps: list[PumpPlanStep]) -> None:
        self.beginResetModel()
        self._steps = list(steps)
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802 - Qt API
        return 0 if parent.isValid() else len(self._steps)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802 - Qt API
        return 0 if parent.isValid() else COLUMN_COUNT

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):  # noqa: N802 - Qt API
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole and 0 <= section < len(_HEADERS):
            return _HEADERS[section]
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:  # noqa: N802 - Qt API
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == COLUMN_STEP:
            return base
        return base | Qt.ItemFlag.ItemIsEditable

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._steps)):
            return None
        if role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return None
        step = self._steps[index.row()]
        column = index.column()
        if column == COLUMN_STEP:
            return int(step.step)
        if column == COLUMN_DURATION:
            return f"{float(step.duration_s):g}"
        if column == COLUMN_VALVE:
            return str(step.valve or "")
        if column == COLUMN_SWITCH:
            return int(step.switch_position)
        if COLUMN_CHANNEL_FLOW_START <= column < COLUMN_CHANNEL_DIRECTION_START:
            channel_index = column - COLUMN_CHANNEL_FLOW_START
            return f"{float(step.channels[channel_index].flow_ul_min):g}"
        if COLUMN_CHANNEL_DIRECTION_START <= column < COLUMN_COLOR:
            channel_index = column - COLUMN_CHANNEL_DIRECTION_START
            return normalized_pump_direction(step.channels[channel_index].direction)
        if column == COLUMN_COLOR:
            return str(step.color or "#4E79A7")
        if column == COLUMN_COMMENT:
            return str(step.description or "")
        return None

    def setData(self, index: QModelIndex, value: object, role: int = Qt.ItemDataRole.EditRole) -> bool:  # noqa: N802 - Qt API
        if role != Qt.ItemDataRole.EditRole or not index.isValid() or not (0 <= index.row() < len(self._steps)):
            return False
        column = index.column()
        if column == COLUMN_STEP:
            return False
        step = self._steps[index.row()]
        try:
            if column == COLUMN_DURATION:
                step.duration_s = max(float(value), 0.0)
            elif column == COLUMN_VALVE:
                step.valve = normalized_valve_state(value) if str(value).strip().lower() in ("open", "close") else str(value).strip()
            elif column == COLUMN_SWITCH:
                step.switch_position = max(min(int(value), 12), 1)
            elif COLUMN_CHANNEL_FLOW_START <= column < COLUMN_CHANNEL_DIRECTION_START:
                channel_index = column - COLUMN_CHANNEL_FLOW_START
                step.channels[channel_index].flow_ul_min = max(float(value), 0.0)
            elif COLUMN_CHANNEL_DIRECTION_START <= column < COLUMN_COLOR:
                channel_index = column - COLUMN_CHANNEL_DIRECTION_START
                step.channels[channel_index].direction = normalized_pump_direction(value)
            elif column == COLUMN_COLOR:
                step.color = str(value).strip() or "#4E79A7"
            elif column == COLUMN_COMMENT:
                step.description = str(value)
            else:
                return False
        except (TypeError, ValueError):
            return False
        self.dataChanged.emit(index, index, [role])
        return True

    def insert_step_after(self, row: int) -> int:
        """Insert a default step after *row* (or at the end if `row < 0`); returns the new row index."""
        new_step = PumpPlanStep(
            step=1,
            duration_s=60.0,
            channels=[PumpChannelStep() for _ in range(ACTIVE_PUMP_CHANNELS)],
        )
        return self.insert_step(row, new_step)

    def insert_step(self, row: int, step: PumpPlanStep) -> int:
        """Insert *step* after *row* (or at the end if `row < 0`); returns the new row index."""
        insert_at = row + 1 if 0 <= row < len(self._steps) else len(self._steps)
        self.beginInsertRows(QModelIndex(), insert_at, insert_at)
        self._steps.insert(insert_at, step)
        self.endInsertRows()
        return insert_at

    def duplicate_step(self, row: int) -> int | None:
        if not (0 <= row < len(self._steps)):
            return None
        from copy import deepcopy

        clone = deepcopy(self._steps[row])
        insert_at = row + 1
        self.beginInsertRows(QModelIndex(), insert_at, insert_at)
        self._steps.insert(insert_at, clone)
        self.endInsertRows()
        return insert_at

    def remove_step(self, row: int) -> bool:
        if not (0 <= row < len(self._steps)):
            return False
        self.beginRemoveRows(QModelIndex(), row, row)
        del self._steps[row]
        self.endRemoveRows()
        return True

    def move_step(self, row: int, delta: int) -> int | None:
        target = row + delta
        if not (0 <= row < len(self._steps)) or not (0 <= target < len(self._steps)):
            return None
        self.beginMoveRows(
            QModelIndex(), row, row, QModelIndex(), target if target < row else target + 1
        )
        self._steps[row], self._steps[target] = self._steps[target], self._steps[row]
        self.endMoveRows()
        return target


class ValveDelegate(QStyledItemDelegate):
    """Open/Close dropdown, showing the window's custom valve labels
    (`window._valve_state_label`) - the model itself keeps storing the raw
    "Open"/"Close" string; only how it's *displayed* and *edited* changes."""

    def __init__(self, window, parent=None) -> None:
        super().__init__(parent)
        self._window = window

    def displayText(self, value, _locale) -> str:  # noqa: N802 - Qt API
        return self._window._valve_state_label(str(value or "Open"))

    def createEditor(self, parent, _option, _index):  # noqa: N802 - Qt API
        combo = QComboBox(parent)
        for state in ("Open", "Close"):
            combo.addItem(self._window._valve_state_label(state), state)
        return combo

    def setEditorData(self, editor: QComboBox, index) -> None:  # noqa: N802 - Qt API
        current = str(index.data(Qt.ItemDataRole.EditRole) or "Open")
        found = editor.findData(current)
        editor.setCurrentIndex(found if found >= 0 else 0)

    def setModelData(self, editor: QComboBox, model, index) -> None:  # noqa: N802 - Qt API
        model.setData(index, editor.currentData(), Qt.ItemDataRole.EditRole)


class SwitchSolutionDelegate(QStyledItemDelegate):
    """Position dropdown ("N: solution name"), showing the window's
    switch-solution labels (`window._switch_display_text`) - the model
    keeps storing the raw 1-12 position int."""

    def __init__(self, window, parent=None) -> None:
        super().__init__(parent)
        self._window = window

    def displayText(self, value, _locale) -> str:  # noqa: N802 - Qt API
        try:
            position = int(value)
        except (TypeError, ValueError):
            position = 1
        return self._window._switch_display_text(position)

    def createEditor(self, parent, _option, _index):  # noqa: N802 - Qt API
        combo = QComboBox(parent)
        for position in range(1, 13):
            combo.addItem(self._window._switch_display_text(position), position)
        return combo

    def setEditorData(self, editor: QComboBox, index) -> None:  # noqa: N802 - Qt API
        try:
            position = int(index.data(Qt.ItemDataRole.EditRole))
        except (TypeError, ValueError):
            position = 1
        editor.setCurrentIndex(max(min(position, 12), 1) - 1)

    def setModelData(self, editor: QComboBox, model, index) -> None:  # noqa: N802 - Qt API
        model.setData(index, editor.currentData(), Qt.ItemDataRole.EditRole)


class DirectionDelegate(QStyledItemDelegate):
    """CW/CCW dropdown, showing the same rotation glyphs
    (`lspr_acq_shell.experiment_control_builders.direction_glyph`) the
    manual editor row's direction buttons use."""

    def createEditor(self, parent, _option, _index):  # noqa: N802 - Qt API
        combo = QComboBox(parent)
        for direction in ("CW", "CCW"):
            combo.addItem(f"{direction_glyph(direction)} {direction}", direction)
        return combo

    def setEditorData(self, editor: QComboBox, index) -> None:  # noqa: N802 - Qt API
        current = normalized_pump_direction(index.data(Qt.ItemDataRole.EditRole))
        found = editor.findData(current)
        editor.setCurrentIndex(found if found >= 0 else 0)

    def setModelData(self, editor: QComboBox, model, index) -> None:  # noqa: N802 - Qt API
        model.setData(index, editor.currentData(), Qt.ItemDataRole.EditRole)
