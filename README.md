# US Parcel / APN Data Manager for QGIS (`v0.7.0`)

Nationwide US (**All 50 States**) Parcel & APN Data Manager for QGIS. Supports local SHP/ZIP parcel scanning, built-in official state/county ArcGIS parcel services, dynamic 50-state US county parcel discovery, map point click parcel query, address/project-folder/APN search with geocoding spatial fallback, one-click satellite basemap, and a 4-language UI (`English` default, `简体中文`, `Español`, `Français`).

## Installation
1. Download the plugin `.zip` from Releases (or clone this repository into your QGIS `python/plugins/parcel_apn_manager` folder).
2. In QGIS, open **Plugins → Manage and Install Plugins → Install from ZIP**, or enable **US Parcel / APN Data Manager** in the Installed tab.

---

## v0.7.0 — Map Point Parcel Picker, Project Output Auto-Sync & Satellite Basemap
- **① Pick Parcel on Map (`btn_pick_map_parcel`)**:
  - Click directly on any building or lot on the QGIS map canvas to query and retrieve the exact parcel polygon at that point (supports all 50 US states via `auto_us`).
- **Auto-Set Output Folder to Project Folder (`chk_sync_project_output`)**:
  - When selecting a project folder via `Use Project Folder Name`, automatically syncs the Output Folder (`output_dir`) to that project folder so downloaded `.gpkg` parcels are saved directly inside the active project directory.
- **One-Click Satellite Basemap (`btn_basemap`)**:
  - Adds `Esri World Imagery (Satellite)` at the bottom of the QGIS layer tree with one click.
- **One-Click Copy APN / Address (`btn_copy_apn`) & WaitCursor Feedback**:
  - Copies `APN | Address | City | ZIP` of the selected parcel to the clipboard, and shows a busy cursor (`Qt.WaitCursor`) during online queries.

## v0.6.0 — Nationwide US (All 50 States) Coverage & 4-Language UI (English Default)
- **4-Language UI (`English` primary default, `简体中文`, `Español`, `Français`)**:
  - Replaced the 2-language toggle button with a 4-language dropdown (`lang_combo`), defaulting to **English (`en`)**.
- **Nationwide US Coverage (`auto_us` — All 50 States)**:
  - **Built-in Official Statewide & Metro Parcel Services**:
    - **Colorado (`CO`)**: Statewide Public Parcels (All 64 Counties)
    - **Washington (`WA`)**: Statewide Current Parcels (All 39 Counties)
    - **Wisconsin (`WI`)**: Statewide Parcels (All 72 Counties)
    - **New Jersey (`NJ`)**: Statewide Cadastral Parcels (All 21 Counties)
    - **California (`CA`)**: Los Angeles, San Diego, Riverside, San Bernardino Counties
    - **Utah (`UT`)**: Salt Lake County, Utah County
    - **Florida (`FL`)**: Miami-Dade County
    - **Arizona (`AZ`)**: 11 Counties (Maricopa, Pima, Pinal, Yavapai, Coconino, Yuma, Cochise, Navajo, Santa Cruz, Gila, Apache)
  - **Dynamic 50-State US County Auto-Discovery (`discover_us_parcel_service`)**:
    - For any other US county across all 50 states, automatically reverse-geocodes the map center or geocodes the target US address, queries ArcGIS Online for the county's public Polygon Parcel `FeatureServer`/`MapServer`, inspects its `EPSG`, `APN field`, and `Address field`, and connects automatically.

## v0.5.0 — Arizona 11-County Expansion & US ArcGIS Auto-Inspector
- **内置亚利桑那州 11 个主要县官方 Parcel 服务**：
  - `Maricopa County`（Phoenix / Scottsdale / Mesa / Tempe / Chandler / Gilbert / Glendale）
  - `Pinal County`（Casa Grande / Maricopa / Apache Junction / Coolidge / Florence）
  - `Pima County`（Tucson / Marana / Oro Valley / Sahuarita）
  - `Yavapai County`（Prescott / Sedona / Cottonwood / Camp Verde）
  - `Coconino County`（Flagstaff / Page / Williams）
  - `Yuma County`（Yuma / Somerton / San Luis）
  - `Cochise County`（Sierra Vista / Bisbee / Benson / Douglas）
  - `Navajo County`（Show Low / Winslow / Holbrook / Pinetop-Lakeside）
  - `Santa Cruz County`（Nogales / Rio Rico）
  - `Gila County`（Payson / Globe）
  - `Apache County`（St. Johns / Springerville / Eagar）
- **自动识别当前所在县（亚利桑那州）**：默认开启 `auto_az`，按当前地图中心或输入地址自动匹配所属县并切换对应官方 ArcGIS 服务与坐标系。
- **在线搜索框三合一（地址 / 项目文件夹名 / APN 直接查询）**：
  - 支持直接输入带横杠或纯数字 APN（如 `219-47-413`、`21947413`）在线检索并下载单个地块。
  - 当目标县图层不含地址字段或 SQL 模糊匹配未命中时，自动调用 ArcGIS World Geocoder 对美国地址进行地理编码，并通过空间点落面（`esriSpatialRelIntersects`）精准提取对应地块。
- **全美自定义 ArcGIS 图层一键探测**：新增“自动探测图层（Detect Layer）”按钮，自动读取任意美国县市 ArcGIS FeatureServer/MapServer 图层元数据（`?f=json`），自动识别 `EPSG`、`APN 字段` 与 `地址字段`。

## v0.4.2
- 修复在 QGIS 图层树手动移除地块图层后，插件仍持有已销毁 C++ 图层引用导致 `RuntimeError` 报错的问题（改为基于图层 ID 安全解析）。
- 修复在 Windows 下将临时 `.geojson` 转存为 `.gpkg` 后，因未先释放 `temp_layer` 句柄导致临时 `.geojson` 无法删除残留的问题。
- 面板增加纵向滚动条（`QScrollArea`），并将停靠面板最小宽度从 `520px` 优化为 `420px`，提升与右侧其他插件共存及小屏幕下的显示体验。
- 标签位置枚举兼容新版 QGIS `Qgis.LabelPlacement.Horizontal`。

## v0.4.1
- 默认以 QGIS 右侧 Dock Widget 停靠面板打开。
- 支持左右吸附、拖出悬浮、关闭后重新显示。
- 保留 v0.4.0 的地址→APN→单地块下载等功能。

# Parcel / APN Data Manager v0.4.0

QGIS Parcel/APN data manager with local-first loading, current-extent online download, live APN labels, CRS compatibility checks, and address-to-APN lookup.

## v0.4.0 — Address → APN → Parcel Download

- Added **Find APN / Parcel by Address** to the Online Parcel tab.
- Accepts ordinary addresses such as `42450 N 109th Pl`.
- Accepts underscore project-folder names such as `2606215_42450_N_109th_Pl`.
- Accepts a pasted folder path or lets the user choose a project folder directly.
- Automatically removes a leading long project/job number.
- Also handles descriptive project prefixes when a street number appears later, e.g. `2608265_McKeever_Residence_(4700_E_Chaparral_Rd)` → `4700 E CHAPARRAL RD`.
- Searches the official Maricopa County Assessor Parcel query layer by `PHYSICAL_ADDRESS`.
- Displays matching **APN, Property Address, City, ZIP** in a result table.
- Downloads only the selected parcel to GeoPackage and loads it into QGIS.
- Address download intentionally zooms to the matched parcel because this is an explicit target search.
- `APN_DASH` is now preferred for labels when available, so labels can display values such as `219-47-413` instead of undashed IDs.

## Existing behavior retained

- 中文 / English one-click UI switch.
- Local SHP/ZIP Parcel scanning.
- Current-map-extent online Parcel download.
- Orange semi-transparent Parcel fill with dark outlines.
- Live APN label show/hide toggle.
- Current work extent is preserved for normal extent downloads.
- CRS transformation compatibility checks and missing PROJ grid guidance.
