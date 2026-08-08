from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication

from lspr_ui import apply_base_app_theme

from lspri_acq_app import __version__
from lspri_acq_app.version import APP_NAME


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(__version__)
    apply_base_app_theme(app)

    # Import-time side effect: registers this app's device families (Camera,
    # and eventually IlluminationSource) into lspr_acq_shell's shared device
    # lifecycle - see device/registry.py's module docstring. No discovery UI
    # calls into this yet (that's a later GUI milestone); imported here so
    # device_family_order() reflects this app's real capabilities as soon as
    # it starts, matching sLSPR acq's own device_lifecycle shim convention.
    from lspri_acq_app.device import registry as _device_registry
    _ = (_device_registry,)

    from lspri_acq_app.gui.main_window import MainWindow

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
