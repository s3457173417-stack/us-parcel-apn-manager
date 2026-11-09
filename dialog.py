import json
import os
from datetime import datetime

from qgis.PyQt.QtCore import QSettings, Qt, QStandardPaths, QUrl
from qgis.PyQt.QtGui import QColor, QDesktopServices
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsDatumTransform,
    QgsExpression,
    QgsFeatureRequest,
    QgsFillSymbol,
    QgsMessageLog,
    QgsPalLayerSettings,
    QgsPointXY,
    QgsProject,
    QgsRasterLayer,
    QgsRectangle,
    QgsSingleSymbolRenderer,
    QgsTextBufferSettings,
    QgsTextFormat,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
)
from qgis.gui import QgsMapToolEmitPoint

from .parcel_io import (
    count_online_parcels,
    detect_apn_field,
    detect_az_county_by_lonlat,
    detect_us_source_by_lonlat,
    discover_us_parcel_service,
    download_online_geojson,
    geocode_us_address,
    inspect_arcgis_layer_metadata,
    is_probable_apn_query,
    normalize_address_input,
    normalize_layer_url,
    query_parcel_at_point,
    scan_path,
    search_online_address,
    transform_canvas_center_wgs84,
    transform_canvas_extent_to_service,
)
from .sources import (
    ADDRESS_FIELD_CANDIDATES,
    ADDRESS_RESULT_LIMIT,
    APN_FIELD_CANDIDATES,
    AUTO_AZ_KEY,
    AUTO_US_KEY,
    LANGUAGES,
    ONLINE_SOFT_LIMIT,
    ONLINE_SOURCES,
    PLUGIN_NAME,
    PLUGIN_VERSION,
    SERVICE_PAGE_SIZE,
    SETTINGS_ROOT,
)


class ParcelDialog(QWidget):
    def __init__(self, iface, parent=None):
        super().__init__(parent or iface.mainWindow())
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.settings = QSettings()
        self.candidates = []
        self._current_layer_id = None
        self.current_apn_field = ""
        self._current_local_layer_id = None
        self._current_online_layer_id = None
        self.address_features = []
        self.address_normalized = ""
        self._custom_meta = {}
        self._active_search_cfg = {}
        self.map_tool = None
        self.previous_map_tool = None
        self.lang = self.settings.value(f"{SETTINGS_ROOT}/ui_lang", "en", type=str)
        if self.lang not in ("en", "zh", "es", "fr"):
            self.lang = "en"

        self.setObjectName("ParcelAPNDataManagerPanel")
        self.setMinimumWidth(400)
        self._build_ui()
        self._restore_settings()
        self._retranslate_ui()

    @staticmethod
    def _resolve_layer(layer_id):
        if not layer_id:
            return None
        try:
            layer = QgsProject.instance().mapLayer(layer_id)
            if layer is not None and layer.isValid():
                return layer
        except RuntimeError:
            return None
        return None

    @property
    def current_layer(self):
        return self._resolve_layer(self._current_layer_id)

    @current_layer.setter
    def current_layer(self, layer):
        try:
            self._current_layer_id = layer.id() if layer is not None else None
        except RuntimeError:
            self._current_layer_id = None

    @property
    def current_local_layer(self):
        return self._resolve_layer(self._current_local_layer_id)

    @current_local_layer.setter
    def current_local_layer(self, layer):
        try:
            self._current_local_layer_id = layer.id() if layer is not None else None
        except RuntimeError:
            self._current_local_layer_id = None

    @property
    def current_online_layer(self):
        return self._resolve_layer(self._current_online_layer_id)

    @current_online_layer.setter
    def current_online_layer(self, layer):
        try:
            self._current_online_layer_id = layer.id() if layer is not None else None
        except RuntimeError:
            self._current_online_layer_id = None

    def tr(self, zh, en, es="", fr=""):
        if self.lang == "zh":
            return zh
        if self.lang == "es":
            return es or en
        if self.lang == "fr":
            return fr or en
        return en

    def _build_ui(self):
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer_layout.addWidget(self.scroll)

        self.content = QWidget()
        self.content.setMinimumWidth(390)
        self.scroll.setWidget(self.content)

        root = QVBoxLayout(self.content)
        root.setContentsMargins(8, 6, 8, 8)
        root.setSpacing(6)

        top = QHBoxLayout()
        self.intro = QLabel()
        self.intro.setWordWrap(True)
        self.btn_basemap = QPushButton()
        self.btn_basemap.clicked.connect(self.add_us_basemap)
        self.lang_combo = QComboBox()
        self.lang_combo.setFixedWidth(105)
        for label, code in LANGUAGES:
            self.lang_combo.addItem(label, code)
        idx = self.lang_combo.findData(self.lang)
        self.lang_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.lang_combo.currentIndexChanged.connect(self._on_language_changed)
        self.btn_language = self.lang_combo
        top.addWidget(self.intro, 1)
        top.addWidget(self.btn_basemap, 0, Qt.AlignmentFlag.AlignTop)
        top.addWidget(self.lang_combo, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(top)

        self.tabs = QTabWidget()
        self.local_tab = self._build_local_tab()
        self.online_tab = self._build_online_tab()
        self.tabs.addTab(self.local_tab, "")
        self.tabs.addTab(self.online_tab, "")
        root.addWidget(self.tabs, 1)

        self.search_box = QGroupBox()
        search_layout = QHBoxLayout(self.search_box)
        search_layout.setContentsMargins(6, 6, 6, 6)
        self.search_label = QLabel("APN:")
        self.apn_search = QLineEdit()
        self.btn_search_apn = QPushButton()
        self.btn_search_apn.clicked.connect(self.search_loaded_apn)
        search_layout.addWidget(self.search_label)
        search_layout.addWidget(self.apn_search, 1)
        search_layout.addWidget(self.btn_search_apn)
        root.addWidget(self.search_box)

        bottom = QHBoxLayout()
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.close_btn = QPushButton()
        self.close_btn.clicked.connect(self.close)
        bottom.addWidget(self.status, 1)
        bottom.addWidget(self.close_btn)
        root.addLayout(bottom)

    def _build_local_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self.local_source_box = QGroupBox()
        source_layout = QGridLayout(self.local_source_box)
        source_layout.setContentsMargins(6, 6, 6, 6)
        self.local_path = QLineEdit()
        self.btn_file = QPushButton()
        self.btn_folder = QPushButton()
        self.btn_scan = QPushButton()
        self.btn_file.clicked.connect(self.choose_local_file)
        self.btn_folder.clicked.connect(self.choose_local_folder)
        self.btn_scan.clicked.connect(self.scan_local)
        source_layout.addWidget(self.local_path, 0, 0, 1, 4)
        source_layout.addWidget(self.btn_file, 1, 0)
        source_layout.addWidget(self.btn_folder, 1, 1)
        source_layout.addWidget(self.btn_scan, 1, 2)
        source_layout.setColumnStretch(3, 1)
        layout.addWidget(self.local_source_box)

        self.local_table = QTableWidget(0, 7)
        self.local_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.local_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.local_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.local_table.horizontalHeader().setStretchLastSection(True)
        self.local_table.doubleClicked.connect(self.load_selected_local)
        layout.addWidget(self.local_table, 1)

        action_row = QHBoxLayout()
        self.chk_label = QCheckBox()
        self.chk_label.setChecked(True)
        self.chk_label.stateChanged.connect(self._toggle_local_labels_live)
        self.btn_load = QPushButton()
        self.btn_load.clicked.connect(self.load_selected_local)
        action_row.addWidget(self.chk_label)
        action_row.addStretch(1)
        action_row.addWidget(self.btn_load)
        layout.addLayout(action_row)
        return tab

    def _build_online_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self.online_source_box = QGroupBox()
        form = QFormLayout(self.online_source_box)
        form.setContentsMargins(6, 6, 6, 6)
        self.source_combo = QComboBox()
        self.source_combo.currentIndexChanged.connect(self._online_source_changed)
        self.service_url = QLineEdit()
        self.service_epsg = QSpinBox()
        self.service_epsg.setRange(1, 999999)
        self.service_epsg.setValue(3857)
        self.btn_detect_service = QPushButton()
        self.btn_detect_service.clicked.connect(self.detect_custom_service)
        epsg_row = QHBoxLayout()
        epsg_row.setContentsMargins(0, 0, 0, 0)
        epsg_row.addWidget(self.service_epsg, 1)
        epsg_row.addWidget(self.btn_detect_service, 0)
        self.lbl_source = QLabel()
        self.lbl_url = QLabel()
        self.lbl_epsg = QLabel()
        form.addRow(self.lbl_source, self.source_combo)
        form.addRow(self.lbl_url, self.service_url)
        form.addRow(self.lbl_epsg, epsg_row)
        layout.addWidget(self.online_source_box)

        self.online_info = QLabel()
        self.online_info.setWordWrap(True)
        layout.addWidget(self.online_info)

        # Address-to-APN lookup. Accepts ordinary addresses, underscore-based project
        # folder names, a pasted project-folder path, a direct APN, or map point click.
        self.address_box = QGroupBox()
        address_layout = QVBoxLayout(self.address_box)
        address_layout.setContentsMargins(6, 6, 6, 6)
        address_layout.setSpacing(5)
        address_input_row = QHBoxLayout()
        self.lbl_address = QLabel()
        self.address_search = QLineEdit()
        self.btn_address_folder = QPushButton()
        self.btn_address_search = QPushButton()
        self.btn_address_folder.clicked.connect(self.choose_address_folder)
        self.btn_address_search.clicked.connect(self.search_address_online)
        self.address_search.returnPressed.connect(self.search_address_online)
        address_input_row.addWidget(self.lbl_address)
        address_input_row.addWidget(self.address_search, 1)
        address_input_row.addWidget(self.btn_address_folder)
        address_input_row.addWidget(self.btn_address_search)
        address_layout.addLayout(address_input_row)

        quick_tools_row = QHBoxLayout()
        self.btn_pick_map_parcel = QPushButton()
        self.btn_pick_map_parcel.clicked.connect(self.activate_pick_parcel_on_map)
        self.chk_sync_project_output = QCheckBox()
        self.chk_sync_project_output.setChecked(True)
        quick_tools_row.addWidget(self.btn_pick_map_parcel)
        quick_tools_row.addWidget(self.chk_sync_project_output, 1)
        address_layout.addLayout(quick_tools_row)

        self.address_normalized_label = QLabel()
        self.address_normalized_label.setWordWrap(True)
        address_layout.addWidget(self.address_normalized_label)

        self.address_table = QTableWidget(0, 4)
        self.address_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.address_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.address_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.address_table.horizontalHeader().setStretchLastSection(True)
        self.address_table.doubleClicked.connect(self.download_selected_address_parcel)
        self.address_table.setMaximumHeight(165)
        address_layout.addWidget(self.address_table)

        address_action_row = QHBoxLayout()
        self.address_result_status = QLabel()
        self.address_result_status.setWordWrap(True)
        self.btn_copy_apn = QPushButton()
        self.btn_copy_apn.clicked.connect(self.copy_selected_parcel_info)
        self.btn_copy_apn.setEnabled(False)
        self.btn_download_address = QPushButton()
        self.btn_download_address.clicked.connect(self.download_selected_address_parcel)
        self.btn_download_address.setEnabled(False)
        address_action_row.addWidget(self.address_result_status, 1)
        address_action_row.addWidget(self.btn_copy_apn)
        address_action_row.addWidget(self.btn_download_address)
        address_layout.addLayout(address_action_row)
        layout.addWidget(self.address_box)

        self.extent_box = QGroupBox()
        extent_layout = QFormLayout(self.extent_box)
        extent_layout.setContentsMargins(6, 6, 6, 6)
        self.extent_info = QLabel()
        self.extent_info.setWordWrap(True)
        self.check_btn = QPushButton()
        self.check_btn.clicked.connect(self.check_online_extent)
        self.lbl_extent_status = QLabel()
        extent_layout.addRow(self.lbl_extent_status, self.extent_info)
        extent_layout.addRow("", self.check_btn)
        layout.addWidget(self.extent_box)

        self.output_box = QGroupBox()
        output_layout = QGridLayout(self.output_box)
        output_layout.setContentsMargins(6, 6, 6, 6)
        self.output_dir = QLineEdit()
        self.btn_output = QPushButton()
        self.btn_output.clicked.connect(self.choose_output_folder)
        self.lbl_output_folder = QLabel()
        output_layout.addWidget(self.lbl_output_folder, 0, 0)
        output_layout.addWidget(self.output_dir, 0, 1)
        output_layout.addWidget(self.btn_output, 0, 2)
        layout.addWidget(self.output_box)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        row = QHBoxLayout()
        self.chk_online_label = QCheckBox()
        self.chk_online_label.setChecked(True)
        self.chk_online_label.stateChanged.connect(self._toggle_online_labels_live)
        self.btn_download = QPushButton()
        self.btn_download.clicked.connect(self.download_current_extent)
        row.addWidget(self.chk_online_label)
        row.addStretch(1)
        row.addWidget(self.btn_download)
        layout.addLayout(row)
        layout.addStretch(1)
        return tab

    def _on_language_changed(self, _idx=0):
        lang = self.lang_combo.currentData() or "en"
        self.lang = lang
        self.settings.setValue(f"{SETTINGS_ROOT}/ui_lang", self.lang)
        self._retranslate_ui()

    def toggle_language(self):
        order = ["en", "zh", "es", "fr"]
        nxt = order[(order.index(self.lang) + 1) % len(order)] if self.lang in order else "en"
        self.lang = nxt
        self.settings.setValue(f"{SETTINGS_ROOT}/ui_lang", self.lang)
        idx = self.lang_combo.findData(self.lang)
        if idx >= 0:
            self.lang_combo.blockSignals(True)
            self.lang_combo.setCurrentIndex(idx)
            self.lang_combo.blockSignals(False)
        self._retranslate_ui()

    def _retranslate_ui(self):
        self.intro.setText(self.tr(
            "全美通用 Parcel/APN 地块数据管理器（覆盖美国 50 州）。支持本地 SHP/ZIP 扫描，以及全美在线按地图点击、范围、地址、项目文件夹名或 APN 检索下载。",
            "Nationwide US Parcel/APN Manager (All 50 States). Scans local SHP/ZIP or downloads online parcels by map click, extent, US address, project folder name, or APN.",
            "Gestor Nacional de Parcelas/APN de EE. UU. (50 estados). Escanea SHP/ZIP locales o descarga parcelas en línea por clic en mapa, extensión, dirección, carpeta o APN.",
            "Gestionnaire national de parcelles/APN US (50 États). Analyse les SHP/ZIP locaux ou télécharge les parcelles en ligne par clic carte, emprise, adresse, dossier ou APN.",
        ))
        self.btn_basemap.setText(self.tr("卫星底图", "Basemap", "Mapa Base", "Fond Carte"))
        self.btn_basemap.setToolTip(self.tr(
            "一键加载 Esri 全球高清卫星影像底图（置于图层树最底层）",
            "Add Esri World Imagery satellite basemap at the bottom of the layer tree",
            "Añadir mapa base satelital Esri World Imagery al fondo del árbol de capas",
            "Ajouter le fond satellite Esri World Imagery tout en bas de l'arbre des couches",
        ))
        idx = self.lang_combo.findData(self.lang)
        if idx >= 0 and self.lang_combo.currentIndex() != idx:
            self.lang_combo.blockSignals(True)
            self.lang_combo.setCurrentIndex(idx)
            self.lang_combo.blockSignals(False)

        self.tabs.setTabText(0, self.tr("本地地块", "Local Parcel", "Parcela Local", "Parcelle Locale"))
        self.tabs.setTabText(1, self.tr("在线地块", "Online Parcel", "Parcela en Línea", "Parcelle en Ligne"))

        self.search_box.setTitle(self.tr("已加载图层 APN 定位", "Search APN in Loaded Layer", "Buscar APN en Capa Cargada", "Rechercher APN dans la Couche"))
        self.apn_search.setPlaceholderText(self.tr("例如：219-47-413 或 21947413", "Example: 219-47-413 or 21947413", "Ej.: 219-47-413 o 21947413", "Ex. : 219-47-413 ou 21947413"))
        self.btn_search_apn.setText(self.tr("选择并缩放", "Select + Zoom", "Seleccionar + Zoom", "Sélectionner + Zoom"))
        if self.status.text() in ("", "就绪", "Ready", "Listo", "Prêt", "就绪 / Ready"):
            self.status.setText(self.tr("就绪", "Ready", "Listo", "Prêt"))
        self.close_btn.setText(self.tr("关闭", "Close", "Cerrar", "Fermer"))

        self.local_source_box.setTitle(self.tr("1. 扫描本地 Parcel 数据", "1. Scan Local Parcel Data", "1. Escanear Datos de Parcelas Locales", "1. Analyser les Données Locales"))
        self.local_path.setPlaceholderText(self.tr(
            "选择 SHP、Parcel ZIP 或 GIS 根目录，例如 D:\\gisdata\\_GIS",
            "Choose a SHP, Parcel ZIP, or GIS root folder, e.g. D:\\gisdata\\_GIS",
            "Elija un archivo SHP, ZIP o carpeta GIS, ej. D:\\gisdata\\_GIS",
            "Choisir un SHP, ZIP ou dossier racine SIG, ex. D:\\gisdata\\_GIS",
        ))
        self.btn_file.setText(self.tr("选择文件", "Choose File", "Archivo", "Fichier"))
        self.btn_folder.setText(self.tr("选择文件夹", "Choose Folder", "Carpeta", "Dossier"))
        self.btn_scan.setText(self.tr("扫描", "Scan", "Escanear", "Analyser"))
        self.local_table.setHorizontalHeaderLabels([
            self.tr("数据集", "Dataset", "Conjunto", "Jeu de données"),
            self.tr("类型", "Type", "Tipo", "Type"),
            self.tr("状态", "Status", "Estado", "État"),
            self.tr("APN字段", "APN Field", "Campo APN", "Champ APN"),
            self.tr("地块数", "Features", "Objetos", "Entités"),
            self.tr("坐标系", "CRS", "SRC", "SCR"),
            self.tr("来源", "Source", "Origen", "Source"),
        ])
        self.chk_label.setText(self.tr("显示 APN 数字（实时开关）", "Show APN labels (live toggle)", "Mostrar etiquetas APN (en vivo)", "Afficher étiquettes APN (en direct)"))
        self.btn_load.setText(self.tr("加载选中地块", "Load Selected Parcel", "Cargar Parcela Seleccionada", "Charger la Parcelle Sélectionnée"))

        self.online_source_box.setTitle(self.tr("1. 全美在线数据源（覆盖美国 50 州）", "1. Nationwide US Online Data Source (All 50 States)", "1. Fuente en Línea de EE. UU. (50 Estados)", "1. Source en Ligne US (50 États)"))
        self.lbl_source.setText(self.tr("数据源：", "Source:", "Fuente:", "Source :"))
        self.lbl_url.setText(self.tr("图层 URL：", "Layer URL:", "URL de capa:", "URL couche :"))
        self.lbl_epsg.setText(self.tr("服务 EPSG：", "Service EPSG:", "EPSG:", "EPSG :"))
        self.btn_detect_service.setText(self.tr("自动探测图层", "Detect Layer", "Detectar Capa", "Détecter Couche"))
        self.service_url.setPlaceholderText(self.tr("ArcGIS 图层 URL，例如 https://.../FeatureServer/0", "ArcGIS layer URL, e.g. https://.../FeatureServer/0", "URL de capa ArcGIS, ej. https://.../FeatureServer/0", "URL de couche ArcGIS, ex. https://.../FeatureServer/0"))
        self._rebuild_source_combo()
        self.online_info.setText(self.tr(
            "默认开启“全美自动识别州/县（覆盖美国 50 州）”：自动根据当前地图位置或输入地址匹配内置官方源（CA / CO / UT / WA / WI / NJ / FL / AZ 等）或动态检索对应美国县级 ArcGIS 服务；也可粘贴任意自定义 URL 点击“自动探测图层”。",
            "Defaults to 'Auto-Detect US State / County (All 50 States)': automatically matches built-in state/county parcel services (CA, CO, UT, WA, WI, NJ, FL, AZ, etc.) or dynamically discovers public US county ArcGIS parcel layers by map extent or address.",
            "Por defecto usa 'Detección automática de Estado/Condado (50 estados de EE. UU.)': conecta automáticamente con servicios estatales/condales integrados o descubre capas públicas de parcelas de ArcGIS en cualquier condado de EE. UU.",
            "Par défaut sur 'Détection auto État/Comté US (50 États)' : sélectionne automatiquement les services d'État/comté intégrés ou découvre dynamiquement les couches publiques ArcGIS de parcelles aux États-Unis.",
        ))
        self.address_box.setTitle(self.tr("2. 按地址 / 项目文件夹名 / APN / 地图点击查找地块", "2. Find Parcel by Address / Folder Name / APN / Map Click", "2. Buscar Parcela por Dirección / Carpeta / APN / Clic en Mapa", "2. Chercher Parcelle par Adresse / Dossier / APN / Clic Carte"))
        self.lbl_address.setText(self.tr("地址/APN：", "Addr/APN:", "Dir./APN:", "Adr./APN :"))
        self.address_search.setPlaceholderText(self.tr(
            "例如 2606215_42450_N_109th_Pl、42450 N 109th Pl 或 219-47-413",
            "e.g. 2606215_42450_N_109th_Pl, 42450 N 109th Pl, or 219-47-413",
            "ej. 2606215_42450_N_109th_Pl, 42450 N 109th Pl o 219-47-413",
            "ex. 2606215_42450_N_109th_Pl, 42450 N 109th Pl ou 219-47-413",
        ))
        self.btn_address_folder.setText(self.tr("读取项目文件夹名", "Use Project Folder Name", "Usar Nombre de Carpeta", "Nom du Dossier Projet"))
        self.btn_address_search.setText(self.tr("搜索地块", "Search Parcel", "Buscar Parcela", "Rechercher"))
        self.btn_pick_map_parcel.setText(self.tr("① 地图点击取地块", "① Pick Parcel on Map", "① Clic en Mapa para Parcela", "① Cliquer Parcelle sur Carte"))
        self.btn_pick_map_parcel.setToolTip(self.tr(
            "在 QGIS 地图上直接点击房屋或空地位置，实时反查并提取该点所在的单个 Parcel 地块",
            "Click any location on the QGIS map canvas to query and fetch the exact parcel polygon at that point",
            "Haga clic en cualquier punto del mapa para consultar y obtener la parcela exacta en esa ubicación",
            "Cliquez sur n'importe quel point de la carte pour interroger et récupérer la parcelle correspondante",
        ))
        self.chk_sync_project_output.setText(self.tr(
            "读取项目文件夹时自动设为输出目录",
            "Auto-set Output Folder to Project Folder",
            "Usar carpeta del proyecto como carpeta de salida",
            "Définir auto le dossier projet comme dossier de sortie",
        ))
        self.address_table.setHorizontalHeaderLabels([
            self.tr("APN", "APN", "APN", "APN"),
            self.tr("产权地址", "Property Address", "Dirección", "Adresse"),
            self.tr("城市", "City", "Ciudad", "Ville"),
            self.tr("邮编", "ZIP", "CP", "Code postal"),
        ])
        if self.address_normalized:
            self.address_normalized_label.setText(self.tr(
                f"识别查询：{self.address_normalized}",
                f"Parsed query: {self.address_normalized}",
                f"Consulta reconocida: {self.address_normalized}",
                f"Requête analysée : {self.address_normalized}",
            ))
        else:
            self.address_normalized_label.setText(self.tr(
                "支持全美地址、带下划线的项目文件夹名（自动去除前缀编号）、直接输入 APN 或点击地图直接拾取地块。",
                "Supports any US street address, underscore project folder names (auto-strips leading job ID), direct APN input, or clicking directly on the map.",
                "Admite cualquier dirección de EE. UU., nombres de carpetas con guiones bajos, APN directo o clic directo en el mapa.",
                "Prend en charge toute adresse US, les noms de dossiers avec underscores, un APN direct ou un clic direct sur la carte.",
            ))
        self.btn_copy_apn.setText(self.tr("复制 APN", "Copy APN", "Copiar APN", "Copier APN"))
        self.btn_copy_apn.setToolTip(self.tr(
            "复制当前选中地块的 APN 与产权地址到剪贴板",
            "Copy selected parcel's APN and property address to clipboard",
            "Copiar APN y dirección de la parcela seleccionada al portapapeles",
            "Copier l'APN et l'adresse de la parcelle sélectionnée dans le presse-papiers",
        ))
        self.btn_download_address.setText(self.tr("下载选中地块", "Download Selected Parcel", "Descargar Parcela Seleccionada", "Télécharger la Parcelle"))
        self.extent_box.setTitle(self.tr("3. 当前地图工作范围", "3. Current Map Work Extent", "3. Extensión Actual del Mapa", "3. Emprise Actuelle de la Carte"))
        if not self.extent_info.text() or "Click" in self.extent_info.text() or "点击" in self.extent_info.text() or "Haga" in self.extent_info.text() or "Cliquez" in self.extent_info.text():
            self.extent_info.setText(self.tr("点击“检查范围”查询地块数量。", "Click 'Check Extent' to query the parcel count.", "Haga clic en 'Verificar Extensión' para consultar el número de parcelas.", "Cliquez sur 'Vérifier l'emprise' pour compter les parcelles."))
        self.lbl_extent_status.setText(self.tr("状态：", "Status:", "Estado:", "État :"))
        self.check_btn.setText(self.tr("检查范围", "Check Extent", "Verificar Extensión", "Vérifier l'emprise"))
        self.output_box.setTitle(self.tr("4. 输出", "4. Output", "4. Salida", "4. Sortie"))
        self.lbl_output_folder.setText(self.tr("目录：", "Folder:", "Carpeta:", "Dossier :"))
        self.btn_output.setText(self.tr("选择文件夹", "Choose Folder", "Elegir Carpeta", "Choisir Dossier"))
        self.chk_online_label.setText(self.tr("显示 APN 数字（实时开关）", "Show APN labels (live toggle)", "Mostrar etiquetas APN (en vivo)", "Afficher étiquettes APN (en direct)"))
        self.btn_download.setText(self.tr("下载当前工作范围", "Download Current Work Extent", "Descargar Extensión Actual", "Télécharger l'Emprise Actuelle"))

    def _resolve_auto_us_source_from_canvas(self, allow_network=False):
        lonlat = transform_canvas_center_wgs84(self.canvas)
        if lonlat is not None:
            matched_key = detect_us_source_by_lonlat(lonlat[0], lonlat[1])
            if matched_key and matched_key in ONLINE_SOURCES:
                return dict(ONLINE_SOURCES[matched_key])
            if allow_network:
                return discover_us_parcel_service(lonlat[0], lonlat[1])
        return dict(ONLINE_SOURCES["maricopa"])

    def _rebuild_source_combo(self):
        current_key = self.source_combo.currentData()
        self.source_combo.blockSignals(True)
        self.source_combo.clear()
        self.source_combo.addItem(
            self.tr(
                "全美自动识别州/县（覆盖美国 50 州）",
                "Auto-Detect US State / County (All 50 States)",
                "Detección automática de Estado/Condado (50 estados de EE. UU.)",
                "Détection auto État/Comté US (50 États)",
            ),
            AUTO_US_KEY,
        )
        label_field = f"label_{self.lang}" if self.lang in ("en", "zh", "es", "fr") else "label_en"
        for key, cfg in ONLINE_SOURCES.items():
            label = cfg.get(label_field) or cfg.get("label_en") or key
            self.source_combo.addItem(label, key)
        self.source_combo.addItem(
            self.tr("自定义美国 ArcGIS 图层", "Custom US ArcGIS Layer", "Capa ArcGIS de EE. UU. Personalizada", "Couche ArcGIS US Personnalisée"),
            "custom",
        )
        idx = self.source_combo.findData(current_key)
        self.source_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.source_combo.blockSignals(False)
        self._online_source_changed()

    def _online_source_changed(self):
        if not hasattr(self, "source_combo"):
            return
        key = self.source_combo.currentData()
        if key == "custom":
            self.service_url.setReadOnly(False)
            self.btn_detect_service.setEnabled(True)
            self.address_result_status.setText(self.tr(
                "自定义源支持自动探测字段、APN 直接查询及美国地址地理编码空间匹配。",
                "Custom source supports auto-detected fields, direct APN search, and US address geocoding + spatial matching.",
                "La fuente personalizada admite detección automática de campos, búsqueda directa de APN y geocodificación espacial en EE. UU.",
                "La source personnalisée prend en charge la détection auto des champs, la recherche directe d'APN et le géocodage spatial US.",
            ))
            return
        self.btn_detect_service.setEnabled(False)
        if key == AUTO_US_KEY:
            cfg = self._resolve_auto_us_source_from_canvas(allow_network=False)
        else:
            cfg = ONLINE_SOURCES.get(key, ONLINE_SOURCES["maricopa"])
        self.service_url.setText(cfg.get("layer_url", ""))
        self.service_epsg.setValue(int(cfg.get("wkid", 3857)))
        self.service_url.setReadOnly(True)

    def detect_custom_service(self, interactive=True):
        url = normalize_layer_url(self.service_url.text())
        if not url:
            if interactive:
                QMessageBox.information(
                    self,
                    PLUGIN_NAME,
                    self.tr("请先输入自定义 ArcGIS 图层 URL。", "Please enter a custom ArcGIS layer URL first."),
                )
            return {}
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            meta = inspect_arcgis_layer_metadata(url)
        except Exception as exc:
            if interactive:
                QMessageBox.warning(
                    self,
                    PLUGIN_NAME,
                    self.tr(f"探测图层元数据失败：\n{exc}", f"Failed to inspect layer metadata:\n{exc}"),
                )
            return {}
        finally:
            QApplication.restoreOverrideCursor()

        wkid = int(meta.get("wkid") or 4326)
        self.service_url.setText(url)
        self.service_epsg.setValue(wkid)
        self._custom_meta = {
            "url": url,
            "wkid": wkid,
            "apn_field": meta.get("apn_field") or "",
            "address_field": meta.get("address_field") or "",
            "name": meta.get("name") or "Custom",
        }
        msg = self.tr(
            f"已探测图层：{self._custom_meta['name']} | EPSG:{wkid} | APN字段：{self._custom_meta['apn_field'] or '未识别'} | 地址字段：{self._custom_meta['address_field'] or '地理编码回退'}",
            f"Detected layer: {self._custom_meta['name']} | EPSG:{wkid} | APN: {self._custom_meta['apn_field'] or 'none'} | Address: {self._custom_meta['address_field'] or 'geocode fallback'}",
        )
        self.status.setText(msg)
        if interactive:
            QMessageBox.information(self, PLUGIN_NAME, msg)
        return self._custom_meta

    def _restore_settings(self):
        local = self.settings.value(f"{SETTINGS_ROOT}/localPath", "", type=str)
        out = self.settings.value(f"{SETTINGS_ROOT}/outputDir", "", type=str)
        if not out:
            docs = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
            out = os.path.join(docs, "QGIS_Parcel_APN")
        self.local_path.setText(local)
        self.output_dir.setText(out)

    def _save_settings(self):
        self.settings.setValue(f"{SETTINGS_ROOT}/localPath", self.local_path.text().strip())
        self.settings.setValue(f"{SETTINGS_ROOT}/outputDir", self.output_dir.text().strip())
        self.settings.setValue(f"{SETTINGS_ROOT}/ui_lang", self.lang)

    def closeEvent(self, event):
        if self.map_tool and self.canvas.mapTool() == self.map_tool and self.previous_map_tool:
            self.canvas.setMapTool(self.previous_map_tool)
        self._save_settings()
        super().closeEvent(event)

    def add_us_basemap(self):
        name = "Esri World Imagery (Satellite)"
        for lyr in QgsProject.instance().mapLayers().values():
            if lyr.name() == name:
                self.status.setText(self.tr("卫星底图已在工程中。", "Satellite basemap is already in the project."))
                return
        uri = (
            "type=xyz&url=https://server.arcgisonline.com/ArcGIS/rest/services/"
            "World_Imagery/MapServer/tile/%7Bz%7D/%7By%7D/%7Bx%7D&zmax=19&zmin=0"
        )
        layer = QgsRasterLayer(uri, name, "wms")
        if layer.isValid():
            QgsProject.instance().addMapLayer(layer, False)
            QgsProject.instance().layerTreeRoot().insertLayer(-1, layer)
            self.canvas.refresh()
            self.status.setText(self.tr(
                "已加载 Esri 全球高清卫星影像底图（置于底层）。",
                "Added Esri World Imagery satellite basemap at the bottom of the layer tree.",
                "Mapa base satelital Esri World Imagery añadido al fondo.",
                "Fond satellite Esri World Imagery ajouté tout en bas.",
            ))
        else:
            QMessageBox.warning(
                self,
                PLUGIN_NAME,
                self.tr("无法加载在线卫星底图。", "Could not load the online satellite basemap."),
            )

    def activate_pick_parcel_on_map(self):
        self.tabs.setCurrentIndex(1)
        if self.map_tool is None:
            self.map_tool = QgsMapToolEmitPoint(self.canvas)
            self.map_tool.canvasClicked.connect(self._point_parcel_clicked)
        self.previous_map_tool = self.canvas.mapTool()
        self.canvas.setMapTool(self.map_tool)
        self.status.setText(self.tr(
            "请在 QGIS 地图上直接点击目标地块位置…",
            "Click directly on the target parcel location on the QGIS map…",
            "Haga clic directamente sobre la parcela objetivo en el mapa…",
            "Cliquez directement sur la parcelle cible sur la carte QGIS…",
        ))

    def _point_parcel_clicked(self, point, _button):
        if self.previous_map_tool:
            self.canvas.setMapTool(self.previous_map_tool)
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        transform = QgsCoordinateTransform(
            self.canvas.mapSettings().destinationCrs(), wgs84, QgsProject.instance()
        )
        pt = transform.transform(QgsPointXY(point))
        lon, lat = float(pt.x()), float(pt.y())

        self.address_table.setRowCount(0)
        self.address_features = []
        self.btn_download_address.setEnabled(False)
        self.btn_copy_apn.setEnabled(False)
        self.address_result_status.setText(self.tr("正在查询点击位置地块…", "Querying parcel at clicked point…"))

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            key = self.source_combo.currentData()
            if key == AUTO_US_KEY:
                matched_key = detect_us_source_by_lonlat(lon, lat)
                if matched_key and matched_key in ONLINE_SOURCES:
                    cfg = dict(ONLINE_SOURCES[matched_key])
                else:
                    cfg = discover_us_parcel_service(lon, lat)
                self.service_url.setText(cfg.get("layer_url", ""))
                self.service_epsg.setValue(int(cfg.get("wkid", 3857)))
            else:
                cfg = self._address_source_config()

            self._active_search_cfg = cfg
            layer_url = normalize_layer_url(cfg.get("layer_url") or cfg.get("address_layer_url") or "")
            if not layer_url:
                raise RuntimeError(self.tr("未找到可用的在线地块图层 URL。", "No online parcel layer URL available."))
            data = query_parcel_at_point(lon, lat, layer_url, result_limit=5)
        except Exception as exc:
            self.address_result_status.setText(self.tr(f"地图点击查询失败：{exc}", f"Map click query failed: {exc}"))
            QMessageBox.warning(self, PLUGIN_NAME, self.tr(f"地图点击查询地块失败：\n{exc}", f"Map click parcel query failed:\n{exc}"))
            return
        finally:
            QApplication.restoreOverrideCursor()

        coord_label = f"Point ({lon:.5f}, {lat:.5f})"
        self.address_normalized = coord_label
        self.address_Features_from_point = True
        self.address_features = data.get("features", []) or []
        self._populate_address_results(cfg, coord_label)

    def copy_selected_parcel_info(self):
        row = self.address_table.currentRow()
        if row < 0:
            return
        vals = []
        for col in range(self.address_table.columnCount()):
            it = self.address_table.item(row, col)
            if it and it.text().strip():
                vals.append(it.text().strip())
        if vals:
            text = " | ".join(vals)
            QApplication.clipboard().setText(text)
            self.status.setText(self.tr(f"已复制到剪贴板：{text}", f"Copied to clipboard: {text}"))

    def choose_local_file(self):
        fn, _ = QFileDialog.getOpenFileName(
            self, self.tr("选择 Parcel 数据", "Choose Parcel Dataset"), self.local_path.text() or "",
            "Parcel data (*.shp *.zip);;Shapefile (*.shp);;ZIP (*.zip);;All files (*.*)"
        )
        if fn:
            self.local_path.setText(fn)
            self.scan_local()

    def choose_local_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, self.tr("选择 Parcel 根目录", "Choose Parcel Root Folder"), self.local_path.text() or ""
        )
        if folder:
            self.local_path.setText(folder)
            self.scan_local()

    def choose_output_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, self.tr("选择输出目录", "Choose Output Folder"), self.output_dir.text() or ""
        )
        if folder:
            self.output_dir.setText(folder)

    def scan_local(self):
        path = self.local_path.text().strip()
        if not path:
            QMessageBox.warning(self, PLUGIN_NAME, self.tr("请先选择 SHP、ZIP 或文件夹。", "Choose a SHP, ZIP, or folder first."))
            return
        if not os.path.exists(path):
            QMessageBox.warning(self, PLUGIN_NAME, self.tr("所选路径不存在。", "The selected path does not exist."))
            return

        self.status.setText(self.tr("正在扫描本地 Parcel 数据…", "Scanning local Parcel data…"))
        self.local_table.setRowCount(0)
        try:
            self.candidates = scan_path(path)
        except Exception as exc:
            QMessageBox.critical(self, PLUGIN_NAME, self.tr(f"本地扫描失败：\n{exc}", f"Local scan failed:\n{exc}"))
            self.status.setText(self.tr("扫描失败", "Scan failed"))
            return

        for c in self.candidates:
            row = self.local_table.rowCount()
            self.local_table.insertRow(row)
            status = c.status
            if self.lang == "zh":
                status = status.replace("Ready / 可用", "可用").replace("Invalid / 无效", "无效")
            values = [c.name, c.source_type, status, c.apn_field or "—",
                      f"{c.feature_count:,}" if c.feature_count >= 0 else "?", c.crs_authid or "?", c.source]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, row)
                self.local_table.setItem(row, col, item)

        self.local_table.resizeColumnsToContents()
        self.local_table.horizontalHeader().setStretchLastSection(True)
        self.status.setText(self.tr(
            f"发现 {len(self.candidates)} 个候选地块图层。",
            f"Found {len(self.candidates)} candidate layer(s)."
        ))
        if self.candidates:
            self.local_table.selectRow(0)
        self._save_settings()

    def _selected_candidate(self):
        row = self.local_table.currentRow()
        if row < 0 or row >= len(self.candidates):
            return None
        return self.candidates[row]

    def load_selected_local(self, *args):
        c = self._selected_candidate()
        if c is None:
            QMessageBox.information(self, PLUGIN_NAME, self.tr("请先选择一个数据集。", "Select a dataset first."))
            return
        if not c.valid:
            QMessageBox.warning(self, PLUGIN_NAME, self.tr("该数据源无效。", "The selected vector source is invalid."))
            return

        # Preserve the user's current work view. Loading a county-scale file must not zoom away.
        work_extent = QgsRectangle(self.canvas.extent())
        layer = self._find_project_layer_by_source(c.source)
        if layer is None:
            layer = QgsVectorLayer(c.source, c.name, "ogr")
            if not layer.isValid():
                QMessageBox.critical(self, PLUGIN_NAME, self.tr("QGIS 无法加载该地块图层。", "QGIS could not load this Parcel layer."))
                return
            QgsProject.instance().addMapLayer(layer)

        apn_field = c.apn_field or detect_apn_field(layer)
        self._apply_parcel_style(layer, apn_field, self.chk_label.isChecked())
        self.current_layer = layer
        self.current_apn_field = apn_field
        self.current_local_layer = layer
        self.iface.setActiveLayer(layer)
        self.canvas.setExtent(work_extent)
        self.canvas.refresh()
        self.status.setText(self.tr(
            f"已加载：{layer.name()} | APN：{apn_field or '未识别'} | {layer.featureCount():,} 个地块",
            f"Loaded: {layer.name()} | APN: {apn_field or 'not detected'} | {layer.featureCount():,} parcels"
        ))
        self._check_layer_crs_transform(layer, interactive=True)

    def _crs_name(self, crs):
        if crs is None or not crs.isValid():
            return self.tr("未知坐标系", "Unknown CRS")
        auth = crs.authid() or ""
        desc = crs.description() or ""
        if auth and desc:
            return f"{auth} — {desc}"
        return auth or desc or self.tr("未知坐标系", "Unknown CRS")

    def _format_accuracy(self, accuracy):
        try:
            value = float(accuracy)
        except Exception:
            return self.tr("未知", "unknown")
        if value < 0:
            return self.tr("未知", "unknown")
        return f"{value:g} m"

    def _check_layer_crs_transform(self, layer, interactive=True):
        """Checks preferred/available PROJ operations without blocking ordinary Parcel use.

        Returns a dictionary with status: same, precise, fallback, unavailable, or unknown.
        QGIS/PROJ may list a preferred operation as unavailable when a transformation grid
        is missing; in that case we show a friendly explanation and allow the layer to remain loaded.
        """
        result = {"status": "unknown", "missing_grids": [], "message": ""}
        if layer is None or not layer.isValid():
            return result

        src = layer.crs()
        dst = QgsProject.instance().crs()
        if not src.isValid() or not dst.isValid():
            result["message"] = self.tr(
                "图层或工程坐标系无效，无法检查坐标转换。",
                "The layer or project CRS is invalid, so the coordinate transform cannot be checked."
            )
            return result

        if src == dst:
            result["status"] = "same"
            result["message"] = self.tr(
                f"坐标检查：{src.authid()} 与当前工程一致，无需坐标转换。",
                f"CRS check: {src.authid()} matches the project CRS; no transformation is required."
            )
            self.status.setText(result["message"])
            return result

        try:
            operations = list(QgsDatumTransform.operations(src, dst))
        except Exception:
            operations = []

        # Older/limited PROJ builds may not expose operation details. A real transform can
        # still be valid, so treat this as unknown rather than as a hard failure.
        if not operations:
            result["message"] = self.tr(
                f"坐标检查：{src.authid()} → {dst.authid()}。当前 QGIS/PROJ 未返回详细转换列表，QGIS 将使用工程转换设置。",
                f"CRS check: {src.authid()} → {dst.authid()}. This QGIS/PROJ build did not return detailed operations; QGIS will use the project transform settings."
            )
            self.status.setText(result["message"])
            return result

        preferred = operations[0]
        available = next((op for op in operations if getattr(op, "isAvailable", False)), None)
        missing = []
        for grid in getattr(preferred, "grids", []) or []:
            if not getattr(grid, "isAvailable", False):
                missing.append({
                    "short_name": getattr(grid, "shortName", "") or getattr(grid, "fullName", "") or "grid",
                    "url": getattr(grid, "url", "") or "",
                    "open_license": bool(getattr(grid, "openLicense", False)),
                })
        result["missing_grids"] = missing

        if getattr(preferred, "isAvailable", False):
            result["status"] = "precise"
            result["message"] = self.tr(
                f"坐标检查正常：{src.authid()} → {dst.authid()}，首选转换可用（精度 {self._format_accuracy(getattr(preferred, 'accuracy', -1))}）。",
                f"CRS check OK: {src.authid()} → {dst.authid()}; the preferred transformation is available (accuracy {self._format_accuracy(getattr(preferred, 'accuracy', -1))})."
            )
            self.status.setText(result["message"])
            return result

        if available is not None:
            result["status"] = "fallback"
            preferred_name = getattr(preferred, "name", "") or self.tr("首选转换", "preferred transformation")
            available_name = getattr(available, "name", "") or self.tr("备用转换", "fallback transformation")
            grid_names = ", ".join(g["short_name"] for g in missing) or self.tr("未识别网格文件", "unidentified grid file")
            urls = [g["url"] for g in missing if g["url"]]
            result["message"] = self.tr(
                f"坐标转换可用，但首选高精度转换缺少网格：{grid_names}。QGIS 将使用备用转换，预计精度 {self._format_accuracy(getattr(available, 'accuracy', -1))}。",
                f"Coordinate transformation is usable, but the preferred high-accuracy operation is missing grid(s): {grid_names}. QGIS will use a fallback operation with estimated accuracy {self._format_accuracy(getattr(available, 'accuracy', -1))}."
            )
            self.status.setText(result["message"])

            if interactive:
                box = QMessageBox(self)
                box.setIcon(QMessageBox.Icon.Warning)
                box.setWindowTitle(self.tr("坐标转换提示", "Coordinate Transform Notice"))
                box.setText(self.tr(
                    "数据可以继续使用，但当前不是最高精度坐标转换。",
                    "The data can still be used, but the highest-accuracy coordinate transformation is not currently available."
                ))
                detail_lines = [
                    self.tr(f"源坐标系：{self._crs_name(src)}", f"Source CRS: {self._crs_name(src)}"),
                    self.tr(f"工程坐标系：{self._crs_name(dst)}", f"Project CRS: {self._crs_name(dst)}"),
                    self.tr(f"首选转换：{preferred_name}", f"Preferred operation: {preferred_name}"),
                    self.tr(f"备用转换：{available_name}", f"Fallback operation: {available_name}"),
                    self.tr(
                        f"备用转换精度：{self._format_accuracy(getattr(available, 'accuracy', -1))}",
                        f"Fallback accuracy: {self._format_accuracy(getattr(available, 'accuracy', -1))}"
                    ),
                    self.tr(f"缺失网格：{grid_names}", f"Missing grid(s): {grid_names}"),
                ]
                if urls:
                    detail_lines.append(self.tr("下载地址：", "Download URL(s):"))
                    detail_lines.extend(urls)
                box.setDetailedText("\n".join(detail_lines))
                box.setInformativeText(self.tr(
                    "普通 Parcel/APN 查看和一般 GIS 分析通常可继续。若用于测量、产权边界或工程级成果，建议安装缺失网格后再使用。",
                    "Ordinary Parcel/APN viewing and general GIS analysis can usually continue. For surveying, legal boundary, or engineering-grade work, install the missing grid before use."
                ))
                box.addButton(self.tr("继续使用", "Continue"), QMessageBox.ButtonRole.AcceptRole)
                if urls:
                    btn_grid = box.addButton(
                        self.tr("下载缺失网格", "Download Missing Grid"),
                        QMessageBox.ButtonRole.ActionRole,
                    )
                else:
                    btn_grid = None
                box.exec()
                if btn_grid is not None and box.clickedButton() == btn_grid:
                    QDesktopServices.openUrl(QUrl(urls[0]))
            return result

        result["status"] = "unavailable"
        grid_names = ", ".join(g["short_name"] for g in missing) or self.tr("未知", "unknown")
        result["message"] = self.tr(
            f"没有找到可用的 {src.authid()} → {dst.authid()} 坐标转换。缺失网格：{grid_names}。",
            f"No usable coordinate operation was found for {src.authid()} → {dst.authid()}. Missing grid(s): {grid_names}."
        )
        self.status.setText(result["message"])
        if interactive:
            QMessageBox.warning(self, PLUGIN_NAME, result["message"])
        return result

    def _find_project_layer_by_source(self, source):
        norm = source.replace("\\", "/")
        for layer in QgsProject.instance().mapLayers().values():
            try:
                if layer.source().replace("\\", "/") == norm:
                    return layer
            except Exception as exc:
                QgsMessageLog.logMessage(str(exc), PLUGIN_NAME, Qgis.MessageLevel.Info)
        return None

    def _apply_parcel_style(self, layer, apn_field, enable_labels=True):
        """Orange parcel fill + dark outline + optional parcel-centered APN labels."""
        try:
            symbol = QgsFillSymbol.createSimple({
                "color": "245,177,38,135",
                "outline_color": "86,74,31,235",
                "outline_width": "0.45",
                "outline_style": "solid",
            })
            layer.setRenderer(QgsSingleSymbolRenderer(symbol))
        except Exception as exc:
            QgsMessageLog.logMessage(str(exc), PLUGIN_NAME, Qgis.MessageLevel.Info)

        self._set_apn_labels(layer, apn_field, enable_labels)

    def _set_apn_labels(self, layer, apn_field, enabled):
        """Enable/disable APN labels without changing the parcel renderer."""
        if layer is None or not layer.isValid():
            return False
        if enabled and apn_field:
            try:
                pal = QgsPalLayerSettings()
                pal.enabled = True
                pal.fieldName = apn_field
                pal.placement = getattr(
                    getattr(Qgis, "LabelPlacement", None),
                    "Horizontal",
                    QgsPalLayerSettings.Placement.Horizontal,
                )
                pal.scaleVisibility = False

                text_format = QgsTextFormat()
                text_format.setSize(8.5)
                text_format.setColor(QColor(35, 31, 20))
                buffer = QgsTextBufferSettings()
                buffer.setEnabled(True)
                buffer.setSize(0.8)
                buffer.setColor(QColor(255, 248, 222, 220))
                text_format.setBuffer(buffer)
                pal.setFormat(text_format)

                layer.setLabeling(QgsVectorLayerSimpleLabeling(pal))
                layer.setLabelsEnabled(True)
            except Exception:
                return False
        else:
            try:
                layer.setLabelsEnabled(False)
            except Exception:
                return False
        layer.triggerRepaint()
        self.canvas.refresh()
        return True

    def _toggle_local_labels_live(self, state):
        layer = self.current_local_layer
        if layer is None or not layer.isValid():
            return
        field = detect_apn_field(layer)
        enabled = bool(state)
        if enabled and not field:
            self.status.setText(self.tr("当前本地图层未识别到 APN 字段。", "No APN field was detected in the current local layer."))
            return
        if self._set_apn_labels(layer, field, enabled):
            self.status.setText(self.tr(
                "本地图层 APN 数字已显示。" if enabled else "本地图层 APN 数字已隐藏。",
                "Local APN labels are now visible." if enabled else "Local APN labels are now hidden."
            ))

    def _toggle_online_labels_live(self, state):
        layer = self.current_online_layer
        if layer is None or not layer.isValid():
            return
        field = detect_apn_field(layer)
        enabled = bool(state)
        if enabled and not field:
            self.status.setText(self.tr("当前在线地块图层未识别到 APN 字段。", "No APN field was detected in the current online parcel layer."))
            return
        if self._set_apn_labels(layer, field, enabled):
            self.status.setText(self.tr(
                "APN 数字已实时显示。" if enabled else "APN 数字已实时隐藏。",
                "APN labels are now visible." if enabled else "APN labels are now hidden."
            ))

    def choose_address_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            self.tr("选择项目文件夹", "Choose Project Folder"),
            self.local_path.text().strip() or "",
        )
        if folder:
            # Keep the original folder name in the input so the user can see what
            # was parsed; normalization happens automatically during search.
            name = folder.rstrip("\\/").replace("\\", "/").split("/")[-1]
            self.address_search.setText(name)
            normalized = normalize_address_input(name)
            self.address_normalized = normalized
            if hasattr(self, "chk_sync_project_output") and self.chk_sync_project_output.isChecked():
                self.output_dir.setText(folder)
                self._save_settings()
            self.address_normalized_label.setText(self.tr(
                f"识别查询：{normalized or '未识别'}",
                f"Parsed query: {normalized or 'not recognized'}"
            ))

    @staticmethod
    def _extract_prop_value(props, candidates):
        if not props:
            return ""
        lower_map = {str(k).lower(): v for k, v in props.items() if v is not None and str(v).strip() != ""}
        for cand in candidates:
            if not cand:
                continue
            val = lower_map.get(str(cand).lower())
            if val is not None and str(val).strip() != "":
                return str(val).strip()
        return ""

    def _address_source_config(self, raw_query=""):
        key = self.source_combo.currentData()
        if key == "custom":
            url = normalize_layer_url(self.service_url.text())
            if url and self._custom_meta.get("url") != url:
                self.detect_custom_service(interactive=False)
            apn_field = self._custom_meta.get("apn_field") or "APN_DASH"
            addr_field = self._custom_meta.get("address_field") or ""
            return {
                "layer_url": url,
                "address_layer_url": url,
                "address_field": addr_field,
                "address_city_field": "PHYSICAL_CITY",
                "address_zip_field": "PHYSICAL_ZIP",
                "address_apn_field": apn_field,
                "apn_search_fields": [apn_field] if apn_field else [],
                "short_name": self._custom_meta.get("name") or "Custom",
            }
        if key == AUTO_US_KEY:
            cfg = None
            if raw_query and not is_probable_apn_query(raw_query):
                cands = geocode_us_address(
                    normalize_address_input(raw_query),
                    max_locations=1,
                    bias_lonlat=transform_canvas_center_wgs84(self.canvas),
                )
                if cands:
                    cfg = discover_us_parcel_service(
                        cands[0]["lon"],
                        cands[0]["lat"],
                        county=cands[0].get("county", ""),
                        state_abbr=cands[0].get("state", ""),
                        state_name=cands[0].get("state_name", ""),
                    )
            if not cfg:
                cfg = self._resolve_auto_us_source_from_canvas(allow_network=True)
            self.service_url.setText(cfg.get("layer_url", ""))
            self.service_epsg.setValue(int(cfg.get("wkid", 3857)))
            return cfg
        return dict(ONLINE_SOURCES.get(key, {}))

    def _populate_address_results(self, cfg, normalized):
        self.address_normalized_label.setText(self.tr(
            f"识别查询：{normalized or '未识别'}（数据源：{cfg.get('short_name', 'Online')}）",
            f"Parsed query: {normalized or 'not recognized'} (Source: {cfg.get('short_name', 'Online')})"
        ))
        apn_candidates = [cfg.get("address_apn_field", "")] + list(APN_FIELD_CANDIDATES)
        addr_candidates = [cfg.get("address_field", "")] + list(ADDRESS_FIELD_CANDIDATES)
        city_candidates = [
            cfg.get("address_city_field", ""),
            "PHYSICAL_CITY", "site_city", "SITE_CITY", "SITUS_CITY",
            "city", "City", "JURIS_OL", "Jurisdiction", "OWNER_CITY",
        ]
        zip_candidates = [
            cfg.get("address_zip_field", ""),
            "PHYSICAL_ZIP", "site_zip", "SITE_ZIP", "SITUS_ZIP",
            "zip_code", "ZIP", "Postal", "OWNER_ZIP",
        ]

        for idx, feature in enumerate(self.address_features):
            props = feature.get("properties") or {}
            apn = self._extract_prop_value(props, apn_candidates)
            address = self._extract_prop_value(props, addr_candidates)
            city = self._extract_prop_value(props, city_candidates)
            zipcode = self._extract_prop_value(props, zip_candidates)
            row = self.address_table.rowCount()
            self.address_table.insertRow(row)
            for col, value in enumerate((apn, address, city, zipcode)):
                item = QTableWidgetItem(str(value or ""))
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, idx)
                self.address_table.setItem(row, col, item)

        self.address_table.resizeColumnsToContents()
        self.address_table.horizontalHeader().setStretchLastSection(True)
        src_name = cfg.get("short_name", "Online")
        if self.address_features:
            self.address_table.selectRow(0)
            self.btn_download_address.setEnabled(True)
            self.btn_copy_apn.setEnabled(True)
            self.address_result_status.setText(self.tr(
                f"[{src_name}] 找到 {len(self.address_features)} 个匹配结果。请选择正确地块后下载。",
                f"[{src_name}] Found {len(self.address_features)} match(es). Select the correct parcel to download."
            ))
        else:
            self.address_result_status.setText(self.tr(
                f"[{src_name}] 未找到：{normalized}",
                f"[{src_name}] No match found: {normalized}"
            ))

    def search_address_online(self):
        raw = self.address_search.text().strip()
        if not raw:
            QMessageBox.information(self, PLUGIN_NAME, self.tr(
                "请输入地址、项目文件夹名或 APN。",
                "Enter an address, project folder name, or APN."
            ))
            return

        self.address_table.setRowCount(0)
        self.address_features = []
        self.btn_download_address.setEnabled(False)
        self.btn_copy_apn.setEnabled(False)
        self.address_result_status.setText(self.tr("正在搜索…", "Searching…"))

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            cfg = self._address_source_config(raw_query=raw)
            self._active_search_cfg = cfg
            layer_url = normalize_layer_url(cfg.get("address_layer_url") or cfg.get("layer_url") or "")
            fallback_url = normalize_layer_url(cfg.get("layer_url") or layer_url)
            address_field = cfg.get("address_field", "")
            if not layer_url:
                QMessageBox.warning(self, PLUGIN_NAME, self.tr(
                    "当前数据源没有可用于搜索的在线图层。",
                    "The current source has no online layer available for search."
                ))
                return

            normalized, data = search_online_address(
                raw,
                layer_url,
                address_field=address_field,
                result_limit=ADDRESS_RESULT_LIMIT,
                fallback_point_layer_url=fallback_url,
                apn_fields=cfg.get("apn_search_fields"),
            )
        except Exception as exc:
            self.address_result_status.setText(self.tr(f"搜索失败：{exc}", f"Search failed: {exc}"))
            QMessageBox.warning(self, PLUGIN_NAME, self.tr(
                f"地块搜索失败：\n{exc}",
                f"Parcel search failed:\n{exc}"
            ))
            return
        finally:
            QApplication.restoreOverrideCursor()

        self.address_normalized = normalized
        self.address_features = data.get("features", []) or []
        self._populate_address_results(cfg, normalized)

    def _selected_address_feature(self):
        row = self.address_table.currentRow()
        if row < 0:
            return None
        item = self.address_table.item(row, 0)
        idx = item.data(Qt.ItemDataRole.UserRole) if item is not None else row
        try:
            idx = int(idx)
        except Exception:
            idx = row
        if 0 <= idx < len(self.address_features):
            return self.address_features[idx]
        return None

    def download_selected_address_parcel(self, *args):
        feature = self._selected_address_feature()
        if feature is None:
            QMessageBox.information(self, PLUGIN_NAME, self.tr(
                "请先搜索地址或 APN 并选择一个结果。",
                "Search an address or APN and select a result first."
            ))
            return

        out_dir = self.output_dir.text().strip()
        if not out_dir:
            QMessageBox.warning(self, PLUGIN_NAME, self.tr(
                "请先选择输出目录。",
                "Choose an output folder first."
            ))
            return
        os.makedirs(out_dir, exist_ok=True)
        self._save_settings()

        cfg = self._active_search_cfg or self._address_source_config()
        props = feature.get("properties") or {}
        apn = (
            self._extract_prop_value(props, [cfg.get("address_apn_field", "")] + list(APN_FIELD_CANDIDATES))
            or "Parcel"
        )
        address = (
            self._extract_prop_value(props, [cfg.get("address_field", "")] + list(ADDRESS_FIELD_CANDIDATES))
            or self.address_normalized
            or "Address"
        )

        def safe_name(value, max_len=70):
            import re
            text = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "").strip())
            text = re.sub(r"_+", "_", text).strip("_")
            return (text[:max_len] or "Parcel")

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = f"{safe_name(cfg.get('short_name', 'Parcel'))}_{safe_name(apn, 24)}_{safe_name(address, 55)}_{stamp}"
        geojson_path = os.path.join(out_dir, base + ".geojson")
        gpkg_path = os.path.join(out_dir, base + ".gpkg")
        collection = {"type": "FeatureCollection", "features": [feature]}

        try:
            with open(geojson_path, "w", encoding="utf-8") as f:
                json.dump(collection, f, ensure_ascii=False)
        except Exception as exc:
            QMessageBox.critical(self, PLUGIN_NAME, self.tr(
                f"保存地址地块失败：\n{exc}",
                f"Could not save the address parcel:\n{exc}"
            ))
            return

        layer_name = f"APN_{safe_name(apn, 30)}"
        temp_layer = QgsVectorLayer(geojson_path, layer_name, "ogr")
        if not temp_layer.isValid():
            QMessageBox.warning(self, PLUGIN_NAME, self.tr(
                "地块已找到，但 QGIS 无法读取下载的 GeoJSON。",
                "The parcel was found, but QGIS could not read the downloaded GeoJSON."
            ))
            return

        if self._save_as_gpkg(temp_layer, gpkg_path, layer_name):
            del temp_layer
            try:
                os.remove(geojson_path)
            except OSError:
                pass
            final_layer = QgsVectorLayer(gpkg_path, layer_name, "ogr")
            saved_path = gpkg_path
        else:
            final_layer = temp_layer
            saved_path = geojson_path

        if not final_layer.isValid():
            QMessageBox.critical(self, PLUGIN_NAME, self.tr(
                "文件已保存，但 QGIS 无法加载该地块。",
                "The file was saved, but QGIS could not load the parcel."
            ))
            return

        QgsProject.instance().addMapLayer(final_layer)
        apn_field = detect_apn_field(final_layer)
        self._apply_parcel_style(final_layer, apn_field, self.chk_online_label.isChecked())
        self.current_layer = final_layer
        self.current_apn_field = apn_field
        self.current_online_layer = final_layer
        self.iface.setActiveLayer(final_layer)

        # An address/APN search is an explicit navigation request, unlike an extent
        # download. Zooming to the matched parcel is therefore intentional.
        ids = [f.id() for f in final_layer.getFeatures()]
        if ids:
            final_layer.selectByIds(ids)
            try:
                self.canvas.zoomToSelected(final_layer)
                # Add a little context around a single parcel.
                self.canvas.zoomOut()
            except Exception as exc:
                QgsMessageLog.logMessage(str(exc), PLUGIN_NAME, Qgis.MessageLevel.Info)
        self.canvas.refresh()
        self._check_layer_crs_transform(final_layer, interactive=True)

        self.status.setText(self.tr(
            f"地块已下载：APN {apn} | {address}",
            f"Parcel downloaded: APN {apn} | {address}"
        ))
        QMessageBox.information(self, PLUGIN_NAME, self.tr(
            f"下载完成\n\n数据源：{cfg.get('short_name', 'Online')}\nAPN：{apn}\n地址：{address}\n保存位置：{saved_path}",
            f"Finished\n\nSource: {cfg.get('short_name', 'Online')}\nAPN: {apn}\nAddress: {address}\nSaved: {saved_path}"
        ))

    def _current_online_source(self):
        key = self.source_combo.currentData()
        if key == AUTO_US_KEY:
            cfg = self._resolve_auto_us_source_from_canvas(allow_network=True)
            layer_url = normalize_layer_url(cfg.get("layer_url", ""))
            wkid = int(cfg.get("wkid", 3857))
            self.service_url.setText(layer_url)
            self.service_epsg.setValue(wkid)
            short_name = cfg.get("short_name", "Parcels") + "_Parcels"
            return layer_url, wkid, short_name

        layer_url = normalize_layer_url(self.service_url.text())
        if not layer_url:
            raise RuntimeError(self.tr("请输入 ArcGIS 图层 URL。", "Enter an ArcGIS layer URL."))
        if key == "custom":
            if self._custom_meta.get("url") != layer_url:
                self.detect_custom_service(interactive=False)
            wkid = int(self.service_epsg.value())
            short_name = (self._custom_meta.get("name") or "Custom") + "_Parcels"
        else:
            wkid = int(self.service_epsg.value())
            short_name = ONLINE_SOURCES.get(key, {}).get("short_name", "Parcels") + "_Parcels"
        return layer_url, wkid, short_name

    def check_online_extent(self):
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            layer_url, wkid, _ = self._current_online_source()
            extent = transform_canvas_extent_to_service(self.canvas, wkid)
            count = count_online_parcels(extent, layer_url, wkid)
        except Exception as exc:
            self.extent_info.setText(self.tr(f"查询失败：{exc}", f"Query failed: {exc}"))
            QMessageBox.warning(self, PLUGIN_NAME, self.tr(f"无法查询在线地块：\n{exc}", f"Could not query parcels:\n{exc}"))
            return
        finally:
            QApplication.restoreOverrideCursor()
        if count > ONLINE_SOFT_LIMIT:
            suffix = self.tr("建议缩小地图范围后再下载。", "Zoom in before downloading.")
        else:
            suffix = self.tr("可以下载。", "Ready to download.")
        self.extent_info.setText(self.tr(
            f"当前工作范围约 {count:,} 个地块。{suffix}",
            f"Current work extent: about {count:,} parcels. {suffix}"
        ))
        self.status.setText(self.tr(f"在线检查：{count:,} 个地块", f"Online check: {count:,} parcels"))

    def download_current_extent(self):
        out_dir = self.output_dir.text().strip()
        if not out_dir:
            QMessageBox.warning(self, PLUGIN_NAME, self.tr("请先选择输出目录。", "Choose an output folder first."))
            return
        os.makedirs(out_dir, exist_ok=True)
        self._save_settings()

        # Capture QGIS work area before any network/file operations.
        work_extent = QgsRectangle(self.canvas.extent())
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            layer_url, wkid, short_name = self._current_online_source()
            service_extent = transform_canvas_extent_to_service(self.canvas, wkid)
            count = count_online_parcels(service_extent, layer_url, wkid)
        except Exception as exc:
            QMessageBox.warning(self, PLUGIN_NAME, self.tr(f"无法查询在线地块：\n{exc}", f"Could not query parcels:\n{exc}"))
            return
        finally:
            QApplication.restoreOverrideCursor()

        if count <= 0:
            QMessageBox.information(self, PLUGIN_NAME, self.tr(
                "当前工作范围没有返回地块。请确认地图位置、数据源和 EPSG。",
                "No parcels were returned for the current work extent. Check the map location, data source, and service EPSG."
            ))
            return

        if count > ONLINE_SOFT_LIMIT:
            answer = QMessageBox.question(self, PLUGIN_NAME, self.tr(
                f"当前范围约有 {count:,} 个地块，下载量较大。建议缩小地图范围。仍然继续？",
                f"The current extent contains about {count:,} parcels. Zooming in is recommended. Continue anyway?"
            ))
            if answer != QMessageBox.StandardButton.Yes:
                return

        self.progress.setValue(0)
        self.status.setText(self.tr(f"正在下载 {count:,} 个地块…", f"Downloading {count:,} parcels…"))

        def progress(done, total):
            pct = int((done / total) * 100) if total else 0
            self.progress.setValue(pct)
            self.status.setText(self.tr(f"下载中：{done:,} / {total:,}", f"Downloading: {done:,} / {total:,}"))
            QApplication.processEvents()

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            # parcel_io requests GeoJSON coordinates in EPSG:4326 on purpose. QGIS then
            # reprojects them correctly into the project's CRS and avoids GeoJSON CRS ambiguity.
            geojson = download_online_geojson(service_extent, layer_url, wkid, SERVICE_PAGE_SIZE, progress)
        except Exception as exc:
            QMessageBox.critical(self, PLUGIN_NAME, self.tr(f"地块下载失败：\n{exc}", f"Parcel download failed:\n{exc}"))
            self.status.setText(self.tr("下载失败", "Download failed"))
            return
        finally:
            QApplication.restoreOverrideCursor()

        features = geojson.get("features", [])
        if not features:
            QMessageBox.information(self, PLUGIN_NAME, self.tr("服务未返回地块要素。", "The service returned no parcel features."))
            return

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_base = short_name.replace(" ", "_")
        geojson_path = os.path.join(out_dir, f"{safe_base}_{stamp}.geojson")
        gpkg_path = os.path.join(out_dir, f"{safe_base}_{stamp}.gpkg")
        try:
            with open(geojson_path, "w", encoding="utf-8") as f:
                json.dump(geojson, f, ensure_ascii=False)
        except Exception as exc:
            QMessageBox.critical(self, PLUGIN_NAME, self.tr(f"保存 GeoJSON 失败：\n{exc}", f"Could not save GeoJSON:\n{exc}"))
            return

        layer_name = f"{safe_base}_{stamp}"
        temp_layer = QgsVectorLayer(geojson_path, layer_name, "ogr")
        if not temp_layer.isValid():
            QMessageBox.warning(self, PLUGIN_NAME, self.tr(
                f"数据已下载，但 QGIS 无法打开 GeoJSON。\n{geojson_path}",
                f"Data downloaded but QGIS could not open the GeoJSON.\n{geojson_path}"
            ))
            return

        if self._save_as_gpkg(temp_layer, gpkg_path, safe_base):
            del temp_layer
            try:
                os.remove(geojson_path)
            except OSError:
                pass
            final_layer = QgsVectorLayer(gpkg_path, layer_name, "ogr")
            saved_path = gpkg_path
        else:
            final_layer = temp_layer
            saved_path = geojson_path

        if not final_layer.isValid():
            QMessageBox.critical(self, PLUGIN_NAME, self.tr("文件已保存但无法载入 QGIS。", "Saved data could not be loaded into QGIS."))
            return

        QgsProject.instance().addMapLayer(final_layer)
        apn_field = detect_apn_field(final_layer)
        self._apply_parcel_style(final_layer, apn_field, self.chk_online_label.isChecked())
        self.current_layer = final_layer
        self.current_apn_field = apn_field
        self.current_online_layer = final_layer
        self.iface.setActiveLayer(final_layer)

        # Never replace the user's current work area with the downloaded layer extent.
        self.canvas.setExtent(work_extent)
        self.canvas.refresh()

        # Check the downloaded layer CRS against the current project CRS. Missing high-
        # accuracy datum grids are reported as a friendly warning, not as a failed download.
        self._check_layer_crs_transform(final_layer, interactive=True)

        self.progress.setValue(100)
        self.status.setText(self.tr(
            f"完成：{len(features):,} 个地块 → {saved_path}",
            f"Finished: {len(features):,} parcels → {saved_path}"
        ))
        QMessageBox.information(self, PLUGIN_NAME, self.tr(
            f"下载完成\n\n地块数：{len(features):,}\n保存位置：{saved_path}\nAPN字段：{apn_field or '未识别'}\n\n当前地图工作范围已保持不变。",
            f"Finished\n\nParcels: {len(features):,}\nSaved: {saved_path}\nAPN field: {apn_field or 'not detected'}\n\nThe current map work extent was preserved."
        ))

    def _save_as_gpkg(self, layer, gpkg_path, layer_name="Parcels"):
        try:
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.driverName = "GPKG"
            options.fileEncoding = "UTF-8"
            options.layerName = layer_name
            options.actionOnExistingFile = QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteFile
            result = QgsVectorFileWriter.writeAsVectorFormatV3(
                layer, gpkg_path, QgsProject.instance().transformContext(), options
            )
            error_code = result[0] if isinstance(result, tuple) else result
            return error_code == QgsVectorFileWriter.WriterError.NoError
        except Exception:
            try:
                result = QgsVectorFileWriter.writeAsVectorFormat(layer, gpkg_path, "UTF-8", layer.crs(), "GPKG")
                error_code = result[0] if isinstance(result, tuple) else result
                return error_code == QgsVectorFileWriter.WriterError.NoError
            except Exception:
                return False

    def search_loaded_apn(self):
        layer = self.current_layer
        field = self.current_apn_field
        if layer is None or not layer.isValid():
            layer = self.iface.activeLayer()
            if layer is not None and layer.isValid():
                field = detect_apn_field(layer)

        if layer is None or not layer.isValid():
            QMessageBox.information(self, PLUGIN_NAME, self.tr("请先加载或激活 Parcel 图层。", "Load or activate a Parcel layer first."))
            return
        if not field:
            QMessageBox.warning(self, PLUGIN_NAME, self.tr("未检测到 APN/Parcel ID 字段。", "No APN/Parcel ID field was detected."))
            return

        value = self.apn_search.text().strip()
        if not value:
            return
        normalized = value.replace("-", "").replace(" ", "").upper()
        expression = (
            f"upper(replace(replace(to_string(\"{field}\"), '-', ''), ' ', '')) = "
            f"{QgsExpression.quotedString(normalized)}"
        )
        request = QgsFeatureRequest().setFilterExpression(expression)
        ids = [f.id() for f in layer.getFeatures(request)]
        if not ids:
            QMessageBox.information(self, PLUGIN_NAME, self.tr(f"未找到 APN：{value}", f"APN not found: {value}"))
            return
        layer.selectByIds(ids)
        self.iface.setActiveLayer(layer)
        try:
            self.canvas.zoomToSelected(layer)
        except Exception as exc:
            QgsMessageLog.logMessage(str(exc), PLUGIN_NAME, Qgis.MessageLevel.Info)
        self.status.setText(self.tr(
            f"已选择 {len(ids)} 个地块：{value}",
            f"Selected {len(ids)} parcel(s): {value}"
        ))
