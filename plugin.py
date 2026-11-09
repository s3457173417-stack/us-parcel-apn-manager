import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QDockWidget

from .dialog import ParcelDialog
from .sources import PLUGIN_NAME, PLUGIN_VERSION


class ParcelAPNManagerPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dialog = None
        self.dock = None
        self.plugin_dir = os.path.dirname(__file__)

    def initGui(self):
        icon_path = os.path.join(self.plugin_dir, "icon.svg")
        self.action = QAction(QIcon(icon_path), PLUGIN_NAME, self.iface.mainWindow())
        self.action.setObjectName("ParcelAPNDataManagerAction")
        self.action.setToolTip("Parcel/APN data manager | 地块/APN数据管理器")
        self.action.setCheckable(True)
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToVectorMenu(PLUGIN_NAME, self.action)

    def _create_dock(self):
        if self.dock is not None:
            return

        self.dialog = ParcelDialog(self.iface, parent=self.iface.mainWindow())
        self.dock = QDockWidget(
            f"{PLUGIN_NAME} v{PLUGIN_VERSION}",
            self.iface.mainWindow(),
        )
        self.dock.setObjectName("ParcelAPNDataManagerDock")
        self.dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable
            | QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self.dock.setWidget(self.dialog)
        # The panel's own Close button should hide the whole dock, not only
        # the embedded content widget.
        try:
            self.dialog.close_btn.clicked.disconnect()
        except TypeError:
            pass
        self.dialog.close_btn.clicked.connect(self.dock.hide)
        self.dock.visibilityChanged.connect(self._dock_visibility_changed)

        # First creation is intentionally docked on the RIGHT side of QGIS.
        self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
        self.dock.resize(620, 760)

    def _dock_visibility_changed(self, visible):
        if self.action:
            self.action.blockSignals(True)
            self.action.setChecked(bool(visible))
            self.action.blockSignals(False)

    def unload(self):
        if self.action:
            self.iface.removeToolBarIcon(self.action)
            self.iface.removePluginVectorMenu(PLUGIN_NAME, self.action)

        if self.dock:
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None
            self.dialog = None

    def run(self, checked=False):
        self._create_dock()

        # Clicking the toolbar/menu action behaves as a panel toggle.
        if checked is False and self.dock.isVisible():
            self.dock.hide()
            return

        self.dock.show()
        self.dock.raise_()
        self.action.setChecked(True)
