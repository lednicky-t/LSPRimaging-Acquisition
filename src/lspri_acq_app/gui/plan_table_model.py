"""Lean, editable table model over a pump-plan step list for LSPRi acq's
experiment-control panel.

Deliberately simpler than sLSPR acq's `flow_plan_model.ExperimentPlanTableModel`
(1,123 lines with theme-aware dropdown-picker delegates for valve/switch/color,
tightly coupled to `ExperimentControlWindow` via a `self._window` reference on
each delegate) - that layer was traced and found not worth sharing or porting
for a first working panel (see the 2026-08-09 build-log entry): plain Qt text/
combo editing here, no custom popups. Pairs with the already-shared
`lspr_acq_shell.experiment_control_widgets.ExperimentControlTableView` and
`PlanColorDelegate` (Tier 1) - those only ever needed a model exposing the
standard `QAbstractTableModel` API plus `rowCount()`/`columnCount()`/
`currentRow()`/`selectRow()` on the *view*, which `ExperimentControlTableView`
already provides.
"""

from __future__ import annotations

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt

from lspr_acq_shell.pump_plan import (
    PumpChannelStep,
    PumpPlanStep,
    normalized_pump_direction,
    normalized_valve_state,
)
from lspr_acq_shell.reglo_icc import ACTIVE_PUMP_CHANNELS

_COLUMN_STEP = 0
_COLUMN_DURATION = 1
_COLUMN_VALVE = 2
_COLUMN_SWITCH = 3
_COLUMN_CHANNEL_FLOW_START = 4
_COLUMN_CHANNEL_DIRECTION_START = _COLUMN_CHANNEL_FLOW_START + ACTIVE_PUMP_CHANNELS
_COLUMN_COLOR = _COLUMN_CHANNEL_DIRECTION_START + ACTIVE_PUMP_CHANNELS
_COLUMN_COMMENT = _COLUMN_COLOR + 1
_COLUMN_COUNT = _COLUMN_COMMENT + 1

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
        return 0 if parent.isValid() else _COLUMN_COUNT

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):  # noqa: N802 - Qt API
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole and 0 <= section < len(_HEADERS):
            return _HEADERS[section]
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:  # noqa: N802 - Qt API
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == _COLUMN_STEP:
            return base
        return base | Qt.ItemFlag.ItemIsEditable

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._steps)):
            return None
        if role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return None
        step = self._steps[index.row()]
        column = index.column()
        if column == _COLUMN_STEP:
            return int(step.step)
        if column == _COLUMN_DURATION:
            return f"{float(step.duration_s):g}"
        if column == _COLUMN_VALVE:
            return str(step.valve or "")
        if column == _COLUMN_SWITCH:
            return int(step.switch_position)
        if _COLUMN_CHANNEL_FLOW_START <= column < _COLUMN_CHANNEL_DIRECTION_START:
            channel_index = column - _COLUMN_CHANNEL_FLOW_START
            return f"{float(step.channels[channel_index].flow_ul_min):g}"
        if _COLUMN_CHANNEL_DIRECTION_START <= column < _COLUMN_COLOR:
            channel_index = column - _COLUMN_CHANNEL_DIRECTION_START
            return normalized_pump_direction(step.channels[channel_index].direction)
        if column == _COLUMN_COLOR:
            return str(step.color or "#4E79A7")
        if column == _COLUMN_COMMENT:
            return str(step.description or "")
        return None

    def setData(self, index: QModelIndex, value: object, role: int = Qt.ItemDataRole.EditRole) -> bool:  # noqa: N802 - Qt API
        if role != Qt.ItemDataRole.EditRole or not index.isValid() or not (0 <= index.row() < len(self._steps)):
            return False
        column = index.column()
        if column == _COLUMN_STEP:
            return False
        step = self._steps[index.row()]
        try:
            if column == _COLUMN_DURATION:
                step.duration_s = max(float(value), 0.0)
            elif column == _COLUMN_VALVE:
                step.valve = normalized_valve_state(value) if str(value).strip().lower() in ("open", "close") else str(value).strip()
            elif column == _COLUMN_SWITCH:
                step.switch_position = max(min(int(value), 12), 1)
            elif _COLUMN_CHANNEL_FLOW_START <= column < _COLUMN_CHANNEL_DIRECTION_START:
                channel_index = column - _COLUMN_CHANNEL_FLOW_START
                step.channels[channel_index].flow_ul_min = max(float(value), 0.0)
            elif _COLUMN_CHANNEL_DIRECTION_START <= column < _COLUMN_COLOR:
                channel_index = column - _COLUMN_CHANNEL_DIRECTION_START
                step.channels[channel_index].direction = normalized_pump_direction(value)
            elif column == _COLUMN_COLOR:
                step.color = str(value).strip() or "#4E79A7"
            elif column == _COLUMN_COMMENT:
                step.description = str(value)
            else:
                return False
        except (TypeError, ValueError):
            return False
        self.dataChanged.emit(index, index, [role])
        return True

    def insert_step_after(self, row: int) -> int:
        """Insert a default step after *row* (or at the end if `row < 0`); returns the new row index."""
        insert_at = row + 1 if 0 <= row < len(self._steps) else len(self._steps)
        new_step = PumpPlanStep(
            step=insert_at + 1,
            duration_s=60.0,
            channels=[PumpChannelStep() for _ in range(ACTIVE_PUMP_CHANNELS)],
        )
        self.beginInsertRows(QModelIndex(), insert_at, insert_at)
        self._steps.insert(insert_at, new_step)
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
