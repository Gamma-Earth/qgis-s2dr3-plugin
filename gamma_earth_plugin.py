# --- Gamma Earth S2DR3 QGIS Plugin (3 AOI Modes + CDSE Helper) ---

from PyQt5.QtWidgets import (
    QAction, QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QComboBox,
    QProgressBar, QFileDialog, QRadioButton, QButtonGroup, QMessageBox, QPlainTextEdit,
    QTableWidget, QTableWidgetItem, QHeaderView
)
from PyQt5.QtGui import QIcon
from PyQt5.QtCore import QObject, QThread, pyqtSignal, QSettings
from qgis.core import (
    QgsProject, QgsRasterLayer, QgsVectorLayer
)
import requests
import os
import tempfile
import time
import math


API_BASE_URL = "https://api.gamma.earth/jobs"


def storage_url(path):
    """Convert an s3:// or gs:// path into a public HTTPS URL."""
    if path.startswith("s3://"):
        bucket, _, key = path[len("s3://"):].partition("/")
        return f"https://{bucket}.s3.amazonaws.com/{key}"
    if path.startswith("gs://"):
        return path.replace("gs://", "https://storage.googleapis.com/")
    return path


def redact(text, api_key):
    """requests errors embed the request URL, which contains the API key."""
    return text.replace(api_key, "***") if api_key else text


def settings_default_range():
    return QSettings().value("GammaEarth/date_range", "7")


# ============================================================
# Download Worker (runs in background thread)
# ============================================================
class DownloadWorker(QObject):
    progress = pyqtSignal(int)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, url, save_path):
        super().__init__()
        self.url = url
        self.save_path = save_path

    def run(self):
        try:
            r = requests.get(self.url, stream=True, timeout=60)
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0))
            downloaded = 0

            with open(self.save_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0:
                            self.progress.emit(int(downloaded / total * 100))

            self.finished.emit(self.save_path)
        except Exception as e:
            self.error.emit(str(e))


# ============================================================
# Poll Worker (checks for file availability in background)
# ============================================================
class PollWorker(QObject):
    ready = pyqtSignal(str)      # URL when file is ready
    timeout = pyqtSignal(str)    # URL when timeout
    error = pyqtSignal(str)      # error message

    def __init__(self, url, attempts=50, wait_seconds=6):
        super().__init__()
        self.url = url
        self.attempts = attempts
        self.wait_seconds = wait_seconds

    def run(self):
        try:
            for _ in range(self.attempts):
                time.sleep(self.wait_seconds)
                try:
                    resp = requests.head(self.url, timeout=15)
                    if resp.status_code == 200:
                        self.ready.emit(self.url)
                        return
                except Exception as e:
                    print(f"[GammaEarth] HEAD check failed: {e}")
                    continue
            self.timeout.emit(self.url)
        except Exception as e:
            self.error.emit(str(e))


# ============================================================
# Credits Worker (GET <API_BASE_URL>/<api_key> in the background)
# ============================================================
class CreditsWorker(QObject):
    result = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, api_key):
        super().__init__()
        self.api_key = api_key

    def run(self):
        try:
            r = requests.get(f"{API_BASE_URL}/{self.api_key}", timeout=30)
            if r.status_code != 200:
                try:
                    msg = r.json().get("error", r.text)
                except ValueError:
                    msg = r.text
                self.error.emit(f"{r.status_code}: {msg}")
                return
            self.result.emit(r.json())
        except Exception as e:
            self.error.emit(redact(str(e), self.api_key))


# ============================================================
# AOI Dialog (3 AOI input methods + CDSE helper)
# ============================================================
class AOIDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Gamma Earth S2DR3 QGIS Plugin")
        self.setMinimumWidth(480)

        layout = QVBoxLayout()

        # --- AOI method selection ---
        layout.addWidget(QLabel("<b>AOI input method</b>"))

        self.aoi_group = QButtonGroup(self)

        self.rb_selection = QRadioButton("Use selected polygon from active layer")
        self.rb_square = QRadioButton("Define square by center (lon/lat) and side (km)")
        self.rb_geojson = QRadioButton("Use AOI from GeoJSON file")

        self.rb_selection.setChecked(True)

        for i, rb in enumerate([self.rb_selection, self.rb_square, self.rb_geojson]):
            self.aoi_group.addButton(rb, i)
            layout.addWidget(rb)

        # Selected polygon feedback (method 1)
        self.selection_info = QLabel(
            "<i style='color:#2e7d32;'>Select a polygon in the active layer.</i>"
        )
        layout.addWidget(self.selection_info)

        # --- Square: center lon/lat + side (km) ---
        self.center_lon_label = QLabel("Center Lon")
        self.center_lon = QLineEdit()
        self.center_lat_label = QLabel("Center Lat")
        self.center_lat = QLineEdit()
        self.side_km_label = QLabel("Side (km)")
        self.side_km = QLineEdit()

        row = QHBoxLayout()
        row.addWidget(self.center_lon_label)
        row.addWidget(self.center_lon)
        row.addWidget(self.center_lat_label)
        row.addWidget(self.center_lat)
        row.addWidget(self.side_km_label)
        row.addWidget(self.side_km)
        layout.addLayout(row)

        # --- GeoJSON path ---
        self.geojson_path = QLineEdit()
        self.geojson_browse = QPushButton("Browse GeoJSON")
        self.geojson_browse.clicked.connect(self.browse_geojson)

        row = QHBoxLayout()
        row.addWidget(self.geojson_path)
        row.addWidget(self.geojson_browse)
        layout.addLayout(row)

        # --- Copernicus CDSE helper (above date field) ---
        layout.addWidget(QLabel("<b>Sentinel-2 date search (Copernicus CDSE, optional)</b>"))

        help_label = QLabel(
            "<i>Use this section to list available Sentinel-2 L2A scenes for the current AOI, "
            "filtered by date range and maximum cloud cover. Use the results to choose the preferred "
            "date for Gamma Earth S2DR3 processing.</i>"
        )
        help_label.setWordWrap(True)
        layout.addWidget(help_label)


        cdse_row = QHBoxLayout()
        self.cdse_from = QLineEdit()
        self.cdse_to = QLineEdit()
        self.cdse_cloud = QLineEdit()
        self.cdse_from.setPlaceholderText("From YYYY-MM-DD")
        self.cdse_to.setPlaceholderText("To YYYY-MM-DD")
        self.cdse_cloud.setPlaceholderText("Max cloud % (e.g. 10)")
        cdse_row.addWidget(self.cdse_from)
        cdse_row.addWidget(self.cdse_to)
        cdse_row.addWidget(self.cdse_cloud)
        layout.addLayout(cdse_row)

        self.cdse_button = QPushButton("Check Sentinel-2 dates in CDSE")
        layout.addWidget(self.cdse_button)

        # --- Gamma Earth date field ---
        layout.addWidget(QLabel("Date (YYYY-MM-DD)"))
        self.date_input = QLineEdit()
        layout.addWidget(self.date_input)

        layout.addWidget(QLabel("Date range (days)"))
        self.date_range_input = QLineEdit(settings_default_range())
        layout.addWidget(self.date_range_input)

        # --- API key & product type ---
        settings = QSettings()
        self.api_key_input = QLineEdit(settings.value("GammaEarth/api_key", ""))
        self.api_key_input.setEchoMode(QLineEdit.Password)
        layout.addWidget(QLabel("API key"))
        layout.addWidget(self.api_key_input)

        # --- Credits ---
        self.credits_label = QLabel("Credits: not checked")
        self.credits_button = QPushButton("Check credits")
        credits_row = QHBoxLayout()
        credits_row.addWidget(self.credits_label, 1)
        credits_row.addWidget(self.credits_button)
        layout.addLayout(credits_row)

        self.jobs_table = QTableWidget(0, 4)
        self.jobs_table.setHorizontalHeaderLabels(
            ["Job ID", "Date", "Status", "Credits used"]
        )
        self.jobs_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.jobs_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.jobs_table.verticalHeader().setVisible(False)
        self.jobs_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.jobs_table.setMaximumHeight(140)
        self.jobs_table.setVisible(False)
        layout.addWidget(self.jobs_table)

        self.product_select = QComboBox()
        self.product_select.addItems(["MS", "TCI", "NDVI", "IRP"])
        layout.addWidget(QLabel("Product type"))
        layout.addWidget(self.product_select)

        # --- Output folder ---
        layout.addWidget(QLabel("Output folder"))
        self.folder_input = QLineEdit(settings.value("GammaEarth/last_output_folder", ""))
        self.folder_browse = QPushButton("Browse")
        self.folder_browse.clicked.connect(self.browse_folder)

        row = QHBoxLayout()
        row.addWidget(self.folder_input)
        row.addWidget(self.folder_browse)
        layout.addLayout(row)

        # --- Status log (scrollable, selectable) ---
        self.status_edit = QPlainTextEdit()
        self.status_edit.setReadOnly(True)
        self.status_edit.setMinimumHeight(100)
        self.status_edit.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        layout.addWidget(self.status_edit)

        # --- Progress bar ---
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        # --- Gamma Earth branding (small marketing text) ---
        self.brand_label = QLabel(
            "<small><i>Gamma Earth S2DR3 – Super-resolution for Sentinel-2. "
            "Requires a Gamma Earth API key and Data Allowance. Learn more at https://gamma.earth</i></small>"
        )
        layout.addWidget(self.brand_label)

        # --- Buttons ---
        self.submit_button = QPushButton("Submit")
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.close)
        layout.addWidget(self.submit_button)
        layout.addWidget(self.close_button)

        self.setLayout(layout)

        # AOI mode visibility
        self.aoi_group.buttonClicked.connect(self.update_visibility)
        self.update_visibility()

    # -----------------------
    def update_visibility(self):
        mode = self.aoi_group.checkedId()

        # Hide all AOI-specific widgets by default
        self.selection_info.setVisible(False)
        self.center_lon_label.setVisible(False)
        self.center_lon.setVisible(False)
        self.center_lat_label.setVisible(False)
        self.center_lat.setVisible(False)
        self.side_km_label.setVisible(False)
        self.side_km.setVisible(False)
        self.geojson_path.setVisible(False)
        self.geojson_browse.setVisible(False)

        if mode == 0:  # selected polygon
            self.selection_info.setVisible(True)
        elif mode == 1:  # square
            self.center_lon_label.setVisible(True)
            self.center_lon.setVisible(True)
            self.center_lat_label.setVisible(True)
            self.center_lat.setVisible(True)
            self.side_km_label.setVisible(True)
            self.side_km.setVisible(True)
        elif mode == 2:  # geojson
            self.geojson_path.setVisible(True)
            self.geojson_browse.setVisible(True)

    # -----------------------
    def browse_geojson(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select GeoJSON", "", "GeoJSON (*.geojson *.json)"
        )
        if path:
            self.geojson_path.setText(path)

    def browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Output Folder")
        if folder:
            self.folder_input.setText(folder)

    def show_credits(self, data):
        credits = data.get("credits", {})
        self.credits_label.setText(
            f"<b>Credits: {credits.get('total_balance', '?')}</b> "
            f"(purchased {credits.get('purchased_balance', '?')}, "
            f"bonus {credits.get('bonus_balance', '?')})"
        )
        jobs = data.get("jobs", [])
        self.jobs_table.setRowCount(len(jobs))
        for row, job in enumerate(jobs):
            status = job.get("credits_status", "")
            used = job.get("credits_used", 0)
            if status == "PENDING":
                used_text = f"{job.get('credits_requested', '?')} (reserved)"
            else:
                used_text = str(used)
            values = [
                job.get("job_id", ""),
                (job.get("reserved_at") or "")[:10],
                status.capitalize(),
                used_text,
            ]
            for col, value in enumerate(values):
                self.jobs_table.setItem(row, col, QTableWidgetItem(value))
        self.jobs_table.setVisible(bool(jobs))

    def set_progress(self, v: int):
        self.progress.setVisible(True)
        self.progress.setValue(v)

    def set_status(self, msg: str, error: bool = False, clear: bool = False):
        """
        Append a status line (or replace all if clear=True).
        Text is selectable and scrollable in the QPlainTextEdit.
        """
        prefix = "[ERROR] " if error else ""
        line = prefix + msg

        if clear:
            self.status_edit.setPlainText(line)
        else:
            current = self.status_edit.toPlainText()
            if current:
                self.status_edit.appendPlainText(line)
            else:
                self.status_edit.setPlainText(line)

    def get_data(self):
        return {
            "aoi_mode": self.aoi_group.checkedId(),
            "center_lon": self.center_lon.text(),
            "center_lat": self.center_lat.text(),
            "side_km": self.side_km.text(),
            "geojson": self.geojson_path.text(),
            "date": self.date_input.text(),
            "date_range": self.date_range_input.text(),
            "api_key": self.api_key_input.text(),
            "product": self.product_select.currentText(),
            "output_folder": self.folder_input.text(),
            "cdse_from": self.cdse_from.text(),
            "cdse_to": self.cdse_to.text(),
            "cdse_cloud": self.cdse_cloud.text(),
        }


# ============================================================
# Plugin class
# ============================================================
class GammaEarthPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.output_folder = ""
        self.poll_thread = None
        self.job_id = None
        self.credits_workers = set()
        self.poll_worker = None
        self.download_thread = None
        self.download_worker = None

    def initGui(self):
        icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
        print("[GammaEarth] Icon path:", icon_path, "exists:", os.path.exists(icon_path))

        self.action = QAction(QIcon(icon_path), "Gamma Earth", self.iface.mainWindow())
        self.action.triggered.connect(self.run)

        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("&Gamma Earth", self.action)

    def unload(self):
        if self.action:
            self.iface.removeToolBarIcon(self.action)
            self.iface.removePluginMenu("&Gamma Earth", self.action)

    def run(self):
        self.dialog = AOIDialog()

        # Submit job
        self.dialog.submit_button.clicked.connect(self.run_from_dialog)

        # CDSE helper
        self.dialog.cdse_button.clicked.connect(self.check_cdse_dates)

        # Credits
        self.dialog.credits_button.clicked.connect(self.refresh_credits)

        # AOI feedback for method 1
        self.update_selection_feedback()
        self.dialog.aoi_group.buttonClicked.connect(
            lambda _: self.update_selection_feedback()
        )

        self.dialog.show()

    # ========================================================
    def run_from_dialog(self):
        d = self.dialog.get_data()
        print(f"[GammaEarth] run_from_dialog() AOI mode: {d['aoi_mode']}")

        api_key = d["api_key"].strip()
        if not api_key or "/" in api_key:
            self.dialog.set_status("Invalid API key", True, clear=True)
            return

        date_range = d["date_range"].strip()
        if not date_range.isdigit() or int(date_range) < 1:
            self.dialog.set_status(
                "Date range must be a positive number of days", True, clear=True
            )
            return

        QSettings().setValue("GammaEarth/api_key", api_key)
        QSettings().setValue("GammaEarth/date_range", date_range)
        QSettings().setValue("GammaEarth/last_output_folder", d["output_folder"])
        self.output_folder = d["output_folder"]

        aoi = self.build_aoi(d)
        if not aoi:
            return

        self.dialog.set_status("Submitting Gamma Earth job…", error=False, clear=False)
        self.dialog.submit_button.setEnabled(False)
        self.submit_job(api_key, aoi, d["date"], date_range, d["product"])

    # ========================================================
    def update_selection_feedback(self):
        """
        Live feedback for method 1 (selected polygon).
        """
        if not hasattr(self, "dialog"):
            return

        mode = self.dialog.aoi_group.checkedId()

        if mode != 0:
            self.dialog.selection_info.clear()
            self.dialog.selection_info.setVisible(False)
            return

        layer = self.iface.activeLayer()
        if not isinstance(layer, QgsVectorLayer):
            msg = "Active layer is not a polygon vector layer."
            self.dialog.selection_info.setText(
                f"<i style='color:#c62828;'>{msg}</i>"
            )
            self.dialog.selection_info.setVisible(True)
            return

        feats = layer.selectedFeatures()
        if not feats:
            msg = "No polygon selected in active layer."
            self.dialog.selection_info.setText(
                f"<i style='color:#c62828;'>{msg}</i>"
            )
            self.dialog.selection_info.setVisible(True)
            return

        feature = feats[0]
        geom = feature.geometry()
        bbox = geom.boundingBox()

        layer_name = layer.name()
        feature_id = feature.id()

        fields = feature.fields()
        attr_name = None
        attr_val = None
        preferred_names = [
            "AOI_name", "aoi_name", "name", "Name", "NAME",
            "id", "ID", "label", "Label"
        ]
        for fname in preferred_names:
            if fname in fields.names():
                attr_name = fname
                attr_val = feature[fname]
                break

        if attr_name is None and len(fields) > 0:
            first_field = fields[0]
            attr_name = first_field.name()
            attr_val = feature[attr_name]

        info_parts = [f"Layer: {layer_name}", f"Feature ID: {feature_id}"]
        if attr_name is not None:
            info_parts.append(f"{attr_name}={attr_val}")
        info_parts.append(
            "AOI bbox: "
            f"{bbox.xMinimum():.5f}, {bbox.yMinimum():.5f}, "
            f"{bbox.xMaximum():.5f}, {bbox.yMaximum():.5f}"
        )
        info_text = " | ".join(info_parts)

        self.dialog.selection_info.setText(
            f"<i style='color:#2e7d32;'>{info_text}</i>"
        )
        self.dialog.selection_info.setVisible(True)

    # ========================================================
    def build_aoi(self, d):
        mode = d["aoi_mode"]
        print(f"[GammaEarth] AOI mode selected: {mode}")

        # 1) Selected polygon
        if mode == 0:
            layer = self.iface.activeLayer()
            if not isinstance(layer, QgsVectorLayer):
                msg = "Active layer is not a vector layer. Please select a polygon layer first."
                self.dialog.set_status(msg, True, clear=True)
                QMessageBox.warning(self.iface.mainWindow(), "Gamma Earth", msg)
                return None

            feats = layer.selectedFeatures()
            if not feats:
                msg = "No feature selected. Please select a polygon in the active layer."
                self.dialog.set_status(msg, True, clear=True)
                QMessageBox.warning(self.iface.mainWindow(), "Gamma Earth", msg)
                return None

            feature = feats[0]
            geom = feature.geometry()
            bbox = geom.boundingBox()

            layer_name = layer.name()
            feature_id = feature.id()

            fields = feature.fields()
            attr_name = None
            attr_val = None
            preferred_names = [
                "AOI_name", "aoi_name", "name", "Name", "NAME",
                "id", "ID", "label", "Label"
            ]
            for fname in preferred_names:
                if fname in fields.names():
                    attr_name = fname
                    attr_val = feature[fname]
                    break

            if attr_name is None and len(fields) > 0:
                first_field = fields[0]
                attr_name = first_field.name()
                attr_val = feature[attr_name]

            info_parts = [f"Layer: {layer_name}", f"Feature ID: {feature_id}"]
            if attr_name is not None:
                info_parts.append(f"{attr_name}={attr_val}")
            info_parts.append(
                "AOI bbox: "
                f"{bbox.xMinimum():.5f}, {bbox.yMinimum():.5f}, "
                f"{bbox.xMaximum():.5f}, {bbox.yMaximum():.5f}"
            )
            info_text = " | ".join(info_parts)
            print(f"[GammaEarth] Selected AOI info: {info_text}")

            self.dialog.selection_info.setText(
                f"<i style='color:#2e7d32;'>{info_text}</i>"
            )
            self.dialog.selection_info.setVisible(True)

            self.dialog.set_status(
                "AOI: selected polygon from layer -> " + info_text,
                error=False,
                clear=True,
            )

            return (
                f"{bbox.xMinimum()} {bbox.yMinimum()} "
                f"{bbox.xMaximum()} {bbox.yMaximum()}"
            )

        # 2) Square: center lon/lat + side length (km)
        if mode == 1:
            try:
                lon = float(d["center_lon"])
                lat = float(d["center_lat"])
                side_km = float(d["side_km"])
            except ValueError:
                self.dialog.set_status(
                    "Invalid center lon/lat or side length", True, clear=True
                )
                return None

            if side_km <= 0:
                self.dialog.set_status(
                    "Side length must be > 0", True, clear=True
                )
                return None

            half_km = side_km / 2.0

            lat_deg_per_km = 1.0 / 111.0
            delta_lat = half_km * lat_deg_per_km

            lat_rad = math.radians(lat)
            cos_lat = math.cos(lat_rad)
            if abs(cos_lat) < 1e-6:
                self.dialog.set_status(
                    "Cannot compute square near the poles", True, clear=True
                )
                return None

            lon_deg_per_km = 1.0 / (111.0 * cos_lat)
            delta_lon = half_km * lon_deg_per_km

            minx = lon - delta_lon
            maxx = lon + delta_lon
            miny = lat - delta_lat
            maxy = lat + delta_lat

            info_text = (
                f"AOI: {side_km}x{side_km} km square "
                f"centered at ({lon}, {lat}) | "
                f"AOI bbox: {minx:.5f}, {miny:.5f}, {maxx:.5f}, {maxy:.5f}"
            )
            print(f"[GammaEarth] {info_text}")
            self.dialog.set_status(info_text, error=False, clear=True)

            return f"{minx} {miny} {maxx} {maxy}"

        # 3) GeoJSON file
        if mode == 2:
            path = d["geojson"]
            if not os.path.exists(path):
                self.dialog.set_status("GeoJSON not found", True, clear=True)
                return None
            layer = QgsVectorLayer(path, "aoi", "ogr")
            if not layer.isValid():
                self.dialog.set_status("Invalid GeoJSON", True, clear=True)
                return None
            extent = layer.extent()
            info_text = (
                f"AOI: GeoJSON file '{os.path.basename(path)}' | "
                f"extent bbox: "
                f"{extent.xMinimum():.5f}, {extent.yMinimum():.5f}, "
                f"{extent.xMaximum():.5f}, {extent.yMaximum():.5f}"
            )
            print(f"[GammaEarth] {info_text}")
            self.dialog.set_status(info_text, error=False, clear=True)

            return (
                f"{extent.xMinimum()} {extent.yMinimum()} "
                f"{extent.xMaximum()} {extent.yMaximum()}"
            )

        return None

    # ========================================================
    def submit_job(self, api_key, aoi, date, date_range, product):
        url = f"{API_BASE_URL}/{api_key}"
        payload = {"date": date, "date_range": date_range, "aoi": aoi}

        try:
            r = requests.post(url, json=payload, timeout=60)
            print(f"[GammaEarth] Submit response: {r.status_code} — {r.text}")
            if r.status_code != 200:
                self.dialog.set_status(
                    f"Server error ({r.status_code}): {r.text}", True
                )
                self.dialog.submit_button.setEnabled(True)
                return

            data = r.json()
            print("[GammaEarth] Response JSON keys:", list(data.keys()))

            if product in ("NDVI", "IRP"):
                self.dialog.set_status(
                    f"{product} not yet available as direct GeoTIFF; downloading TCI instead.",
                    error=False
                )
                product = "TCI"
            elif product not in ("MS", "TCI"):
                self.dialog.set_status(f"Unknown product type: {product}", True)
                self.dialog.submit_button.setEnabled(True)
                return

            tif_path = self.resolve_output_path(data, product)
            if not tif_path:
                self.dialog.set_status(
                    f"No output path for product '{product}' in server response", True
                )
                self.dialog.submit_button.setEnabled(True)
                return

            job_id = data.get("job_id")
            self.job_id = job_id
            if job_id:
                self.dialog.set_status(f"Job submitted (job_id: {job_id})")
            if "requested_credits" in data:
                balance = data.get("remaining_total_balance")
                left = f", {balance} credits left" if balance is not None else ""
                self.dialog.set_status(
                    f"Reserved {data['requested_credits']} credits{left}"
                )
                self.refresh_credits()

            download_url = storage_url(tif_path)
            self.start_polling(download_url)

        except Exception as e:
            print(f"[GammaEarth] Exception in submit_job: {e}")
            self.dialog.set_status(
                f"Error submitting job: {redact(str(e), api_key)}", True
            )
            self.dialog.submit_button.setEnabled(True)

    # ========================================================
    # Credits
    # ========================================================
    def refresh_credits(self, *_, announce_job=False):
        """Fetch balance + per-job credits for the key in the dialog."""
        api_key = self.dialog.api_key_input.text().strip()
        if not api_key or "/" in api_key:
            self.dialog.set_status("Enter a valid API key to check credits", True)
            return

        self.dialog.credits_button.setEnabled(False)
        thread = QThread()
        worker = CreditsWorker(api_key)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.result.connect(
            lambda data: self.on_credits(data, announce_job)
        )
        worker.error.connect(self.on_credits_error)
        for signal in (worker.result, worker.error):
            signal.connect(thread.quit)
        thread.finished.connect(lambda: self.credits_workers.discard((thread, worker)))
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(
            lambda: self.dialog.credits_button.setEnabled(True)
        )
        self.credits_workers.add((thread, worker))
        thread.start()

    def on_credits(self, data, announce_job):
        self.dialog.show_credits(data)
        if announce_job and self.job_id:
            for job in data.get("jobs", []):
                if job.get("job_id") == self.job_id:
                    if job.get("credits_status") == "COMMITTED":
                        msg = f"Job used {job.get('credits_used')} credits"
                    elif job.get("credits_status") == "PENDING":
                        msg = (
                            f"{job.get('credits_requested')} credits reserved; "
                            "final cost is confirmed when the job completes"
                        )
                    else:
                        msg = "No credits charged for this job (released)"
                    self.dialog.set_status(msg)
                    break

    def on_credits_error(self, msg):
        self.dialog.set_status(f"Could not read credits: {msg}", True)

    @staticmethod
    def resolve_output_path(data, product):
        """
        Find the output GeoTIFF path for `product` in the job response.
        The API returns e.g. "save_path_S3_TCI"; if the requested product has
        no key of its own, derive it from another product's path by swapping
        the "_<PRODUCT>.tif" filename suffix.
        """
        for key in (f"save_path_S3_{product}", f"save_path_{product}"):
            if data.get(key):
                return data[key]
        for key, value in data.items():
            if key.startswith("save_path") and isinstance(value, str):
                for other in ("TCI", "MS"):
                    suffix = f"_{other}.tif"
                    if value.endswith(suffix):
                        return value[: -len(suffix)] + f"_{product}.tif"
        return None

    # ========================================================
    def start_polling(self, url):
        self.dialog.set_status("Waiting for product to be available…")
        self.poll_thread = QThread()
        self.poll_worker = PollWorker(url)

        self.poll_worker.moveToThread(self.poll_thread)
        self.poll_thread.started.connect(self.poll_worker.run)

        self.poll_worker.ready.connect(self.on_poll_ready)
        self.poll_worker.timeout.connect(self.on_poll_timeout)
        self.poll_worker.error.connect(self.on_poll_error)

        self.poll_worker.ready.connect(self.poll_thread.quit)
        self.poll_worker.timeout.connect(self.poll_thread.quit)
        self.poll_worker.error.connect(self.poll_thread.quit)

        self.poll_worker.ready.connect(self.poll_worker.deleteLater)
        self.poll_worker.timeout.connect(self.poll_worker.deleteLater)
        self.poll_worker.error.connect(self.poll_worker.deleteLater)

        self.poll_thread.finished.connect(self.poll_thread.deleteLater)

        self.poll_thread.start()

    def on_poll_ready(self, url):
        self.dialog.set_status("Product found. Downloading…")
        self.download(url)

    def on_poll_timeout(self, url):
        job = f" (job_id: {self.job_id})" if getattr(self, "job_id", None) else ""
        self.dialog.set_status(
            f"File not ready after 5 minutes{job}. Manual download:\n{url}", True
        )
        self.dialog.submit_button.setEnabled(True)

    def on_poll_error(self, msg):
        self.dialog.set_status(f"Polling error: {msg}", True)
        self.dialog.submit_button.setEnabled(True)

    # ========================================================
    def download(self, url):
        fname = os.path.basename(url)
        folder = self.output_folder or tempfile.gettempdir()
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, fname)

        self.download_thread = QThread()
        self.download_worker = DownloadWorker(url, path)
        self.download_worker.moveToThread(self.download_thread)

        self.download_thread.started.connect(self.download_worker.run)
        self.download_worker.progress.connect(self.dialog.set_progress)
        self.download_worker.finished.connect(self.on_download_done)
        self.download_worker.error.connect(self.on_download_error)

        self.download_worker.finished.connect(self.download_thread.quit)
        self.download_worker.error.connect(self.download_thread.quit)
        self.download_worker.finished.connect(self.download_worker.deleteLater)
        self.download_worker.error.connect(self.download_worker.deleteLater)
        self.download_thread.finished.connect(self.download_thread.deleteLater)

        self.download_thread.start()

    def on_download_done(self, path):
        if not os.path.exists(path):
            self.dialog.set_status("Download failed: file missing", True)
            self.dialog.submit_button.setEnabled(True)
            return

        raster = QgsRasterLayer(path, os.path.basename(path))
        if raster.isValid():
            QgsProject.instance().addMapLayer(raster)
            self.dialog.set_status("Raster loaded")
        else:
            self.dialog.set_status("Failed to load raster", True)

        self.dialog.submit_button.setEnabled(True)
        self.refresh_credits(announce_job=True)

    def on_download_error(self, err):
        self.dialog.set_status(f"Download error: {err}", True)
        self.dialog.submit_button.setEnabled(True)

     # ========================================================
    # Copernicus CDSE helper
    # ========================================================
    def check_cdse_dates(self):
        """
        Query CDSE STAC for Sentinel-2 L2A scenes for the current AOI and filters.
        No explicit 'limit' is set; the service applies its own default cap.
        Results in the status box clearly state which max cloud threshold was used.
        """
        if not hasattr(self, "dialog"):
            return

        d = self.dialog.get_data()

        # Clear previous CDSE results but keep AOI feedback in the label
        self.dialog.set_status("Preparing CDSE query…", error=False, clear=True)

        # Use SAME AOI logic as for Gamma Earth, but we only need the bbox string
        aoi = self.build_aoi(d)
        if not aoi:
            # build_aoi already wrote a meaningful error into the status box
            return

        try:
            minx, miny, maxx, maxy = map(float, aoi.split())
        except Exception:
            self.dialog.set_status(
                "Could not parse AOI for CDSE search (bbox parsing failed).",
                True,
                clear=True,
            )
            return

        from_date = d["cdse_from"].strip()
        to_date = d["cdse_to"].strip()
        cloud_str = d["cdse_cloud"].strip()

        max_cloud = None
        if cloud_str:
            try:
                max_cloud = float(cloud_str)
            except ValueError:
                self.dialog.set_status(
                    f"Invalid max cloud percentage: '{cloud_str}'",
                    True,
                    clear=True,
                )
                return

        url = "https://stac.dataspace.copernicus.eu/v1/search"
        params = {
            "collections": "sentinel-2-l2a",
            "bbox": f"{minx},{miny},{maxx},{maxy}",
            "limit": 1000,  # ask CDSE for up to 1000 scenes instead of the default 10
        }

        # Date range filter
        if from_date or to_date:
            start = from_date if from_date else ".."
            end = to_date if to_date else ".."
            params["datetime"] = f"{start}T00:00:00Z/{end}T23:59:59Z"

        # Cloud filter (CQL2-text)
        if max_cloud is not None:
            params["filter-lang"] = "cql2-text"
            params["filter"] = f"eo:cloud_cover<={max_cloud}"

        # Log what we are about to query
        debug_line = (
            f"CDSE query: bbox={params['bbox']}, "
            f"datetime={params.get('datetime', 'ANY')}, "
            f"max_cloud={max_cloud if max_cloud is not None else 'ANY'}"
        )
        print(f"[GammaEarth] {debug_line}")
        self.dialog.set_status(debug_line, error=False, clear=True)

        try:
            resp = requests.get(url, params=params, timeout=30)
            print(f"[GammaEarth] CDSE STAC URL: {resp.url}")

            if resp.status_code != 200:
                self.dialog.set_status(
                    f"CDSE STAC error: HTTP {resp.status_code}",
                    True,
                    clear=False,
                )
                return

            data = resp.json()
            features = data.get("features", [])
            if not features:
                self.dialog.set_status(
                    "CDSE: no Sentinel-2 L2A scenes found for these filters.",
                    False,
                    clear=False,
                )
                return

            # Sort results by datetime (ascending)
            try:
                features.sort(
                    key=lambda f: f.get("properties", {}).get("datetime", "")
                )
            except Exception:
                pass

            # Build a compact table of results and show count + max cloud used
            header = (
                f"CDSE Sentinel-2 L2A scenes found: {len(features)} "
                f"(max_cloud={max_cloud if max_cloud is not None else 'ANY'}%)"
            )
            lines = [header, "date        | cloud% | id"]

            for feat in features:
                props = feat.get("properties", {})
                dt = props.get("datetime", "")[:10]
                cc = props.get("eo:cloud_cover", None)
                if isinstance(cc, (int, float)):
                    cc_str = f"{cc:.1f}"
                else:
                    cc_str = str(cc)
                feat_id = feat.get("id", "")
                lines.append(f"{dt} | {cc_str:>6} | {feat_id}")

            # Now overwrite status area with just the CDSE summary
            self.dialog.set_status("\n".join(lines), error=False, clear=True)

        except Exception as e:
            self.dialog.set_status(
                f"CDSE STAC query failed: {e}", True, clear=False
            )

