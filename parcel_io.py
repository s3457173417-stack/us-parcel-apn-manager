import json
import os
import re
import zipfile
from dataclasses import dataclass
from urllib.parse import urlencode

from qgis.PyQt.QtCore import QEventLoop, QTimer, QUrl
from qgis.PyQt.QtNetwork import QNetworkRequest
from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsMessageLog,
    QgsNetworkAccessManager,
    QgsProject,
    QgsRectangle,
    QgsVectorLayer,
    QgsWkbTypes,
)

from .sources import (
    ADDRESS_FIELD_CANDIDATES,
    APN_FIELD_CANDIDATES,
    ONLINE_SOURCES,
    PLUGIN_VERSION,
)

ARCGIS_GEOCODER_URL = (
    "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/findAddressCandidates"
)
ARCGIS_REVERSE_GEOCODER_URL = (
    "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/reverseGeocode"
)
ARCGIS_ONLINE_SEARCH_URL = "https://www.arcgis.com/sharing/rest/search"

_DISCOVERED_US_CACHE = {}


@dataclass
class ParcelCandidate:
    name: str
    source: str
    source_type: str
    status: str = ""
    apn_field: str = ""
    feature_count: int = -1
    crs_authid: str = ""
    valid: bool = False
    geometry_ok: bool = False
    companions_ok: bool = True


def _lower_field_map(layer):
    return {f.name().upper(): f.name() for f in layer.fields()}


def detect_apn_field_from_names(field_names):
    by_upper = {str(name).upper(): str(name) for name in (field_names or [])}
    for candidate in APN_FIELD_CANDIDATES:
        if candidate.upper() in by_upper:
            return by_upper[candidate.upper()]

    for name in field_names or []:
        n = str(name).upper().replace(" ", "_")
        if "APN" in n:
            return str(name)
    for name in field_names or []:
        n = str(name).upper().replace(" ", "_")
        if "PARCEL" in n and any(k in n for k in ("ID", "NO", "NUM", "NUMBER", "LABEL")):
            return str(name)
    if "PARCEL" in by_upper:
        return by_upper["PARCEL"]
    return ""


def detect_address_field_from_names(field_names):
    by_upper = {str(name).upper(): str(name) for name in (field_names or [])}
    for candidate in ADDRESS_FIELD_CANDIDATES:
        if candidate.upper() in by_upper:
            return by_upper[candidate.upper()]
    for name in field_names or []:
        n = str(name).upper()
        if ("SITUS" in n or "SITE" in n or "PHYSICAL" in n or "PROP" in n) and "ADDR" in n:
            return str(name)
    return ""


def detect_apn_field(layer):
    return detect_apn_field_from_names([f.name() for f in layer.fields()])


def _validate_vector_source(name, source, source_type, companions_ok=True):
    c = ParcelCandidate(name=name, source=source, source_type=source_type)
    c.companions_ok = companions_ok
    layer = QgsVectorLayer(source, name, "ogr")
    if not layer.isValid():
        c.status = "Invalid / 无效"
        return c

    c.valid = True
    c.geometry_ok = QgsWkbTypes.geometryType(layer.wkbType()) == QgsWkbTypes.GeometryType.PolygonGeometry
    try:
        c.feature_count = int(layer.featureCount())
    except Exception:
        c.feature_count = -1
    c.crs_authid = layer.crs().authid() if layer.crs().isValid() else "Unknown / 未知"
    c.apn_field = detect_apn_field(layer)

    messages = []
    if not c.geometry_ok:
        messages.append("Not Polygon / 非面图层")
    if not c.apn_field:
        messages.append("APN not detected / 未识别APN字段")
    if not companions_ok:
        messages.append("SHP incomplete / SHP配套文件不完整")
    if not messages:
        messages.append("Ready / 可用")
    c.status = "; ".join(messages)
    return c


def _shp_companions_ok(shp_path):
    stem, _ = os.path.splitext(shp_path)
    return os.path.exists(stem + ".dbf") and os.path.exists(stem + ".shx")


def scan_shapefile(shp_path):
    name = os.path.splitext(os.path.basename(shp_path))[0]
    return _validate_vector_source(
        name=name,
        source=os.path.abspath(shp_path),
        source_type="SHP",
        companions_ok=_shp_companions_ok(shp_path),
    )


def scan_zip(zip_path):
    results = []
    abs_zip = os.path.abspath(zip_path)
    try:
        with zipfile.ZipFile(abs_zip, "r") as zf:
            names = zf.namelist()
    except Exception:
        return results

    lower = {n.lower(): n for n in names}
    shp_names = [n for n in names if n.lower().endswith(".shp")]
    for internal in shp_names:
        stem = internal[:-4]
        companions_ok = (stem + ".dbf").lower() in lower and (stem + ".shx").lower() in lower
        vsi_zip = abs_zip.replace("\\", "/")
        source = "/vsizip/" + vsi_zip + "/" + internal
        results.append(
            _validate_vector_source(
                name=os.path.splitext(os.path.basename(internal))[0],
                source=source,
                source_type="ZIP/SHP",
                companions_ok=companions_ok,
            )
        )
    return results


def scan_path(path):
    results = []
    path = os.path.abspath(path)
    if os.path.isfile(path):
        if path.lower().endswith(".shp"):
            results.append(scan_shapefile(path))
        elif path.lower().endswith(".zip"):
            results.extend(scan_zip(path))
        return results

    if not os.path.isdir(path):
        return results

    root_depth = path.rstrip(os.sep).count(os.sep)
    for root, dirs, files in os.walk(path):
        depth = root.rstrip(os.sep).count(os.sep) - root_depth
        if depth >= 3:
            dirs[:] = []
        parent_hint = os.path.basename(root).lower()
        for fn in files:
            full = os.path.join(root, fn)
            low = fn.lower()
            if low.endswith(".shp"):
                candidate = scan_shapefile(full)
                if (
                    candidate.apn_field
                    or "parcel" in low
                    or "apn" in low
                    or "parcel" in parent_hint
                    or "apn" in parent_hint
                ):
                    results.append(candidate)
            elif low.endswith(".zip") and ("parcel" in low or "apn" in low):
                results.extend(scan_zip(full))

    results.sort(
        key=lambda c: (
            0 if (c.valid and c.geometry_ok and c.apn_field) else 1,
            0 if c.valid else 1,
            c.name.lower(),
        )
    )
    return results


def normalize_layer_url(url):
    url = (url or "").strip().rstrip("/")
    if url.lower().endswith("/query"):
        url = url[:-6].rstrip("/")
    return url


def query_url(layer_url):
    return normalize_layer_url(layer_url) + "/query"


def transform_canvas_extent_to_service(canvas, service_wkid):
    extent = canvas.extent()
    src = canvas.mapSettings().destinationCrs()
    dst = QgsCoordinateReferenceSystem(f"EPSG:{int(service_wkid)}")
    if not src.isValid():
        raise RuntimeError("Current QGIS CRS is invalid / 当前QGIS坐标系无效。")
    if not dst.isValid():
        raise RuntimeError(f"Invalid service EPSG: {service_wkid} / 服务EPSG无效。")
    if src == dst:
        return QgsRectangle(extent)
    tx = QgsCoordinateTransform(src, dst, QgsProject.instance())
    return tx.transformBoundingBox(extent)


def transform_canvas_center_wgs84(canvas):
    center = canvas.extent().center()
    src = canvas.mapSettings().destinationCrs()
    dst = QgsCoordinateReferenceSystem("EPSG:4326")
    if not src.isValid() or src == dst:
        return center.x(), center.y()
    tx = QgsCoordinateTransform(src, dst, QgsProject.instance())
    pt = tx.transform(center)
    return pt.x(), pt.y()


def detect_us_source_by_lonlat(lon, lat, subregion="", state_abbr=""):
    """Return the ONLINE_SOURCES key matching the given US (lon, lat), county, or state."""
    norm_state = (state_abbr or "").strip().upper()
    norm_sub = re.sub(r"\b(?:county|parish|borough)\b", "", str(subregion or ""), flags=re.I).strip().lower().replace(" ", "_")

    if norm_sub:
        if norm_sub in ONLINE_SOURCES:
            return norm_sub
        if norm_state:
            prefixed = f"{norm_state.lower()}_{norm_sub}"
            if prefixed in ONLINE_SOURCES:
                return prefixed
        for key, cfg in ONLINE_SOURCES.items():
            if cfg.get("county") == norm_sub and (not norm_state or cfg.get("state") == norm_state):
                return key

    if norm_state:
        for key, cfg in ONLINE_SOURCES.items():
            if cfg.get("state") == norm_state and cfg.get("county") == "*":
                return key

    try:
        lon = float(lon)
        lat = float(lat)
    except Exception:
        return "maricopa"

    county_matches = []
    state_matches = []
    for key, cfg in ONLINE_SOURCES.items():
        bbox = cfg.get("bbox_wgs84")
        if not bbox:
            continue
        xmin, ymin, xmax, ymax = bbox
        if xmin <= lon <= xmax and ymin <= lat <= ymax:
            cx = 0.5 * (xmin + xmax)
            cy = 0.5 * (ymin + ymax)
            dist2 = (lon - cx) ** 2 + (lat - cy) ** 2
            if cfg.get("county") == "*":
                state_matches.append((dist2, key))
            else:
                county_matches.append((dist2, key))

    if county_matches:
        county_matches.sort(key=lambda x: x[0])
        return county_matches[0][1]
    if state_matches:
        state_matches.sort(key=lambda x: x[0])
        return state_matches[0][1]
    return ""


def detect_az_county_by_lonlat(lon, lat, subregion=""):
    """Backward-compatible wrapper returning a matched source key (or 'maricopa' fallback)."""
    return detect_us_source_by_lonlat(lon, lat, subregion=subregion) or "maricopa"


def _network_get_json(url, timeout_ms=60000):
    request = QNetworkRequest(QUrl(url))
    request.setRawHeader(b"User-Agent", f"QGIS-Parcel-APN-Data-Manager/{PLUGIN_VERSION}".encode("utf-8"))
    reply = QgsNetworkAccessManager.instance().get(request)

    loop = QEventLoop()
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    reply.finished.connect(loop.quit)
    timer.start(timeout_ms)
    loop.exec()

    if not timer.isActive():
        reply.abort()
        reply.deleteLater()
        raise TimeoutError("Request timed out / 在线请求超时。")
    timer.stop()

    if reply.error():
        msg = reply.errorString()
        reply.deleteLater()
        raise RuntimeError(f"Network error / 网络错误: {msg}")

    raw = bytes(reply.readAll())
    reply.deleteLater()
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        snippet = raw[:240].decode("utf-8", errors="replace")
        raise RuntimeError(f"Server did not return JSON / 服务器未返回JSON: {snippet}") from exc

    if isinstance(data, dict) and data.get("error"):
        err = data["error"]
        raise RuntimeError(f"ArcGIS error / ArcGIS服务错误: {err.get('message', err)}")
    return data


def inspect_arcgis_layer_metadata(layer_url):
    """Fetch ArcGIS layer ?f=json metadata to detect wkid, APN field, and address field."""
    base = normalize_layer_url(layer_url)
    if not base:
        return {}
    if not re.search(r"/(?:FeatureServer|MapServer)/\d+$", base, flags=re.I):
        base = base.rstrip("/") + "/0"
    data = _network_get_json(base + "?f=json", timeout_ms=20000)
    sr = (
        (data.get("extent") or {}).get("spatialReference")
        or data.get("sourceSpatialReference")
        or data.get("spatialReference")
        or {}
    )
    wkid = sr.get("latestWkid") or sr.get("wkid")
    if not wkid:
        # Try parent service JSON for spatialReference if layer JSON omitted it
        parent = re.sub(r"/\d+$", "", base)
        try:
            pdata = _network_get_json(parent + "?f=json", timeout_ms=12000)
            psr = (
                (pdata.get("fullExtent") or {}).get("spatialReference")
                or pdata.get("spatialReference")
                or {}
            )
            wkid = psr.get("latestWkid") or psr.get("wkid") or 3857
        except Exception:
            wkid = 3857
    fields = [f.get("name") for f in (data.get("fields") or []) if f.get("name")]
    apn_field = detect_apn_field_from_names(fields)
    addr_field = detect_address_field_from_names(fields)
    return {
        "layer_url": base,
        "name": data.get("name") or "Custom",
        "geometry_type": data.get("geometryType") or "",
        "wkid": int(wkid) if str(wkid).isdigit() else 3857,
        "fields": fields,
        "apn_field": apn_field,
        "address_field": addr_field,
    }


def reverse_geocode_us_point(lon, lat):
    """Return {'county', 'state_abbr', 'state_name', 'city'} for any WGS84 point in the US."""
    params = {
        "location": f"{float(lon):.6f},{float(lat):.6f}",
        "f": "json",
    }
    try:
        data = _network_get_json(ARCGIS_REVERSE_GEOCODER_URL + "?" + urlencode(params), timeout_ms=15000)
        addr = data.get("address") or {}
        return {
            "county": addr.get("Subregion") or "",
            "state_abbr": addr.get("RegionAbbr") or "",
            "state_name": addr.get("Region") or "",
            "city": addr.get("City") or "",
        }
    except Exception:
        return {"county": "", "state_abbr": "", "state_name": "", "city": ""}


def discover_us_parcel_service(lon, lat, county="", state_abbr="", state_name=""):
    """Resolve a US Parcel ArcGIS service for any location across all 50 US states.

    1) Checks built-in verified ONLINE_SOURCES by county, state, or bbox.
    2) Reverse-geocodes (lon, lat) if county/state are not yet known.
    3) Dynamically searches ArcGIS Online public Feature/Map Services for that US County/State.
    """
    matched_key = detect_us_source_by_lonlat(lon, lat, subregion=county, state_abbr=state_abbr)
    if matched_key and matched_key in ONLINE_SOURCES:
        return dict(ONLINE_SOURCES[matched_key])

    if not county or not state_abbr:
        rev = reverse_geocode_us_point(lon, lat)
        county = county or rev.get("county", "")
        state_abbr = state_abbr or rev.get("state_abbr", "")
        state_name = state_name or rev.get("state_name", "")
        matched_key = detect_us_source_by_lonlat(lon, lat, subregion=county, state_abbr=state_abbr)
        if matched_key and matched_key in ONLINE_SOURCES:
            return dict(ONLINE_SOURCES[matched_key])

    cache_key = f"{state_abbr.upper()}_{county.lower()}".strip("_")
    if cache_key and cache_key in _DISCOVERED_US_CACHE:
        return dict(_DISCOVERED_US_CACHE[cache_key])

    clean_county = re.sub(r"\b(?:county|parish|borough)\b", "", county or "", flags=re.I).strip()
    if clean_county and (state_abbr or state_name):
        q = (
            f'title:Parcels AND ("{clean_county} County" OR "{clean_county}") '
            f'AND ("{state_abbr}" OR "{state_name}") '
            f'AND (type:"Feature Service" OR type:"Map Service")'
        )
        params = {"q": q, "num": "8", "f": "json"}
        try:
            search_data = _network_get_json(ARCGIS_ONLINE_SEARCH_URL + "?" + urlencode(params), timeout_ms=20000)
            for item in search_data.get("results") or []:
                raw_url = item.get("url") or ""
                if not raw_url or "tiles.arcgis.com" in raw_url:
                    continue
                if "FeatureServer" not in raw_url and "MapServer" not in raw_url:
                    continue
                try:
                    meta = inspect_arcgis_layer_metadata(raw_url)
                    if meta.get("geometry_type") not in ("", "esriGeometryPolygon"):
                        continue
                    apn_f = meta.get("apn_field") or "APN"
                    addr_f = meta.get("address_field") or ""
                    short = f"{state_abbr}_{clean_county.replace(' ', '')}" if state_abbr else clean_county.replace(" ", "")
                    cfg = {
                        "state": state_abbr.upper(),
                        "county": clean_county.lower(),
                        "short_name": short or "US_Parcels",
                        "layer_url": meta["layer_url"],
                        "wkid": meta["wkid"],
                        "address_layer_url": meta["layer_url"],
                        "address_field": addr_f,
                        "address_city_field": "",
                        "address_zip_field": "",
                        "address_apn_field": apn_f,
                        "apn_search_fields": [apn_f] if apn_f else [],
                    }
                    if cache_key:
                        _DISCOVERED_US_CACHE[cache_key] = cfg
                    return dict(cfg)
                except Exception as exc:
                    QgsMessageLog.logMessage(str(exc), "ParcelAPNManager", Qgis.MessageLevel.Info)
        except Exception as exc:
            QgsMessageLog.logMessage(str(exc), "ParcelAPNManager", Qgis.MessageLevel.Info)

    return dict(ONLINE_SOURCES["maricopa"])


def _extent_params(extent, service_wkid):
    return {
        "where": "1=1",
        "geometry": f"{extent.xMinimum()},{extent.yMinimum()},{extent.xMaximum()},{extent.yMaximum()}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": str(int(service_wkid)),
        "spatialRel": "esriSpatialRelIntersects",
        "f": "json",
    }


def count_online_parcels(extent, layer_url, service_wkid):
    params = _extent_params(extent, service_wkid)
    params["returnCountOnly"] = "true"
    data = _network_get_json(query_url(layer_url) + "?" + urlencode(params))
    return int(data.get("count", 0))


def get_online_object_ids(extent, layer_url, service_wkid):
    params = _extent_params(extent, service_wkid)
    params["returnIdsOnly"] = "true"
    data = _network_get_json(query_url(layer_url) + "?" + urlencode(params))
    ids = data.get("objectIds") or []
    return [int(v) for v in ids]


def download_online_geojson(extent, layer_url, service_wkid, page_size=500, progress_callback=None):
    object_ids = get_online_object_ids(extent, layer_url, service_wkid)
    if not object_ids:
        return {"type": "FeatureCollection", "features": []}

    merged = None
    total = len(object_ids)
    for start in range(0, total, page_size):
        chunk = object_ids[start:start + page_size]
        params = {
            "objectIds": ",".join(str(x) for x in chunk),
            "outFields": "*",
            "returnGeometry": "true",
            "outSR": "4326",
            "f": "geojson",
        }
        data = _network_get_json(query_url(layer_url) + "?" + urlencode(params), timeout_ms=120000)
        if data.get("type") != "FeatureCollection":
            raise RuntimeError("Not a GeoJSON FeatureCollection / 服务未返回GeoJSON FeatureCollection。")
        if merged is None:
            merged = {k: v for k, v in data.items() if k != "features"}
            merged["features"] = []
        merged["features"].extend(data.get("features", []))
        if progress_callback:
            progress_callback(min(start + len(chunk), total), total)
    return merged or {"type": "FeatureCollection", "features": []}


# ---------------- Address & APN lookup helpers ----------------

def is_probable_apn_query(value):
    """Return True if user input looks like an APN rather than a street address.

    Examples matching True:
      219-47-413
      219-47-413A
      21947413
      APN 219-47-413
      104_22_015B
    Examples matching False:
      42450 N 109th Pl
      2606215_42450_N_109th_Pl
    """
    text = (value or "").strip().strip('"').strip("'")
    if not text:
        return False
    text = re.split(r"[\\/]", text.rstrip("\\/"))[-1]
    text = re.sub(r"^(?:APN|PARCEL|PIN)[:\s#_-]*", "", text, flags=re.I).strip()

    # Dashed/underscored/spaced 3-part or 4-part APN: e.g. 219-47-413A or 104-12-034-01
    if re.fullmatch(r"\d{2,4}[-_\s]\d{2,4}[-_\s]\d{2,5}[A-Za-z]?(?:[-_\s]\d{1,4})?", text):
        return True
    # Compact 7-11 digit APN with optional trailing letter: e.g. 21947413 or 21947413A
    if re.fullmatch(r"\d{7,11}[A-Za-z]?", text):
        return True
    return False


def normalize_apn_input(value):
    """Return (dashed_apn, compact_apn) from an APN input string."""
    text = (value or "").strip().strip('"').strip("'")
    text = re.split(r"[\\/]", text.rstrip("\\/"))[-1]
    text = re.sub(r"^(?:APN|PARCEL|PIN)[:\s#_-]*", "", text, flags=re.I).strip().upper()
    compact = re.sub(r"[^A-Z0-9]", "", text)
    if re.search(r"[-_\s]", text):
        dashed = re.sub(r"[-_\s]+", "-", text).strip("-")
    elif re.fullmatch(r"\d{8}[A-Z]?", compact):
        # Standard Arizona 8-digit APN: BBB-MM-PPP[A]
        dashed = f"{compact[:3]}-{compact[3:5]}-{compact[5:]}"
    elif re.fullmatch(r"\d{9}[A-Z]?", compact):
        dashed = f"{compact[:3]}-{compact[3:5]}-{compact[5:]}"
    else:
        dashed = compact
    return dashed, compact


def normalize_address_input(value):
    """Normalize a typed address or an underscore-based project-folder name.

    Examples:
      2606215_42450_N_109th_Pl -> 42450 N 109TH PL
      42450 N 109th Pl        -> 42450 N 109TH PL
      D:\\Jobs\\2606215_42450_N_109th_Pl -> 42450 N 109TH PL
    """
    text = (value or "").strip().strip('"').strip("'")
    if not text:
        return ""

    text = re.split(r"[\\/]", text.rstrip("\\/"))[-1]
    text = re.sub(r"\.(shp|zip|gpkg|geojson)$", "", text, flags=re.I)
    text = text.replace("_", " ")
    text = re.sub(r"\s+", " ", text).strip()

    parts = text.split()
    if len(parts) >= 2 and re.fullmatch(r"\d{6,}", parts[0]):
        street_idx = None
        for i, token in enumerate(parts[1:], start=1):
            cleaned = token.strip(" ,.;()[]{}")
            if re.fullmatch(r"\d{2,6}[A-Za-z]?", cleaned):
                street_idx = i
                break
        parts = parts[street_idx:] if street_idx is not None else parts[1:]

    direction_map = {
        "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W",
        "NORTHEAST": "NE", "NORTHWEST": "NW", "SOUTHEAST": "SE", "SOUTHWEST": "SW",
    }
    suffix_map = {
        "STREET": "ST", "ST": "ST",
        "ROAD": "RD", "RD": "RD",
        "DRIVE": "DR", "DR": "DR",
        "PLACE": "PL", "PL": "PL",
        "LANE": "LN", "LN": "LN",
        "COURT": "CT", "CT": "CT",
        "AVENUE": "AVE", "AVE": "AVE",
        "BOULEVARD": "BLVD", "BLVD": "BLVD",
        "HIGHWAY": "HWY", "HWY": "HWY",
        "PARKWAY": "PKWY", "PKWY": "PKWY",
        "TRAIL": "TRL", "TRL": "TRL",
        "TERRACE": "TER", "TER": "TER",
        "CIRCLE": "CIR", "CIR": "CIR",
        "WAY": "WAY",
    }

    normalized = []
    for token in parts:
        t = token.strip(" ,.;()[]{}").upper()
        if not t:
            continue
        t = direction_map.get(t, t)
        t = suffix_map.get(t, t)
        normalized.append(t)
    return " ".join(normalized)


def _sql_escape_literal(text):
    return (text or "").replace("'", "''")


def search_online_apn(apn_text, layer_url, apn_fields=None, result_limit=30):
    """Search an online ArcGIS Parcel layer by APN (dashed or compact)."""
    dashed, compact = normalize_apn_input(apn_text)
    if not compact:
        return dashed, {"type": "FeatureCollection", "features": []}

    fields = [f for f in (apn_fields or []) if f]
    if not fields:
        try:
            meta = inspect_arcgis_layer_metadata(layer_url)
            if meta.get("apn_field"):
                fields = [meta["apn_field"]]
        except Exception as exc:
            QgsMessageLog.logMessage(str(exc), "ParcelAPNManager", Qgis.MessageLevel.Info)
    if not fields:
        fields = ["APN_DASH", "APN"]

    clauses = []
    for f in fields:
        col = f.replace('"', "")
        for candidate in {dashed, compact}:
            if candidate:
                esc = _sql_escape_literal(candidate)
                clauses.append(f"UPPER(CAST({col} AS VARCHAR(64))) LIKE '%{esc}%'")

    where = " OR ".join(clauses) if clauses else "1=0"
    params = {
        "where": where,
        "outFields": "*",
        "returnGeometry": "true",
        "outSR": "4326",
        "resultRecordCount": str(int(result_limit)),
        "f": "geojson",
    }
    try:
        data = _network_get_json(query_url(layer_url) + "?" + urlencode(params), timeout_ms=60000)
    except Exception:
        # Some ArcGIS backends reject CAST(...); fall back to plain UPPER(col) LIKE
        clauses_simple = []
        for f in fields:
            col = f.replace('"', "")
            for candidate in {dashed, compact}:
                if candidate:
                    esc = _sql_escape_literal(candidate)
                    clauses_simple.append(f"UPPER({col}) LIKE '%{esc}%'")
        params["where"] = " OR ".join(clauses_simple) if clauses_simple else "1=0"
        data = _network_get_json(query_url(layer_url) + "?" + urlencode(params), timeout_ms=60000)

    if data.get("type") != "FeatureCollection":
        return dashed, {"type": "FeatureCollection", "features": []}
    return dashed, data


def geocode_us_address(address_text, max_locations=5, bias_lonlat=None):
    """Geocode any US address using the public ArcGIS World Geocoder."""
    query = (address_text or "").strip()
    if not query:
        return []

    params = {
        "SingleLine": query,
        "countryCode": "USA",
        "outFields": "StAddr,City,Subregion,Region,RegionAbbr,Postal,Match_addr",
        "maxLocations": str(int(max_locations)),
        "f": "json",
    }
    if bias_lonlat and len(bias_lonlat) == 2:
        try:
            params["location"] = f"{float(bias_lonlat[0]):.5f},{float(bias_lonlat[1]):.5f}"
        except Exception as exc:
            QgsMessageLog.logMessage(str(exc), "ParcelAPNManager", Qgis.MessageLevel.Info)

    try:
        data = _network_get_json(ARCGIS_GEOCODER_URL + "?" + urlencode(params), timeout_ms=25000)
    except Exception:
        return []

    results = []
    for cand in data.get("candidates") or []:
        if (cand.get("score") or 0) < 70:
            continue
        loc = cand.get("location") or {}
        lon = loc.get("x")
        lat = loc.get("y")
        if lon is None or lat is None:
            continue
        attrs = cand.get("attributes") or {}
        results.append({
            "lon": float(lon),
            "lat": float(lat),
            "address": attrs.get("StAddr") or cand.get("address") or query,
            "city": attrs.get("City") or "",
            "county": attrs.get("Subregion") or "",
            "state": attrs.get("RegionAbbr") or "",
            "state_name": attrs.get("Region") or "",
            "zip": attrs.get("Postal") or "",
            "match_addr": cand.get("address") or "",
        })
    return results


def query_parcel_at_point(lon, lat, layer_url, result_limit=10):
    """Query an ArcGIS polygon Parcel layer by WGS84 point-in-polygon intersection."""
    params = {
        "where": "1=1",
        "geometry": f"{float(lon):.7f},{float(lat):.7f}",
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "*",
        "returnGeometry": "true",
        "outSR": "4326",
        "resultRecordCount": str(int(result_limit)),
        "f": "geojson",
    }
    data = _network_get_json(query_url(layer_url) + "?" + urlencode(params), timeout_ms=45000)
    if data.get("type") != "FeatureCollection":
        return {"type": "FeatureCollection", "features": []}
    return data


def search_online_address(
    address_text,
    layer_url,
    address_field="PHYSICAL_ADDRESS",
    result_limit=30,
    fallback_point_layer_url="",
    apn_fields=None,
):
    """Search an ArcGIS polygon layer for a property address (or APN) and return GeoJSON.

    1) If input looks like an APN, searches APN fields directly.
    2) Otherwise searches address_field via SQL LIKE.
    3) If SQL LIKE returns 0 features (or address_field is empty), geocodes the
       address and performs a spatial point-in-polygon query on the parcel layer.
    """
    if is_probable_apn_query(address_text):
        dashed, data = search_online_apn(
            address_text,
            fallback_point_layer_url or layer_url,
            apn_fields=apn_fields,
            result_limit=result_limit,
        )
        return f"APN {dashed}", data

    normalized = normalize_address_input(address_text)
    if not normalized:
        return normalized, {"type": "FeatureCollection", "features": []}

    # Strip trailing city/state/zip tokens from SQL LIKE on street-only fields if present
    street_only = re.split(r"\b(?:PHOENIX|SCOTTSDALE|MESA|TEMPE|CHANDLER|GLENDALE|GILBERT|PEORIA|TUCSON|CASA GRANDE|PRESCOTT|FLAGSTAFF|YUMA|AZ|ARIZONA)\b", normalized)[0].strip(" ,")
    query_tokens = [t for t in (street_only or normalized).split() if t]

    if address_field and layer_url and query_tokens:
        wildcard = "%" + "%".join(_sql_escape_literal(t) for t in query_tokens) + "%"
        field = address_field.replace('"', "")
        where = f"UPPER({field}) LIKE '{wildcard}'"
        params = {
            "where": where,
            "outFields": "*",
            "returnGeometry": "true",
            "outSR": "4326",
            "resultRecordCount": str(int(result_limit)),
            "f": "geojson",
        }
        try:
            data = _network_get_json(query_url(layer_url) + "?" + urlencode(params), timeout_ms=60000)
            if data.get("type") == "FeatureCollection" and data.get("features"):
                return normalized, data
        except Exception as exc:
            QgsMessageLog.logMessage(str(exc), "ParcelAPNManager", Qgis.MessageLevel.Info)

    # Fallback: Geocode US/AZ address -> spatial point-in-polygon parcel query
    candidates = geocode_us_address(normalized, max_locations=3)
    target_url = normalize_layer_url(fallback_point_layer_url or layer_url)
    if candidates and target_url:
        merged_features = []
        seen_keys = set()
        for cand in candidates:
            try:
                fc = query_parcel_at_point(cand["lon"], cand["lat"], target_url, result_limit=5)
                for feat in fc.get("features") or []:
                    props = feat.setdefault("properties", {})
                    if not props.get(address_field or "PHYSICAL_ADDRESS") and not props.get("PHYSICAL_ADDRESS"):
                        props["PHYSICAL_ADDRESS"] = cand.get("address") or normalized
                    if not props.get("PHYSICAL_CITY"):
                        props["PHYSICAL_CITY"] = cand.get("city") or ""
                    if not props.get("PHYSICAL_ZIP"):
                        props["PHYSICAL_ZIP"] = cand.get("zip") or ""
                    key = json.dumps(feat.get("geometry"), sort_keys=True)
                    if key not in seen_keys:
                        seen_keys.add(key)
                        merged_features.append(feat)
            except Exception as exc:
                QgsMessageLog.logMessage(str(exc), "ParcelAPNManager", Qgis.MessageLevel.Info)
        if merged_features:
            return normalized, {"type": "FeatureCollection", "features": merged_features}

    return normalized, {"type": "FeatureCollection", "features": []}
