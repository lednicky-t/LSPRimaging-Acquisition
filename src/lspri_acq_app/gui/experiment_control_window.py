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
- The manual single-step editor row (Duration/Valve/Color/Switch/Comment
  plus per-channel Flow/Direction) landed 2026-08-09, matching sLSPR acq's
  own `_current_editor_step` field mapping exactly - `add_step_button` now
  composes a real `PumpPlanStep` from these widgets (was a bare default
  step before). Simplified relative to sLSPR acq: no time-unit toggle
  (duration is always seconds), no "CHs" uniform/per-channel toggle for
  direction (always per-channel, same simplification already made for tube
  diameter), no switch-solution combo (just the raw 1-12 spin), and the
  settings-gear buttons next to Valve/Color/Switch/Comment are present with
  matching icons; Valve and Color are now real (see below), Switch and
  Comment are still inert. Tube diameter is deliberately NOT part of this
  row (see `self.tube_diameter_spins` above) - it isn't step data at all.
- Valve-label and color-palette editing (`step_valve_settings_button`/
  `color_palette_button`) landed 2026-08-09 - real, working dialogs, but
  lean standard `QDialog`s (a form with line edits + color-picker buttons;
  a table with add/remove-row buttons), not sLSPR acq's custom frameless-
  bordered, gradient-outlined ones (`ExperimentControlDialogs.edit_valve_labels`/
  `edit_color_palette_entries`, ~185/~300 lines each) - same editable data
  (label+color per valve state; name+color pairs for the palette), simpler
  chrome.
- Switch-solution and pump-display dialogs (`step_switch_settings_button`/
  `step_comment_display_button`) landed 2026-08-09, same lean-dialog
  pattern. The switch editor traced sLSPR acq's *actual current* behavior
  rather than its field names: `step_switch_spin` (a raw 1-12 spinbox) was
  removed entirely in favor of `step_switch_combo` ("N: solution name")
  since sLSPR acq's own `_set_switch_solution_mode` unconditionally hides
  the spin and the mode-toggle button regardless of their arguments - that
  code path is effectively dead there now. sLSPR acq's switch-solution
  dialog also has per-position Concentration/Unit/Notes fields; only the
  Solution label is edited here, since nothing in this app reads the
  others. The pump-display dialog is wired to a setting with a real
  effect: `StepCommandContext.pump_display_enabled` (was hardcoded `False`
  before this dialog existed) - checking it means step comments actually
  get sent to the pump's own 16-character display. Its "highlight the
  limit in the plan table" checkbox is left out (needs per-cell
  highlighting the lean `PlanTableModel` doesn't implement).
- Settings persistence landed 2026-08-09, using the already-shared
  `lspr_acq_shell.settings_store` engine directly (its own module docstring
  gives this exact usage pattern for a second app) - valve labels/colors,
  the color palette, switch-solution labels, the pump-display setting, and
  tube diameters now survive an app restart, saved to this app's own
  `lspri_acq_settings.json` (not sLSPR acq's `lspr_settings.json`). The
  plan itself is deliberately NOT persisted this way - it's project/session
  state, not a UI setting, same distinction sLSPR acq makes.
- Real import/export landed 2026-08-09, using the already-shared
  `lspr_acq_shell.experiment_control_import`/`_export` (Tier 0) directly -
  `ExperimentPlanImportTask`/`ExperimentPlanExportTask` do the actual file
  I/O off the GUI thread; this window only builds/consumes their payloads.
  Export is native YAML only (`_build_native_experiment_plan_document`,
  field-for-field the same schema as sLSPR acq's own, so plans exported
  from either app open in the other) - sLSPR acq's legacy compat CSV/TXT
  export formats (25-column layouts for external tool interop) are
  deliberately not built here. Import accepts native YAML, CSV/TSV, and
  HDF5 (all three "just work" since `ExperimentPlanImportTask` itself
  dispatches by file suffix - no extra code needed for HDF5 support beyond
  what CSV/YAML already required) - imported colors and tube diameters are
  merged in and persisted; imported valve-label/switch-solution overrides
  are not (sLSPR acq's importer does merge those; skipped here as a further
  simplification, since nothing exercises that path without real HDF5
  measurement files from this app to import from anyway).
- Real per-cell delegates landed 2026-08-09, closing the last item from the
  visual-parity punch list - but not by porting sLSPR acq's
  `flow_plan_model.ExperimentPlanTableModel` + its 8 delegate classes.
  Traced that file first: the *model* class there takes no `window`
  reference at all (configured via plain setters), so it's genuinely
  portable - but this window's own `gui.plan_table_model.PlanTableModel`
  already has 58+ tests built around its own (different) column layout, so
  swapping in the real model would have meant reworking column indices and
  delegate wiring across already-working, tested code for uncertain
  benefit. The actual complexity is entirely in the 8 delegates, which
  *are* deeply `window`-coupled (event filters, wheel-scroll suppression,
  auto-opening popups, exact popup-width calculations). Built 3 lean, real
  delegates instead (`ValveDelegate`/`SwitchSolutionDelegate`/
  `DirectionDelegate` in `gui/plan_table_model.py`) from pieces already in
  this window (`_valve_state_label`, `_switch_display_text`,
  `direction_glyph`) - real dropdown editors and `displayText()` overrides
  for friendly read-only rendering, no custom popup sizing/wheel-scroll/
  auto-open behavior. `PlanColorDelegate` (Tier 1) was already wired.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QSize, Qt, QThreadPool, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
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
from lspr_acq_shell.reglo_icc import PUMP_DISPLAY_MAX_LENGTH
from lspr_acq_shell.experiment_control_builders import (
    create_direction_button,
    create_flow_step_action_button,
    set_direction_button,
    set_step_valve_button_state_for_button,
)
from lspr_acq_shell.experiment_control_export import ExperimentPlanExportData, ExperimentPlanExportTask
from lspr_acq_shell.experiment_control_import import (
    ExperimentPlanImportData,
    ExperimentPlanImportTask,
    build_experiment_plan_steps_from_import_data,
)
from lspr_acq_shell.experiment_control_run_loop import PlanRunLoopMixin
from lspr_acq_shell.experiment_control_step_decision import StepCommandContext, plan_step_commands
from lspr_acq_shell.experiment_control_step_runner import _StepApplyResult, _StepApplyRunnable
from lspr_acq_shell.experiment_control_timeline import PumpPlanTimelineWidget
from lspr_acq_shell.experiment_control_widgets import ExperimentControlTableView, PlanColorDelegate, TubeDiameterComboBox
from lspr_acq_shell.pump_plan import ACTIVE_PUMP_CHANNELS, PLAN_COLOR_OPTIONS, PumpChannelStep, PumpPlanStep, recompute_plan_timing
from lspr_acq_shell.settings_store import load_app_setting, save_app_setting
from lspr_acq_shell.user_profile import current_config_path

from lspri_acq_app.gui.plan_table_model import (
    COLUMN_CHANNEL_DIRECTION_START,
    COLUMN_COLOR,
    COLUMN_SWITCH,
    COLUMN_VALVE,
    DirectionDelegate,
    PlanTableModel,
    SwitchSolutionDelegate,
    ValveDelegate,
)

_LOGGER = logging.getLogger("lspri_acq_app.experiment_control")

# Own settings file, not sLSPR acq's "lspr_settings.json" - each app gets
# its own per-user settings file under the same shared config directory
# (lspr_acq_shell.user_profile.current_config_path), matching the pattern
# lspr_acq_shell.settings_store's own module docstring recommends.
_SETTINGS_FILENAME = "lspri_acq_settings.json"


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

        # Settings persistence - added 2026-08-09, using the already-shared
        # lspr_acq_shell.settings_store engine directly (its own module
        # docstring gives this exact usage pattern for a second app). One
        # blob under a single "experiment_control" app-setting key, loaded
        # once here and re-read at each state-initialization point below;
        # saved again after each dialog's Accept and after a tube-diameter
        # change. Does NOT include the plan itself - that's project/session
        # state, not a UI setting, same distinction sLSPR acq makes.
        self._persisted_settings: dict = load_app_setting(
            "experiment_control", {}, path=self._settings_path()
        ) or {}

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
        self._experiment_plan_export_generation = 0
        self._experiment_plan_import_generation = 0

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

        # Manual single-step editor row - Duration/Valve/Color/Switch/Comment
        # plus per-channel Flow/Direction, matching sLSPR acq's editor fields
        # exactly (same PumpPlanStep mapping as its _current_editor_step) -
        # added 2026-08-09 as part of the staged visual-parity effort. Tube
        # diameter is deliberately NOT here (see self.tube_diameter_spins
        # below) - it isn't step data at all (PumpPlanStep has no tube_mm
        # field), so sLSPR acq's own _current_editor_step never reads it
        # either; it only looks like part of the same row there for layout
        # compactness.
        self.step_duration_spin = QDoubleSpinBox(self)
        self.step_duration_spin.setRange(0.0, 86400.0)
        self.step_duration_spin.setDecimals(1)
        self.step_duration_spin.setSingleStep(5.0)
        self.step_duration_spin.setValue(60.0)
        self.step_duration_spin.setSuffix(" s")
        self.step_duration_spin.setToolTip("Step duration in seconds.")

        self.manual_flow_spins: list[QDoubleSpinBox] = []
        self.manual_direction_buttons: list[QToolButton] = []
        channel_columns = QHBoxLayout()
        channel_columns.setSpacing(4)
        for channel in range(1, ACTIVE_PUMP_CHANNELS + 1):
            channel_column = QVBoxLayout()
            channel_column.setSpacing(2)
            channel_column.addWidget(_centered_label(f"CH{channel}"))
            flow_spin = QDoubleSpinBox(self)
            flow_spin.setRange(0.0, 10000.0)
            flow_spin.setDecimals(0)
            flow_spin.setSingleStep(1.0)
            flow_spin.setMaximumWidth(82)
            flow_spin.setToolTip(f"Flow rate for CH{channel} in uL/min.")
            direction_button = create_direction_button(self, "CW")
            direction_button.setMaximumWidth(40)
            direction_button.clicked.connect(
                lambda _checked=False, b=direction_button: self._toggle_direction_button(b)
            )
            self.manual_flow_spins.append(flow_spin)
            self.manual_direction_buttons.append(direction_button)
            channel_column.addWidget(flow_spin)
            channel_column.addWidget(direction_button)
            channel_columns.addLayout(channel_column)

        # Valve state labels/colors - editable via step_valve_settings_button
        # (a lean QDialog, not sLSPR acq's custom frameless-bordered one -
        # see _edit_valve_state_labels's docstring).
        self._valve_state_labels: dict[str, str] = self._persisted_settings.get(
            "valve_state_labels", {"Open": "Open", "Close": "Close"}
        )
        self._valve_state_colors: dict[str, str] = self._persisted_settings.get(
            "valve_state_colors", {"Open": "#4E79A7", "Close": "#B44A4A"}
        )

        self.step_valve_button = QToolButton(self)
        self.step_valve_button.setCheckable(True)
        self.step_valve_button.setAutoRaise(True)
        set_step_valve_button_state_for_button(self, self.step_valve_button, "Open")
        self.step_valve_button.clicked.connect(self._on_toggle_step_valve_button)
        self.step_valve_settings_button = create_flow_step_action_button(
            tint_tabler_icon(flow_tabler_icon("settings"), QColor("#f0f3f7")),
            "Edit the text labels used for valve states.",
        )
        self.step_valve_settings_button.clicked.connect(self._edit_valve_state_labels)

        persisted_palette = self._persisted_settings.get("color_palette_entries")
        self._color_palette_entries: list[tuple[str, str]] = (
            [(str(name), str(color)) for name, color in persisted_palette]
            if isinstance(persisted_palette, list) and persisted_palette
            else list(PLAN_COLOR_OPTIONS)
        )

        self.step_color_combo = QComboBox(self)
        self.step_color_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._populate_color_combo(self.step_color_combo)
        self.step_color_combo.setToolTip("Step color used in the plan timeline for quick visual identification.")
        self.color_palette_button = create_flow_step_action_button(
            tint_tabler_icon(flow_tabler_icon("settings"), QColor("#f0f3f7")),
            "Edit and overwrite the color palette used by the dropdown.",
        )
        self.color_palette_button.clicked.connect(self._edit_color_palette_entries)

        # Switch position editor: a "N: label" combo, not a raw 1-12 spin -
        # matching sLSPR acq's *actual current* behavior, not its field
        # names. Traced rather than assumed: sLSPR acq's own
        # _set_switch_solution_mode unconditionally forces
        # step_switch_spin.setVisible(False)/step_switch_combo.setVisible(True)
        # regardless of its `enabled` argument or the stored
        # _switch_solution_mode setting - the raw-spin/mode-toggle path is
        # effectively dead code there now, so it isn't built here either.
        persisted_switch_labels = self._persisted_settings.get("switch_solution_labels")
        self._switch_solution_labels: list[str] = (
            [str(label) for label in persisted_switch_labels[:12]] + [""] * max(0, 12 - len(persisted_switch_labels))
            if isinstance(persisted_switch_labels, list)
            else [""] * 12
        )
        self.step_switch_combo = QComboBox(self)
        self.step_switch_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._populate_switch_solution_combo(self.step_switch_combo, 1)
        self.step_switch_combo.setToolTip("AMF switch position and solution for this step.")
        self.step_switch_settings_button = create_flow_step_action_button(
            tint_tabler_icon(flow_tabler_icon("settings"), QColor("#f0f3f7")),
            "Edit the switch solution labels.",
        )
        self.step_switch_settings_button.clicked.connect(self._edit_switch_solution_labels)

        # Real, already-consequential setting: feeds StepCommandContext.pump_display_enabled
        # (was hardcoded False before this dialog existed) - whether a
        # step's comment actually gets sent to the pump's own 16-character
        # display when that step is applied to real hardware.
        self._pump_display_enabled = bool(self._persisted_settings.get("pump_display_enabled", False))

        self.step_comment_edit = QLineEdit(self)
        self.step_comment_edit.setPlaceholderText("Comment")
        self.step_comment_edit.setToolTip("Free-text note for the step. It is shown in the timeline when there is enough space.")
        self.step_comment_display_button = create_flow_step_action_button(
            tint_tabler_icon(flow_tabler_icon("settings"), QColor("#f0f3f7")),
            "Show all step comments on the pump display.",
        )
        self.step_comment_display_button.clicked.connect(self._edit_pump_display_settings)

        editor_row = QGridLayout()
        editor_row.setHorizontalSpacing(6)
        editor_row.setVerticalSpacing(2)
        editor_row.addWidget(_centered_label("Duration"), 0, 0)
        editor_row.addWidget(self.step_duration_spin, 1, 0)
        editor_row.addWidget(_centered_label("Valve"), 0, 1)
        valve_cell = QHBoxLayout()
        valve_cell.addWidget(self.step_valve_button)
        valve_cell.addWidget(self.step_valve_settings_button)
        editor_row.addLayout(valve_cell, 1, 1)
        editor_row.addWidget(_centered_label("Color"), 0, 2)
        color_cell = QHBoxLayout()
        color_cell.addWidget(self.step_color_combo)
        color_cell.addWidget(self.color_palette_button)
        editor_row.addLayout(color_cell, 1, 2)
        editor_row.addWidget(_centered_label("Switch"), 0, 3)
        switch_cell = QHBoxLayout()
        switch_cell.addWidget(self.step_switch_combo)
        switch_cell.addWidget(self.step_switch_settings_button)
        editor_row.addLayout(switch_cell, 1, 3)
        editor_row.addWidget(_centered_label("Comment"), 0, 4)
        comment_cell = QHBoxLayout()
        comment_cell.addWidget(self.step_comment_edit, 1)
        comment_cell.addWidget(self.step_comment_display_button)
        editor_row.addLayout(comment_cell, 1, 4)
        editor_row.setColumnStretch(4, 1)
        editor_row.addLayout(channel_columns, 0, 5, 2, 1)

        self._table_model = PlanTableModel(recompute_plan_timing(_default_plan_steps()))
        self.plan_table = ExperimentControlTableView(self)
        self.plan_table.setModel(self._table_model)
        self.plan_table.setProperty("experiment_control_edit_mode", True)
        self._install_plan_table_delegates(self.plan_table)
        self.plan_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.plan_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.plan_table.step_move_requested.connect(self._on_step_move_requested)

        self.timeline_widget = PumpPlanTimelineWidget(self)

        persisted_tube_mm = self._persisted_settings.get("tube_mm_by_channel")
        if not (isinstance(persisted_tube_mm, list) and len(persisted_tube_mm) == ACTIVE_PUMP_CHANNELS):
            persisted_tube_mm = None
        self.tube_diameter_spins: list[TubeDiameterComboBox] = []
        tube_row = QHBoxLayout()
        tube_row.addWidget(QLabel("Tube diameter (mm):", self))
        for channel in range(1, ACTIVE_PUMP_CHANNELS + 1):
            tube_row.addWidget(QLabel(f"CH{channel}", self))
            spin = TubeDiameterComboBox(self)
            spin.setToolTip(f"Tubing inner diameter for CH{channel} in mm. Only the pump's supported sizes are selectable.")
            if persisted_tube_mm is not None:
                spin.setValue(float(persisted_tube_mm[channel - 1]))
            spin.valueChanged.connect(self._save_experiment_control_settings)
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
        self._install_plan_table_delegates(self.pause_template_table)
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
        self.add_step_button.clicked.connect(self._add_experiment_control_step_from_editor)
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
        layout.addLayout(editor_row)
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

    def _build_native_experiment_plan_document(self) -> dict[str, object]:
        """Native YAML export schema - matches sLSPR acq's
        `_build_native_experiment_plan_document` field-for-field, so a plan
        exported from either app opens correctly in the other. Its legacy
        compat CSV/TXT export formats (25-column layouts for external tool
        interop, not core functionality) are deliberately not built here -
        native YAML is this app's own primary format too.
        """
        steps = recompute_plan_timing(self._read_experiment_control_steps())
        tube_mm_by_channel = [spin.value() for spin in self.tube_diameter_spins]
        return {
            "format": {"name": "LSPR Experiment Plan", "version": 1},
            "metadata": {
                "created_by": "LSPRimaging Acquisition",
                "exported_at": datetime.now().isoformat(timespec="seconds"),
                "notes": "",
            },
            "units": {"flow": "uL/min", "time": "s", "tube_diameter": "mm"},
            "devices": {
                "pumps": {
                    "pump_1": {
                        "label": "Pump 1",
                        "channels": {
                            f"ch{channel_index}": {
                                "label": f"CH{channel_index}",
                                "tube_mm": float(tube_mm_by_channel[channel_index - 1]),
                            }
                            for channel_index in range(1, ACTIVE_PUMP_CHANNELS + 1)
                        },
                    }
                },
                "valves": {
                    "valve_1": {
                        "display_labels": {
                            "open": self._valve_state_label("Open"),
                            "close": self._valve_state_label("Close"),
                        },
                    }
                },
                "switches": {
                    "switch_1": {"ports": {position: self._switch_solution_label(position) for position in range(1, 13)}}
                },
            },
            "steps": [
                {
                    "id": step.step,
                    "duration_s": float(step.duration_s),
                    "color": str(step.color or self._default_experiment_control_color(step.step - 1)),
                    "comment": str(step.description or ""),
                    "devices": {
                        "pump_1": {
                            f"ch{channel_index + 1}": {
                                "flow": float(step.channels[channel_index].flow_ul_min),
                                "direction": str(step.channels[channel_index].direction or "OFF"),
                            }
                            for channel_index in range(ACTIVE_PUMP_CHANNELS)
                        },
                        "valve_1": {"state": "close" if str(step.valve or "").strip().lower() == "close" else "open"},
                        "switch_1": {"port": int(max(min(int(step.switch_position), 12), 1))},
                    },
                }
                for step in steps
            ],
        }

    def _on_export_plan_clicked(self) -> None:
        steps = self._read_experiment_control_steps()
        if not steps:
            self._set_status_message("There is no experiment plan to export.")
            return
        file_path, _selected_filter = QFileDialog.getSaveFileName(
            self, "Export experiment plan", "experiment_plan.flow.yaml",
            "Native YAML (*.flow.yaml *.yaml *.yml);;All files (*)",
        )
        if not file_path:
            return
        path = Path(file_path)
        if not path.suffix:
            path = path.with_suffix(".flow.yaml")
        self._experiment_plan_export_generation += 1
        generation = self._experiment_plan_export_generation
        task = ExperimentPlanExportTask(generation, ExperimentPlanExportData(
            path=path, document=self._build_native_experiment_plan_document(),
        ))
        task.signals.finished.connect(self._on_experiment_plan_export_finished)
        task.signals.failed.connect(self._on_experiment_plan_export_failed)
        self._set_status_message(f"Exporting experiment plan to {path.name}...")
        QThreadPool.globalInstance().start(task)

    def _on_experiment_plan_export_finished(self, generation: int, payload: object) -> None:
        if generation != self._experiment_plan_export_generation or not isinstance(payload, ExperimentPlanExportData):
            return
        self._set_status_message(f"Exported experiment plan to {payload.path.name}.")

    def _on_experiment_plan_export_failed(self, generation: int, message: str) -> None:
        if generation != self._experiment_plan_export_generation:
            return
        self._set_status_message(f"Could not export experiment plan: {message}")

    def _on_import_plan_clicked(self) -> None:
        file_path, _selected_filter = QFileDialog.getOpenFileName(
            self, "Import experiment plan", "",
            "Experiment plan files (*.flow.yaml *.yaml *.yml *.csv *.txt *.h5 *.hdf5);;All files (*)",
        )
        if not file_path:
            return
        self._experiment_plan_import_generation += 1
        generation = self._experiment_plan_import_generation
        task = ExperimentPlanImportTask(generation, Path(file_path))
        task.signals.finished.connect(self._on_experiment_plan_import_finished)
        task.signals.failed.connect(self._on_experiment_plan_import_failed)
        self._set_status_message(f"Importing experiment plan from {Path(file_path).name}...")
        QThreadPool.globalInstance().start(task)

    def _on_experiment_plan_import_finished(self, generation: int, payload: object) -> None:
        if generation != self._experiment_plan_import_generation or not isinstance(payload, ExperimentPlanImportData):
            return
        steps = payload.steps
        if steps is None:
            steps = build_experiment_plan_steps_from_import_data(payload, l_is_open=True)
        if not steps:
            self._set_status_message(f"No steps found in {payload.path.name}.")
            return
        self._table_model.set_steps(recompute_plan_timing(steps))
        self.plan_table.selectRow(0)
        if payload.imported_colors:
            existing = {color for _name, color in self._color_palette_entries}
            for color in payload.imported_colors:
                if color not in existing:
                    self._color_palette_entries.append((color, color))
                    existing.add(color)
            self._populate_color_combo(self.step_color_combo)
        if payload.tube_mm_by_channel:
            for index, spin in enumerate(self.tube_diameter_spins):
                if index < len(payload.tube_mm_by_channel):
                    spin.setValue(float(payload.tube_mm_by_channel[index]))
        self._save_experiment_control_settings()
        self._sync_experiment_control_timeline(self._read_experiment_control_steps(), None)
        self._set_status_message(f"Imported experiment plan from {payload.path.name}.")

    def _on_experiment_plan_import_failed(self, generation: int, message: str) -> None:
        if generation != self._experiment_plan_import_generation:
            return
        self._set_status_message(f"Could not import experiment plan: {message}")

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
            pump_display_enabled=self._pump_display_enabled,
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

    def _install_plan_table_delegates(self, table: ExperimentControlTableView) -> None:
        """Real per-cell delegates (dropdown pickers, not plain text entry)
        for the plan table and the pause-template table - both use the same
        `PlanTableModel` column layout. See this module's `plan_table_model`
        import and that file's docstring for why these are lean equivalents
        of sLSPR acq's delegates, not ports of them."""
        table.setItemDelegateForColumn(COLUMN_VALVE, ValveDelegate(self, table))
        table.setItemDelegateForColumn(COLUMN_SWITCH, SwitchSolutionDelegate(self, table))
        direction_delegate = DirectionDelegate(table)
        for channel_index in range(ACTIVE_PUMP_CHANNELS):
            table.setItemDelegateForColumn(COLUMN_CHANNEL_DIRECTION_START + channel_index, direction_delegate)
        table.setItemDelegateForColumn(COLUMN_COLOR, PlanColorDelegate(table))

    # ═══════════════════════════════════════════════════════════════════
    # Settings persistence
    # ═══════════════════════════════════════════════════════════════════

    def _settings_path(self):
        return current_config_path(_SETTINGS_FILENAME)

    def _save_experiment_control_settings(self) -> None:
        save_app_setting(
            "experiment_control",
            {
                "valve_state_labels": self._valve_state_labels,
                "valve_state_colors": self._valve_state_colors,
                "color_palette_entries": [list(entry) for entry in self._color_palette_entries],
                "switch_solution_labels": self._switch_solution_labels,
                "pump_display_enabled": self._pump_display_enabled,
                "tube_mm_by_channel": [spin.value() for spin in self.tube_diameter_spins],
            },
            path=self._settings_path(),
        )

    # ═══════════════════════════════════════════════════════════════════
    # Toolbar actions
    # ═══════════════════════════════════════════════════════════════════

    def _populate_color_combo(self, combo: QComboBox) -> None:
        combo.clear()
        for label, color in self._color_palette_entries:
            combo.addItem(label, color)

    def _default_experiment_control_color(self, step_index: int) -> str:
        palette = self._color_palette_entries or list(PLAN_COLOR_OPTIONS)
        return palette[step_index % len(palette)][1]

    def _switch_solution_label(self, position: int) -> str:
        index = max(min(int(position), 12), 1) - 1
        if 0 <= index < len(self._switch_solution_labels):
            label = str(self._switch_solution_labels[index]).strip()
            if label:
                return label
        return "empty"

    def _switch_display_text(self, position: int) -> str:
        normalized = max(min(int(position), 12), 1)
        return f"{normalized}: {self._switch_solution_label(normalized)}"

    def _populate_switch_solution_combo(self, combo: QComboBox, selected_position: int | None = None) -> None:
        current_position = max(min(int(selected_position or 1), 12), 1)
        combo.blockSignals(True)
        combo.clear()
        for position in range(1, 13):
            combo.addItem(self._switch_display_text(position), position)
        combo.setCurrentIndex(current_position - 1)
        combo.blockSignals(False)

    def _current_switch_position_from_editor(self) -> int:
        data = self.step_switch_combo.currentData()
        if isinstance(data, (int, float)):
            return max(min(int(data), 12), 1)
        return max(min(self.step_switch_combo.currentIndex() + 1, 12), 1)

    def _edit_switch_solution_labels(self) -> None:
        """Edit the per-position solution name shown in the switch combo.

        A lean standard QDialog (a 12-row QTableWidget, one "Solution"
        column), not sLSPR acq's custom frameless-bordered one
        (`ExperimentControlDialogs.edit_switch_solution_labels`) - and
        deliberately simplified relative to it: sLSPR acq's dialog also has
        per-position Concentration/Unit/Notes fields; this only edits the
        label actually used anywhere in this app's own logic
        (`_switch_solution_labels`) - the other fields aren't read by
        anything here yet. Part of the staged visual-parity effort's
        dialog-layer slice.
        """
        dialog = QDialog(self)
        dialog.setWindowTitle("Switch solutions")
        layout = QVBoxLayout(dialog)

        table = QTableWidget(12, 2, dialog)
        table.setHorizontalHeaderLabels(["Position", "Solution"])
        table.verticalHeader().setVisible(False)
        for position in range(1, 13):
            row = position - 1
            position_item = QTableWidgetItem(str(position))
            position_item.setFlags(position_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(row, 0, position_item)
            table.setItem(row, 1, QTableWidgetItem(self._switch_solution_labels[row]))
        layout.addWidget(table)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, dialog)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        labels = []
        for row in range(12):
            item = table.item(row, 1)
            labels.append(item.text().strip() if item is not None else "")
        self._switch_solution_labels = labels
        current_position = self._current_switch_position_from_editor()
        self._populate_switch_solution_combo(self.step_switch_combo, current_position)
        self._save_experiment_control_settings()

    def _edit_pump_display_settings(self) -> None:
        """Toggle whether a step's comment is sent to the pump's own
        16-character display when that step is applied to real hardware -
        a lean `QCheckBox` + live preview dialog, not sLSPR acq's custom
        frameless-bordered one (`ExperimentControlDialogs.edit_pump_display_settings`).
        Unlike the other three dialogs in this file, this one is wired to a
        setting that already has a real effect: `_apply_step_to_pump_async`
        reads `self._pump_display_enabled` into `StepCommandContext`, which
        was hardcoded `False` before this dialog existed. The "highlight
        the limit in the plan table" checkbox sLSPR acq also has is left
        out - it needs per-cell highlighting the lean `PlanTableModel`
        doesn't implement.
        """
        dialog = QDialog(self)
        dialog.setWindowTitle("Pump display")
        layout = QVBoxLayout(dialog)

        checkbox = QCheckBox("Show step comments on pump display", dialog)
        checkbox.setChecked(self._pump_display_enabled)
        layout.addWidget(checkbox)

        preview = QLabel(dialog)
        preview.setToolTip(f"Preview - the pump display shows at most {PUMP_DISPLAY_MAX_LENGTH} characters.")
        layout.addWidget(preview)

        def _update_preview() -> None:
            text = self.step_comment_edit.text().strip()[:PUMP_DISPLAY_MAX_LENGTH]
            preview.setText(f"Preview: {text!r}" if checkbox.isChecked() else "Preview: (nothing sent)")

        checkbox.toggled.connect(_update_preview)
        _update_preview()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, dialog)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._pump_display_enabled = checkbox.isChecked()
        self._save_experiment_control_settings()

    def _edit_color_palette_entries(self) -> None:
        """Edit the name/color entries offered in the step-color dropdown.

        A lean standard QDialog (a QTableWidget with name/color-button rows
        plus add/remove buttons), not sLSPR acq's custom frameless-bordered,
        gradient-outlined one (`ExperimentControlDialogs.edit_color_palette_entries`,
        ~300 lines) - same editable data (name + color pairs), simpler
        chrome. Part of the staged visual-parity effort's dialog-layer slice.
        """
        dialog = QDialog(self)
        dialog.setWindowTitle("Color palette")
        layout = QVBoxLayout(dialog)

        table = QTableWidget(len(self._color_palette_entries), 2, dialog)
        table.setHorizontalHeaderLabels(["Name", "Color"])
        table.horizontalHeader().setStretchLastSection(True)

        def _add_row(row: int, name: str, color: str) -> None:
            table.setItem(row, 0, QTableWidgetItem(name))
            color_button = QPushButton(color, dialog)

            def _pick(_checked: bool = False, r: int = row) -> None:
                current = table.cellWidget(r, 1)
                chosen = QColorDialog.getColor(QColor(current.text()), dialog, "Palette color")
                if chosen.isValid():
                    current.setText(chosen.name().upper())

            color_button.clicked.connect(_pick)
            table.setCellWidget(row, 1, color_button)

        for row, (name, color) in enumerate(self._color_palette_entries):
            _add_row(row, name, color)
        layout.addWidget(table)

        row_buttons = QHBoxLayout()
        add_row_button = QPushButton("Add color", dialog)
        remove_row_button = QPushButton("Remove selected", dialog)
        row_buttons.addWidget(add_row_button)
        row_buttons.addWidget(remove_row_button)
        layout.addLayout(row_buttons)

        def _on_add_row() -> None:
            table.insertRow(table.rowCount())
            _add_row(table.rowCount() - 1, f"Custom {table.rowCount()}", "#4E79A7")

        def _on_remove_row() -> None:
            row = table.currentRow()
            if row >= 0:
                table.removeRow(row)

        add_row_button.clicked.connect(_on_add_row)
        remove_row_button.clicked.connect(_on_remove_row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, dialog)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        entries: list[tuple[str, str]] = []
        for row in range(table.rowCount()):
            name_item = table.item(row, 0)
            color_button = table.cellWidget(row, 1)
            name = (name_item.text().strip() if name_item is not None else "") or f"Custom {row + 1}"
            color = QColor(color_button.text() if color_button is not None else "")
            if color.isValid():
                entries.append((name, color.name().upper()))
        if not entries:
            entries = list(PLAN_COLOR_OPTIONS)
        self._color_palette_entries = entries
        current_color = str(self.step_color_combo.currentData() or "")
        self._populate_color_combo(self.step_color_combo)
        restored_index = self.step_color_combo.findData(current_color)
        if restored_index >= 0:
            self.step_color_combo.setCurrentIndex(restored_index)
        self._save_experiment_control_settings()

    def _valve_state_label(self, valve: str) -> str:
        normalized = "Close" if str(valve or "").strip().lower() == "close" else "Open"
        label = str(self._valve_state_labels.get(normalized, normalized)).strip()
        return label or normalized

    def _edit_valve_state_labels(self) -> None:
        """Edit the display text/color shown for the Open/Close valve states.

        A lean standard QDialog, not sLSPR acq's custom frameless-bordered
        one with its own themed color-picker table widget
        (`ExperimentControlDialogs.edit_valve_labels`, ~185 lines) - same
        editable data (a label + a color per state), simpler chrome. Part of
        the staged visual-parity effort's dialog-layer slice.
        """
        dialog = QDialog(self)
        dialog.setWindowTitle("Valve labels")
        form = QFormLayout(dialog)

        label_edits: dict[str, QLineEdit] = {}
        color_buttons: dict[str, QPushButton] = {}
        chosen_colors = dict(self._valve_state_colors)

        def _make_color_picker(state: str) -> QPushButton:
            button = QPushButton(chosen_colors[state], dialog)

            def _pick() -> None:
                color = QColorDialog.getColor(QColor(chosen_colors[state]), dialog, f"{state} color")
                if color.isValid():
                    chosen_colors[state] = color.name().upper()
                    button.setText(chosen_colors[state])

            button.clicked.connect(_pick)
            return button

        for state in ("Open", "Close"):
            edit = QLineEdit(self._valve_state_labels.get(state, state), dialog)
            label_edits[state] = edit
            color_button = _make_color_picker(state)
            color_buttons[state] = color_button
            row = QHBoxLayout()
            row.addWidget(edit)
            row.addWidget(color_button)
            form.addRow(f"{state}:", row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, dialog)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._valve_state_labels = {state: label_edits[state].text().strip() or state for state in ("Open", "Close")}
        self._valve_state_colors = chosen_colors
        current = str(self.step_valve_button.property("valve") or "Open")
        set_step_valve_button_state_for_button(self, self.step_valve_button, current)
        self._save_experiment_control_settings()

    def _on_toggle_step_valve_button(self) -> None:
        current = str(self.step_valve_button.property("valve") or "Open")
        next_state = "Close" if current != "Close" else "Open"
        set_step_valve_button_state_for_button(self, self.step_valve_button, next_state)

    def _current_editor_step(self, step_number: int) -> PumpPlanStep:
        color = self.step_color_combo.currentData()
        return PumpPlanStep(
            step=step_number,
            duration_s=max(self.step_duration_spin.value(), 0.0),
            color=str(color or self._default_experiment_control_color(step_number - 1)),
            valve=str(self.step_valve_button.property("valve") or "Open"),
            switch_position=self._current_switch_position_from_editor(),
            description=self.step_comment_edit.text().strip(),
            channels=[
                PumpChannelStep(
                    flow_ul_min=max(round(self.manual_flow_spins[index].value()), 0),
                    direction=self._direction_button_value(self.manual_direction_buttons[index]),
                )
                for index in range(ACTIVE_PUMP_CHANNELS)
            ],
        )

    def _direction_button_value(self, button: QToolButton) -> str:
        value = button.property("direction")
        return str(value) if value in {"CW", "CCW"} else "CW"

    def _toggle_direction_button(self, button: QToolButton) -> None:
        next_direction = "CCW" if self._direction_button_value(button) == "CW" else "CW"
        set_direction_button(self, button, next_direction)

    def _add_experiment_control_step_from_editor(self) -> None:
        row = self._selected_experiment_control_row()
        insert_at = len(self._table_model.steps()) if row is None else row + 1
        step = self._current_editor_step(insert_at + 1)
        new_row = self._table_model.insert_step(row if row is not None else -1, step)
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


def _centered_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return label


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
