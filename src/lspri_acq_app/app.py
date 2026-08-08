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

    from lspri_acq_app.gui.main_window import MainWindow

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
