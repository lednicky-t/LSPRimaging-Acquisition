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

Visual-parity effort (started 2026-08-09, per explicit maintainer request to
match sLSPR acq's look/behavior - staged across sessions given the true
scope: ~4,000+ un-shared lines across sLSPR acq's dialogs, table delegates,
and editing controller, comparable to Tier 2's own size):

- Theme (`_theme_palette`/`_apply_style`) and the icon toolbar
  (add/duplicate/remove/edit-toggle/import/export, plus the run-control row
  - run/hold/pause/stop/previous/next, all using the same icons, colors,
  and object names sLSPR acq's does, via the newly-shared
  `lspr_acq_shell.experiment_control_builders`) landed 2026-08-09. Import/
  export buttons are present with matching icons/tooltips but are NOT wired
  to real file I/O yet (`_on_import_plan_clicked`/`_on_export_plan_clicked`
  just report "not implemented").
- Still to come, in later sessions: the manual single-step editor row
  (Duration/CHs/Dir/Tube/Flow/CH1-4/Valve/Color/Comment, including the
  "CHs" uniform/per-channel toggle and switch-solution combo), the real
  `flow_plan_model.ExperimentPlanTableModel` + its 8 delegates (replacing
  the lean `PlanTableModel` above), and the Tier-3 dialog layer (color
  palette, valve labels, pump display settings, pause-state dialog).
"""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import Callable

from PyQt6.QtCore import QSize, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from lspr_ui import flow_tabler_icon, tint_tabler_icon, transport_icon

from lspr_acq_shell.communication_models import DeviceCommand
from lspr_acq_shell.device_io_pool import device_io_pool
from lspr_acq_shell.device_lifecycle import device_label_for
from lspr_acq_shell.device_manager import DeviceCommunicationService
from lspr_acq_shell.device_types import PUMP, SELECTOR, SWITCH
from lspr_acq_shell.experiment_control_builders import create_flow_step_action_button
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

        # Matches sLSPR acq's own ExperimentControlWindow, which currently
        # forces dark mode regardless of any setting - see its _theme_mode
        # assignment for why. No light-mode support here yet either.
        self._theme_mode = "dark"

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

        # Icon toolbar: same icons/colors/tooltips as sLSPR acq's
        # experiment_control_window.py (add_step_button/duplicate_step_button/
        # remove_step_button/apply_step_button/import_plan_button/
        # export_plan_button), added 2026-08-09 as the first increment of a
        # planned multi-session visual-parity effort (see module docstring).
        # Import/export are present but inert for now - real CSV/native-plan
        # file I/O wiring is a later increment, not yet built here.
        self.add_step_button = create_flow_step_action_button(
            tint_tabler_icon(flow_tabler_icon("square_plus"), QColor("#47a861")),
            "Add a step after the selected row.",
        )
        self.duplicate_step_button = create_flow_step_action_button(
            tint_tabler_icon(flow_tabler_icon("copy"), QColor("#4f88ff")),
            "Duplicate the selected step.",
        )
        self.remove_step_button = create_flow_step_action_button(
            tint_tabler_icon(flow_tabler_icon("trash"), QColor("#b44a4a")),
            "Remove the selected step.",
        )
        self.apply_step_button = create_flow_step_action_button(
            tint_tabler_icon(flow_tabler_icon("edit"), QColor("#e8d85f")),
            "Toggle table edit mode.",
        )
        self.apply_step_button.setCheckable(True)
        self.apply_step_button.setChecked(True)
        self.import_plan_button = create_flow_step_action_button(
            tint_tabler_icon(flow_tabler_icon("file_import"), QColor("#66d48a")),
            "Import an experiment plan from CSV or TXT. (Not yet wired in this app.)",
        )
        self.export_plan_button = create_flow_step_action_button(
            tint_tabler_icon(flow_tabler_icon("file_export"), QColor("#8fbaff")),
            "Export the current experiment plan to CSV or TXT. (Not yet wired in this app.)",
        )
        self.add_step_button.clicked.connect(self._on_add_step_clicked)
        self.duplicate_step_button.clicked.connect(self._on_duplicate_step_clicked)
        self.remove_step_button.clicked.connect(self._on_delete_step_clicked)
        self.import_plan_button.clicked.connect(self._on_import_plan_clicked)
        self.export_plan_button.clicked.connect(self._on_export_plan_clicked)

        edit_toolbar = QHBoxLayout()
        edit_toolbar.setSpacing(2)
        edit_toolbar.addWidget(self.add_step_button)
        edit_toolbar.addWidget(self.apply_step_button)
        edit_toolbar.addWidget(self.duplicate_step_button)
        edit_toolbar.addWidget(self.remove_step_button)
        edit_toolbar.addWidget(self.import_plan_button)
        edit_toolbar.addWidget(self.export_plan_button)
        edit_toolbar.addStretch(1)

        # Run-control row - same transport icons as sLSPR acq's
        # plan_toggle_button/hold_plan_button/pause_plan_button/
        # stop_plan_button/previous_step_button/next_step_button.
        self.run_button = self._make_icon_button(transport_icon(self._theme_mode, "play"), "Run or resume the plan.")
        self.hold_button = self._make_icon_button(transport_icon(self._theme_mode, "hold"), "Hold plan.")
        self.hold_button.setCheckable(True)
        self.pause_button = self._make_icon_button(transport_icon(self._theme_mode, "pause"), "Pause plan.")
        self.pause_button.setCheckable(True)
        self.stop_button = self._make_icon_button(transport_icon(self._theme_mode, "stop"), "Stop plan.")
        self.previous_step_button = self._make_icon_button(transport_icon(self._theme_mode, "previous"), "Previous step.")
        self.next_step_button = self._make_icon_button(transport_icon(self._theme_mode, "next"), "Next step.")
        self.run_button.clicked.connect(self._run_experiment_control)
        self.hold_button.clicked.connect(self._hold_experiment_control)
        self.pause_button.clicked.connect(self._pause_experiment_control)
        self.stop_button.clicked.connect(self._stop_experiment_control)
        self.previous_step_button.clicked.connect(lambda: self._move_to_relative_experiment_control_step(-1))
        self.next_step_button.clicked.connect(lambda: self._move_to_relative_experiment_control_step(1))

        run_toolbar = QHBoxLayout()
        run_toolbar.setSpacing(2)
        run_toolbar.addWidget(self.run_button)
        run_toolbar.addWidget(self.hold_button)
        run_toolbar.addWidget(self.pause_button)
        run_toolbar.addWidget(self.stop_button)
        run_toolbar.addWidget(self.previous_step_button)
        run_toolbar.addWidget(self.next_step_button)
        run_toolbar.addStretch(1)

        toolbar = QHBoxLayout()
        toolbar.addLayout(edit_toolbar)
        toolbar.addLayout(run_toolbar)

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

        self.plan_table.setObjectName("flowControlTable")
        self.pause_template_table.setObjectName("flowControlTable")
        self._apply_style()

        self._sync_experiment_control_timeline(self._read_experiment_control_steps(), None)
        self._update_experiment_control_toggle_button()

    def _make_icon_button(self, icon, tooltip: str) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName("flowIconButton")
        button.setAutoRaise(True)
        button.setIcon(icon)
        button.setToolTip(tooltip)
        button.setFixedSize(32, 32)
        button.setIconSize(QSize(24, 24))
        return button

    def _theme_palette(self) -> dict[str, str]:
        # Same dark palette as sLSPR acq's ExperimentControlWindow._theme_palette
        # (that method forces dark mode currently, so only the dark dict is
        # ported here - see this window's _theme_mode assignment).
        return {
            "bg": "#13161b",
            "fg": "#e6ebf1",
            "muted": "#a8b0ba",
            "field": "#171b21",
            "button": "#20252d",
            "button_hover": "#272d36",
            "button_pressed": "#303640",
            "accent_button": "#5d6876",
            "accent_hover": "#707d8c",
            "title": "#8fbaff",
            "danger_button": "#8f5a61",
            "danger_hover": "#a46a72",
            "border": "#2b3138",
            "border_hover": "#414852",
            "pressed": "#252b33",
            "scroll": "#49505a",
            "scroll_hover": "#5c6470",
            "splitter": "#2b3138",
            "timeline_bg": "#0f1216",
            "header": "#1b2026",
            "selection": "#252b33",
        }

    def _apply_style(self) -> None:
        # Ported verbatim from sLSPR acq's ExperimentControlWindow._apply_style
        # (object names below match sLSPR acq's exactly: flowIconButton,
        # flowStepActionButton, flowControlTable) so this app's panel picks
        # up the identical stylesheet rules.
        palette = self._theme_palette()
        self.setStyleSheet(
            """
            QWidget {
                background: %(bg)s;
                color: %(fg)s;
                font-size: 12px;
            }
            QToolTip {
                background-color: %(bg)s;
                color: %(fg)s;
                border: 1px solid %(border)s;
                padding: 4px 6px;
            }
            QPushButton, QToolButton, QComboBox, QDoubleSpinBox, QLineEdit, QTableWidget {
                background: %(field)s;
                border: 1px solid %(border)s;
                border-radius: 10px;
                padding: 4px 6px;
            }
            QSpinBox, QDoubleSpinBox {
                border-radius: 3px;
                padding: 1px 4px;
            }
            QSpinBox::up-button, QSpinBox::down-button,
            QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
                width: 0px;
                border: none;
                background: transparent;
            }
            QSpinBox::up-arrow, QSpinBox::down-arrow,
            QDoubleSpinBox::up-arrow, QDoubleSpinBox::down-arrow {
                width: 0px;
                height: 0px;
            }
            QPushButton:hover, QToolButton:hover, QComboBox:hover, QDoubleSpinBox:hover, QLineEdit:hover {
                border-color: %(border_hover)s;
                background: %(button_hover)s;
            }
            QPushButton:pressed, QToolButton:pressed {
                background: %(button_pressed)s;
            }
            QToolButton#flowIconButton {
                background: transparent;
                border: none;
                padding: 0px;
            }
            QToolButton#flowIconButton:hover {
                background: rgba(127, 127, 127, 0.10);
                border: none;
            }
            QToolButton#flowIconButton:pressed {
                background: rgba(127, 127, 127, 0.18);
                border: none;
            }
            QToolButton#flowIconButton:checked {
                background: rgba(102, 167, 255, 0.18);
                border: none;
            }
            QWidget#flowContent, QWidget#flowEditorContainer {
                background: %(bg)s;
                border: none;
            }
            QTableView#flowControlTable {
                background: %(bg)s;
                border: none;
                border-radius: 0px;
                gridline-color: %(border)s;
                alternate-background-color: %(button)s;
                selection-background-color: transparent;
                selection-color: %(fg)s;
                font-size: 11px;
            }
            QTableView#flowControlTable::viewport {
                background: %(bg)s;
                border: none;
            }
            QTableView#flowControlTable::item {
                border: none;
                padding: 1px 4px;
            }
            QTableView#flowControlTable QComboBox,
            QTableView#flowControlTable QDoubleSpinBox,
            QTableView#flowControlTable QLineEdit,
            QTableView#flowControlTable QToolButton {
                background: transparent;
                border: none;
                padding: 0px 1px;
                margin: 0px;
            }
            QTableView#flowControlTable::item:selected {
                background: transparent;
                background-color: transparent;
            }
            QTableView#flowControlTable::item:selected:active,
            QTableView#flowControlTable::item:selected:!active {
                background: transparent;
                background-color: transparent;
            }
            QTableView#flowControlTable QHeaderView::section {
                background: %(header)s;
                border: none;
                border-right: 1px solid %(border)s;
                border-bottom: 1px solid %(border)s;
                padding: 0px 1px;
                font-size: 10px;
                font-weight: 600;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 8px;
            }
            QScrollBar::handle:vertical {
                background: %(scroll)s;
                border-radius: 4px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover {
                background: %(scroll_hover)s;
            }
            QSplitter::handle {
                background: %(splitter)s;
            }
            QSplitter::handle:vertical {
                height: 6px;
                margin: 0 4px;
                border-radius: 3px;
            }
            """ % palette
        )

    def _on_import_plan_clicked(self) -> None:
        # Not yet wired - real CSV/native-plan file import is a later
        # increment of the visual-parity effort (see module docstring).
        self._set_status_message("Import is not implemented in this app yet.")

    def _on_export_plan_clicked(self) -> None:
        # Not yet wired - see _on_import_plan_clicked.
        self._set_status_message("Export is not implemented in this app yet.")

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
        self.hold_button.setChecked(self._plan_holding)
        self.pause_button.setEnabled(self._plan_running or self._plan_holding)
        self.pause_button.setChecked(self._plan_paused)
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
