"""Illumination/camera per-wavelength settings panel - the first step of the
maintainer's described workflow ("user will setup the illumination
parameters... then user setup camera parameters") and, until now, the one
piece of it with no GUI at all: ImagingAcquisitionSettings (domain/models.py)
was only ever buildable programmatically/by tests.

One table row per swept wavelength, always carrying an explicit
WavelengthCameraSettings/WavelengthIlluminationSettings override for that
row - not a "global default, per-row exception" model. This keeps the UI
honest about what the sweep will actually do for each wavelength, rather
than a table where a blank cell silently means "ask the illumination source"
and it's not obvious from looking at the row.

Gain/Current/Settle time all default to "not set" (None in the produced
domain objects - a real, meaningful distinction: None settle time means
"ask illumination.settle_time_ms()", not "wait 0 ms"). Represented via
QDoubleSpinBox.setSpecialValueText() at each field's minimum, the standard
Qt idiom for an optional-numeric spinbox, rather than a separate checkbox
per field.

Deliberately v1-scoped, matching this app's other "lean equivalent, not
everything at once" panels: exposure/gain/binning/settle/current/spectrum-
source are exposed; resolution/crop/saving_mode (also part of the v6.4
camera_settings schema and the WavelengthCameraSettings dataclass) are not
yet editable here - callers needing them can still set them on the
WavelengthCameraSettings objects directly. A future "advanced" dialog per
row is the natural place for those, not a wider table by default.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from lspri_acq_app.domain.models import (
    ImagingAcquisitionSettings,
    WavelengthCameraSettings,
    WavelengthIlluminationSettings,
)

_COLUMN_WAVELENGTH = 0
_COLUMN_EXPOSURE = 1
_COLUMN_GAIN = 2
_COLUMN_BINNING = 3
_COLUMN_SETTLE = 4
_COLUMN_CURRENT = 5
_COLUMN_SPECTRUM_SOURCE = 6
_COLUMN_COUNT = 7
_COLUMN_HEADERS = [
    "Wavelength (nm)",
    "Exposure (µs)",
    "Gain",
    "Binning",
    "Settle (ms)",
    "Current",
    "Spectrum source",
]

_UNSET = -1.0  # sentinel for Gain/Settle/Current spin boxes' minimum value


def _optional_spin(*, maximum: float, decimals: int = 2) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(_UNSET, maximum)
    spin.setDecimals(decimals)
    spin.setSpecialValueText("not set")
    spin.setValue(_UNSET)
    spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
    return spin


def _spin_value_or_none(spin: QDoubleSpinBox) -> float | None:
    return None if spin.value() == _UNSET else float(spin.value())


class IlluminationCameraSettingsPanel(QWidget):
    """Owns the per-wavelength table. current_settings() builds a real
    ImagingAcquisitionSettings from what's in the table right now;
    load_settings() repopulates the table from one (e.g. after a session
    restore - see storage/hdf5_export.py's read_imaging_session())."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._table = QTableWidget(0, _COLUMN_COUNT, self)
        self._table.setHorizontalHeaderLabels(_COLUMN_HEADERS)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setVisible(False)

        add_button = QPushButton("Add wavelength", self)
        add_button.clicked.connect(lambda: self.add_wavelength_row())
        remove_button = QPushButton("Remove selected", self)
        remove_button.clicked.connect(self._on_remove_clicked)
        button_row = QHBoxLayout()
        button_row.addWidget(add_button)
        button_row.addWidget(remove_button)
        button_row.addStretch(1)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(button_row)
        layout.addWidget(self._table, 1)
        self.setLayout(layout)

    # -- row management -----------------------------------------------------

    def add_wavelength_row(
        self,
        wavelength_nm: float = 500.0,
        *,
        exposure_us: float = 5000.0,
        gain: float | None = None,
        binning: int = 1,
        settle_time_ms: float | None = None,
        current: float | None = None,
        spectrum_source: str = "default_file",
    ) -> int:
        row = self._table.rowCount()
        self._table.insertRow(row)

        wavelength_spin = QDoubleSpinBox()
        wavelength_spin.setRange(0.0, 5000.0)
        wavelength_spin.setDecimals(1)
        wavelength_spin.setValue(wavelength_nm)
        self._table.setCellWidget(row, _COLUMN_WAVELENGTH, wavelength_spin)

        exposure_spin = QDoubleSpinBox()
        exposure_spin.setRange(1.0, 10_000_000.0)
        exposure_spin.setDecimals(1)
        exposure_spin.setValue(exposure_us)
        self._table.setCellWidget(row, _COLUMN_EXPOSURE, exposure_spin)

        gain_spin = _optional_spin(maximum=100.0)
        if gain is not None:
            gain_spin.setValue(gain)
        self._table.setCellWidget(row, _COLUMN_GAIN, gain_spin)

        binning_spin = QSpinBox()
        binning_spin.setRange(1, 8)
        binning_spin.setValue(binning)
        self._table.setCellWidget(row, _COLUMN_BINNING, binning_spin)

        settle_spin = _optional_spin(maximum=10_000.0, decimals=1)
        if settle_time_ms is not None:
            settle_spin.setValue(settle_time_ms)
        self._table.setCellWidget(row, _COLUMN_SETTLE, settle_spin)

        current_spin = _optional_spin(maximum=10_000.0, decimals=3)
        if current is not None:
            current_spin.setValue(current)
        self._table.setCellWidget(row, _COLUMN_CURRENT, current_spin)

        source_combo = QComboBox()
        source_combo.addItems(["default_file", "measured"])
        source_combo.setCurrentText(spectrum_source)
        self._table.setCellWidget(row, _COLUMN_SPECTRUM_SOURCE, source_combo)

        return row

    def remove_row(self, row: int) -> None:
        self._table.removeRow(row)

    def row_count(self) -> int:
        return self._table.rowCount()

    def clear_rows(self) -> None:
        self._table.setRowCount(0)

    def _on_remove_clicked(self) -> None:
        row = self._table.currentRow()
        if row >= 0:
            self.remove_row(row)

    # -- settings <-> table --------------------------------------------------

    def current_settings(self) -> ImagingAcquisitionSettings:
        wavelengths_nm: list[float] = []
        camera_by_wavelength: dict[float, WavelengthCameraSettings] = {}
        illumination_by_wavelength: dict[float, WavelengthIlluminationSettings] = {}

        for row in range(self._table.rowCount()):
            wavelength_nm = float(self._table.cellWidget(row, _COLUMN_WAVELENGTH).value())
            exposure_us = float(self._table.cellWidget(row, _COLUMN_EXPOSURE).value())
            gain = _spin_value_or_none(self._table.cellWidget(row, _COLUMN_GAIN))
            binning = int(self._table.cellWidget(row, _COLUMN_BINNING).value())
            settle_time_ms = _spin_value_or_none(self._table.cellWidget(row, _COLUMN_SETTLE))
            current = _spin_value_or_none(self._table.cellWidget(row, _COLUMN_CURRENT))
            spectrum_source = self._table.cellWidget(row, _COLUMN_SPECTRUM_SOURCE).currentText()

            wavelengths_nm.append(wavelength_nm)
            camera_by_wavelength[wavelength_nm] = WavelengthCameraSettings(
                exposure_us=exposure_us, gain=gain, binning=binning
            )
            illumination_by_wavelength[wavelength_nm] = WavelengthIlluminationSettings(
                settle_time_ms=settle_time_ms, current=current, spectrum_source=spectrum_source
            )

        first_exposure = camera_by_wavelength[wavelengths_nm[0]].exposure_us if wavelengths_nm else 0.0
        first_gain = camera_by_wavelength[wavelengths_nm[0]].gain if wavelengths_nm else None
        return ImagingAcquisitionSettings(
            wavelengths_nm=wavelengths_nm,
            exposure_us=first_exposure,
            gain=first_gain,
            camera_settings_by_wavelength=camera_by_wavelength,
            illumination_settings_by_wavelength=illumination_by_wavelength,
        )

    def load_settings(self, settings: ImagingAcquisitionSettings) -> None:
        self.clear_rows()
        for wavelength_nm in settings.wavelengths_nm:
            camera = settings.camera_settings_by_wavelength.get(wavelength_nm)
            illumination = settings.illumination_settings_by_wavelength.get(wavelength_nm)
            self.add_wavelength_row(
                wavelength_nm,
                exposure_us=camera.exposure_us if camera is not None else settings.exposure_us,
                gain=camera.gain if camera is not None else settings.gain,
                binning=camera.binning if camera is not None else 1,
                settle_time_ms=illumination.settle_time_ms if illumination is not None else None,
                current=illumination.current if illumination is not None else None,
                spectrum_source=illumination.spectrum_source if illumination is not None else "default_file",
            )
