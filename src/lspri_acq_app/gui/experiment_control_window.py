"""LSPRi acq's experiment-control panel: the same pump/valve/selector plan
table, timeline, and run/hold/pause/stop loop sLSPR acq's own panel uses,
reused rather than rebuilt (per the maintainer's explicit confirmation that
this app drives the same fluidics hardware - see the architecture plan's
`LspriAcqExperimentControlBackend` checklist item and the 2026-08-09
build-log entries for the full extraction history).

Built from the shared pieces landed across Tiers 0-2 of that extraction:

- `lspr_acq_shell.pump_plan` - the `PumpPlanStep` domain model (Tier 0)
- `lspr_acq_shell.experiment_control_import`/`_export` - plan file I/O (Tier 0)
- `lspr_acq_shell.experiment_control_step_runner` - the async hardware-
  command dispatch mechanism, `_PlannedCommand`/`_StepApplyRunnable` (Tier 0)
- `lspr_acq_shell.experiment_control_timeline`/`_widgets` - the plan
  timeline and table view/color delegate (Tier 1)
- `lspr_acq_shell.experiment_control_step_decision` - `plan_step_commands()`,
  what hardware commands a step transition requires (Tier 2)
- `lspr_acq_shell.experiment_control_run_loop.PlanRunLoopMixin` - the
  run/hold/pause/stop state machine itself (Tier 2)
- `lspr_acq_shell.device_io_pool` - the single-lane device-command thread pool

What's deliberately NOT reused/built yet (a first working panel, not full
parity - see the 2026-08-09 build-log entry for the scope decision):

- The plan table's cell-editing model: this window uses a new, lean
  `gui.plan_table_model.PlanTableModel` (plain Qt text/combo editing)
  instead of sLSPR acq's 1,123-line `flow_plan_model.ExperimentPlanTableModel`
  + its window-coupled dropdown-picker delegates - that layer wasn't traced
  as safely shareable without real redesign work (Tier-3-equivalent), and
  wasn't needed for a first working panel.
  - Per-channel tube diameter IS controlled the same way as sLSPR acq -
    one `TubeDiameterComboBox` per channel (`self.tube_diameter_spins`,
    same shared widget from Tier 1), feeding `plan_step_commands`'
    `tube_mm_by_channel`. Simplified relative to sLSPR acq's version: no
    "uniform" toggle button that drives all four channels from one control
    (sLSPR acq's `manual_uniform_button`) - always independent per-channel
    controls here. Added 2026-08-09.
  - The pause-state template (what PAUSE actually sends to the pump/valve/
    selector) IS editable, same as sLSPR acq - but via a second, tiny
    one-row instance of the same `PlanTableModel`/`ExperimentControlTableView`
    (`self.pause_template_table`/`self._pause_template_model`), not sLSPR
    acq's dedicated themed `QDialog` (`ExperimentControlDialogs.edit_pause_state`,
    part of the not-shared Tier-3 dialog layer) - same editable fields
    (duration/valve/switch/4 channels/color/comment), reusing what already
    exists rather than porting a whole new dialog. `duration_s` is stored
    but unused (the pause step is applied once via `_apply_step_to_pump_async`,
    not run through the timer). Added 2026-08-09.
  - No recording/HDF5 integration - `_request_recording_control` always
    succeeds and `_emit_experimental_control_state` only logs, since the
    sweep-pipeline/session-recording flow for this app is a separate,
    not-yet-built milestone. Running the plan drives real pump/valve/
    selector hardware; it does not yet log a session file.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import Callable

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from lspr_acq_shell.communication_models import DeviceCommand
from lspr_acq_shell.device_io_pool import device_io_pool
from lspr_acq_shell.device_lifecycle import device_label_for
from lspr_acq_shell.device_manager import DeviceCommunicationService
from lspr_acq_shell.device_types import PUMP, SELECTOR, SWITCH
from lspr_acq_shell.experiment_control_run_loop import PlanRunLoopMixin
from lspr_acq_shell.experiment_control_step_decision import StepCommandContext, plan_step_commands
from lspr_acq_shell.experiment_control_step_runner import _StepApplyResult, _StepApplyRunnable
from lspr_acq_shell.experiment_control_timeline import PumpPlanTimelineWidget
from lspr_acq_shell.experiment_control_widgets import ExperimentControlTableView, PlanColorDelegate, TubeDiameterComboBox
from lspr_acq_shell.pump_plan import ACTIVE_PUMP_CHANNELS, PumpChannelStep, PumpPlanStep, recompute_plan_timing

from lspri_acq_app.gui.plan_table_model import PlanTableModel

_LOGGER = logging.getLogger("lspri_acq_app.experiment_control")

_COLOR_COLUMN = 4 + 2 * ACTIVE_PUMP_CHANNELS  # matches plan_table_model's layout


class ExperimentControlWindow(PlanRunLoopMixin, QWidget):
    """Pump/valve/selector experiment-control panel for LSPRi acq.

    Implements the "host" contract `PlanRunLoopMixin` documents; see this
    module's docstring for what's shared vs. simplified for this first
    working version.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._device_comm_service = DeviceCommunicationService.shared()

        # Runtime state PlanRunLoopMixin owns - initialized here exactly like
        # sLSPR acq's own ExperimentControlWindow.__init__, since the mixin
        # only supplies methods, not attribute initialization.
        self._plan_running = False
        self._plan_holding = False
        self._plan_paused = False
        self._plan_active_row: int | None = None
        self._plan_elapsed_s = 0.0
        self._plan_resume_elapsed_s = 0.0
        self._plan_runtime_s = 0.0
        self._plan_resume_runtime_s = 0.0
        self._plan_started_monotonic: float | None = None
        self._step_started_monotonic: float | None = None
        self._measurement_started_monotonic: float | None = None
        self._applied_plan_step: PumpPlanStep | None = None
        self._paused_plan_step: PumpPlanStep | None = None
        self._pending_experiment_control_start_after_recording: tuple[bool, int | None] | None = None
        self._step_apply_inflight = 0

        self._plan_timer = QTimer(self)
        self._plan_timer.setSingleShot(True)
        self._plan_timer.timeout.connect(self._advance_experiment_control_progress)

        # No session-recording flow built for this app yet (see module
        # docstring) - a real, always-unchecked stand-in so
        # _start_or_resume_experiment_control's recording gate is a no-op.
        self.record_with_flow_button = QPushButton()
        self.record_with_flow_button.setCheckable(True)
        self.record_with_flow_button.setChecked(False)
        self.record_with_flow_button.hide()
        self.recording_controller = None

        self._table_model = PlanTableModel(recompute_plan_timing(_default_plan_steps()))
        self.plan_table = ExperimentControlTableView(self)
        self.plan_table.setModel(self._table_model)
        self.plan_table.setProperty("experiment_control_edit_mode", True)
        self.plan_table.setItemDelegateForColumn(_COLOR_COLUMN, PlanColorDelegate(self.plan_table))
        self.plan_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.plan_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.plan_table.step_move_requested.connect(self._on_step_move_requested)

        self.timeline_widget = PumpPlanTimelineWidget(self)

        self.tube_diameter_spins: list[TubeDiameterComboBox] = []
        tube_row = QHBoxLayout()
        tube_row.addWidget(QLabel("Tube diameter (mm):", self))
        for channel in range(1, ACTIVE_PUMP_CHANNELS + 1):
            tube_row.addWidget(QLabel(f"CH{channel}", self))
            spin = TubeDiameterComboBox(self)
            spin.setToolTip(f"Tubing inner diameter for CH{channel} in mm. Only the pump's supported sizes are selectable.")
            self.tube_diameter_spins.append(spin)
            tube_row.addWidget(spin)
        tube_row.addStretch(1)

        # Pause template: what gets sent to the pump/valve/selector when
        # Pause is clicked - reuses the exact same table/model/color-delegate
        # machinery as the main plan table (see module docstring) rather
        # than a dedicated dialog, since it's just editing another
        # PumpPlanStep.
        self._pause_template_model = PlanTableModel([_default_pause_step()])
        self.pause_template_table = ExperimentControlTableView(self)
        self.pause_template_table.setModel(self._pause_template_model)
        self.pause_template_table.setProperty("experiment_control_edit_mode", True)
        self.pause_template_table.setItemDelegateForColumn(_COLOR_COLUMN, PlanColorDelegate(self.pause_template_table))
        self.pause_template_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.pause_template_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.pause_template_table.setFixedHeight(64)
        self.pause_template_table.setToolTip("What the pump/valve/selector are set to when Pause is clicked.")
        pause_label = QLabel("Pause state (sent when Pause is clicked):", self)

        self.status_label = QLabel("Ready.", self)

        self.run_button = QPushButton("Run", self)
        self.hold_button = QPushButton("Hold", self)
        self.pause_button = QPushButton("Pause", self)
        self.stop_button = QPushButton("Stop", self)
        self.add_step_button = QPushButton("Add step", self)
        self.duplicate_step_button = QPushButton("Duplicate", self)
        self.delete_step_button = QPushButton("Delete", self)
        self.run_button.clicked.connect(self._run_experiment_control)
        self.hold_button.clicked.connect(self._hold_experiment_control)
        self.pause_button.clicked.connect(self._pause_experiment_control)
        self.stop_button.clicked.connect(self._stop_experiment_control)
        self.add_step_button.clicked.connect(self._on_add_step_clicked)
        self.duplicate_step_button.clicked.connect(self._on_duplicate_step_clicked)
        self.delete_step_button.clicked.connect(self._on_delete_step_clicked)

        toolbar = QHBoxLayout()
        toolbar.addWidget(self.add_step_button)
        toolbar.addWidget(self.duplicate_step_button)
        toolbar.addWidget(self.delete_step_button)
        toolbar.addStretch(1)
        toolbar.addWidget(self.run_button)
        toolbar.addWidget(self.hold_button)
        toolbar.addWidget(self.pause_button)
        toolbar.addWidget(self.stop_button)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(toolbar)
        layout.addLayout(tube_row)
        layout.addWidget(self.plan_table, 1)
        layout.addWidget(pause_label)
        layout.addWidget(self.pause_template_table)
        layout.addWidget(self.timeline_widget)
        layout.addWidget(self.status_label)
        self.setLayout(layout)

        self._sync_experiment_control_timeline(self._read_experiment_control_steps(), None)
        self._update_experiment_control_toggle_button()

    # ═══════════════════════════════════════════════════════════════════
    # PlanRunLoopMixin host contract
    # ═══════════════════════════════════════════════════════════════════

    def _read_experiment_control_steps(self) -> list[PumpPlanStep]:
        return recompute_plan_timing(self._table_model.steps())

    def _selected_experiment_control_row(self) -> int | None:
        row = self.plan_table.currentRow()
        return row if row >= 0 else None

    def _select_experiment_control_plan_row(self, plan_row: int | None) -> None:
        if plan_row is None:
            self.plan_table.setCurrentCell(-1, -1)
            return
        self.plan_table.selectRow(plan_row)

    def _load_selected_step_into_editor(self) -> None:
        # No separate single-step editor panel in this version - the table
        # itself is the editor (see module docstring).
        return

    def _update_timeline_selection(self) -> None:
        row = self._plan_active_row if (self._plan_running or self._plan_holding or self._plan_paused) else self._selected_experiment_control_row()
        self._sync_experiment_control_timeline(self._read_experiment_control_steps(), row)

    @property
    def _step_apply_pending(self) -> bool:
        return self._step_apply_inflight > 0

    def _apply_step_to_pump_async(
        self,
        step: PumpPlanStep,
        *,
        start: bool,
        on_success: Callable[[], None] | None = None,
    ) -> None:
        switch_controller_type, switch_port = self._service_connection_detail(SWITCH)
        context = StepCommandContext(
            wait_for_mswitch_first=False,
            pump_label=device_label_for(PUMP),
            valve_label=device_label_for(SWITCH),
            switch_label=device_label_for(SELECTOR),
            pump_connected=self._service_device_connected(PUMP),
            valve_connected=self._service_device_connected(SWITCH),
            mswitch_connected=self._service_device_connected(SELECTOR),
            tube_mm_by_channel=[spin.value() for spin in self.tube_diameter_spins],
            pump_backsteps=0,
            pump_roller_count=8,
            pump_display_enabled=False,
            plan_running=self._plan_running,
            plan_holding=self._plan_holding,
            switch_controller_type=switch_controller_type,
            switch_port=switch_port,
        )
        try:
            commands, needs_mswitch_refresh, pre_status = plan_step_commands(step, self._applied_plan_step, context, start=start)
        except Exception as exc:
            _LOGGER.error("Step plan failed (async) | step=%s error=%s", step.step, exc)
            self._set_status_message(f"Step apply failed: {exc}")
            return
        self._applied_plan_step = step
        self._step_apply_inflight += 1
        runnable = _StepApplyRunnable(
            self._device_comm_service, commands, step, needs_mswitch_refresh, pre_status, on_success
        )
        runnable.signals.done.connect(self._on_step_apply_async_done)
        device_io_pool().start(runnable)

    def _on_step_apply_async_done(self, result: _StepApplyResult) -> None:
        self._step_apply_inflight = max(0, self._step_apply_inflight - 1)
        status = "; ".join(result.status_messages)
        self._set_status_message(
            ((" | ".join(result.status_messages) + " | ") if result.status_messages else "")
            + f"Applied experiment-plan step {result.step.step}."
        )
        self._emit_experimental_control_state("step_applied", result.step, status=status)
        if result.success and result.on_success is not None:
            result.on_success()

    def _sync_experiment_control_timeline(self, steps: list[PumpPlanStep], plan_row: int | None, *, refresh_status: bool = False) -> None:
        self.timeline_widget.set_steps(
            steps,
            plan_row,
            self._timeline_progress_for_display(),
            self._plan_runtime_for_display(),
            self._step_runtime_for_display(),
            already_normalized=True,
        )
        if refresh_status:
            self._refresh_status_line()

    def _refresh_status_line(self) -> None:
        snapshot = self._experiment_runtime_snapshot()
        self.status_label.setText(f"Plan {snapshot.payload_state}.")

    def _update_experiment_control_toggle_button(self) -> None:
        self.run_button.setEnabled(not self._plan_running)
        self.hold_button.setEnabled(self._plan_running)
        self.pause_button.setEnabled(self._plan_running or self._plan_holding)
        self.stop_button.setEnabled(self._plan_running or self._plan_holding or self._plan_paused)

    def _set_status_message(self, message: str) -> None:
        self.status_label.setText(message)

    def _emit_experimental_control_state(self, event: str, step: PumpPlanStep | None = None, *, status: str = "") -> None:
        # No session-recording flow for this app yet (see module docstring) -
        # logged for visibility, not persisted anywhere.
        _LOGGER.info("Experiment-control state | event=%s step=%s status=%s", event, getattr(step, "step", None), status)

    def _service_device_connected(self, device_key: str) -> bool:
        label = device_label_for(device_key)
        try:
            return bool(self._device_comm_service.is_connected(label))
        except Exception:
            return False

    def _service_connection_detail(self, device_key: str) -> tuple[str | None, str | None]:
        label = device_label_for(device_key)
        connection = self._device_comm_service.connection(label)
        if connection is None:
            return None, None
        return (
            str(getattr(connection, "controller_type", None) or type(connection).__name__ or ""),
            str(getattr(connection, "port", None) or ""),
        )

    def _stop_all_channels(self) -> None:
        if not self._service_device_connected(PUMP):
            self._set_status_message("Pump offline. Nothing to stop.")
            return
        label = device_label_for(PUMP)
        result = self._device_comm_service.send_command(
            label, DeviceCommand("pump.stop_all", {"channel_count": ACTIVE_PUMP_CHANNELS})
        )
        if not result.success:
            _LOGGER.warning("pump.stop_all failed | error=%s", result.error)
        self._set_status_message("Experiment plan stopped.")

    def _pause_row_step(self) -> PumpPlanStep:
        # Live-edited via self.pause_template_table/self._pause_template_model
        # (see module docstring) - a deepcopy so external code can't mutate
        # the template through the returned step.
        return deepcopy(self._pause_template_model.steps()[0])

    def _request_recording_control(self, action: str) -> bool:
        # No session-recording flow for this app yet (see module docstring).
        return True

    def _run_gui_callback_timed(self, label: str, callback: Callable[[], None]) -> None:
        callback()

    # ═══════════════════════════════════════════════════════════════════
    # Toolbar actions
    # ═══════════════════════════════════════════════════════════════════

    def _on_add_step_clicked(self) -> None:
        row = self._selected_experiment_control_row()
        new_row = self._table_model.insert_step_after(row if row is not None else len(self._table_model.steps()) - 1)
        self.plan_table.selectRow(new_row)
        self._sync_experiment_control_timeline(self._read_experiment_control_steps(), self._plan_active_row)

    def _on_duplicate_step_clicked(self) -> None:
        row = self._selected_experiment_control_row()
        if row is None:
            return
        new_row = self._table_model.duplicate_step(row)
        if new_row is not None:
            self.plan_table.selectRow(new_row)
        self._sync_experiment_control_timeline(self._read_experiment_control_steps(), self._plan_active_row)

    def _on_delete_step_clicked(self) -> None:
        row = self._selected_experiment_control_row()
        if row is None or self._plan_running or self._plan_holding or self._plan_paused:
            return
        self._table_model.remove_step(row)
        self._sync_experiment_control_timeline(self._read_experiment_control_steps(), self._plan_active_row)

    def _on_step_move_requested(self, delta: int) -> None:
        row = self.plan_table.currentRow()
        if row < 0:
            return
        new_row = self._table_model.move_step(row, delta)
        if new_row is not None:
            self.plan_table.selectRow(new_row)
        self._sync_experiment_control_timeline(self._read_experiment_control_steps(), self._plan_active_row)


def _default_plan_steps() -> list[PumpPlanStep]:
    return [
        PumpPlanStep(
            step=1, duration_s=60.0, color="#4E79A7", valve="Open", switch_position=1,
            description="Step 1",
            channels=[PumpChannelStep() for _ in range(ACTIVE_PUMP_CHANNELS)],
        )
    ]


def _default_pause_step() -> PumpPlanStep:
    # All-stop starting point, matching sLSPR acq's own default pause
    # template (experiment_control_window.py's _experiment_control_pause_template) -
    # closes the valve and stops every channel. Fully editable afterward via
    # self.pause_template_table, unlike the fixed template this replaced.
    return PumpPlanStep(
        step=0, duration_s=0.0, color="#B44A4A", valve="Close", switch_position=1,
        description="Pause", channels=[PumpChannelStep() for _ in range(ACTIVE_PUMP_CHANNELS)],
    )
