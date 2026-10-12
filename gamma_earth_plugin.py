# --- Gamma Earth S2DR3 QGIS Plugin (3 AOI Modes + CDSE Helper) ---

from PyQt5.QtWidgets import (
    QAction, QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QCheckBox, QSpinBox,
    QProgressBar, QFileDialog, QRadioButton, QButtonGroup, QMessageBox, QPlainTextEdit,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QTabWidget, QScrollArea, QGroupBox, QWidget, QFrame
)
from PyQt5.QtGui import QIcon
from PyQt5.QtCore import QObject, QThread, pyqtSignal, QSettings, Qt
from qgis.core import (
    QgsProject, QgsRasterLayer, QgsVectorLayer
)
import requests
import os
import tempfile
import time
import math


DEFAULT_API_BASE_URL = "https://api.gamma.earth/jobs"


def api_base_url():
    """Production URL unless overridden (e.g. for testing) by the
    GAMMA_EARTH_API_URL environment variable or the QGIS setting
    GammaEarth/api_url."""
    url = os.environ.get("GAMMA_EARTH_API_URL") or QSettings().value("GammaEarth/api_url", "")
    return (url or DEFAULT_API_BASE_URL).rstrip("/")


def plugin_version():
    """The version in metadata.txt, e.g. '1.1' ('unknown' if it cannot be read)."""
    import configparser
    try:
        parser = configparser.ConfigParser()
        parser.read(os.path.join(os.path.dirname(os.path.abspath(__file__)), "metadata.txt"))
        return parser.get("general", "version").strip() or "unknown"
    except Exception:
        return "unknown"


def client_id():
    """How this client identifies itself to the Gamma Earth API, e.g. 'qgis-plugin/1.1'. It is
    stored with each job so plugin usage can be told apart from direct API usage."""
    return f"qgis-plugin/{plugin_version()}"


def api_headers():
    """Headers for requests to the Gamma Earth API (not for CDSE or the downloads)."""
    return {"User-Agent": f"GammaEarthQGIS/{plugin_version()}"}


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


def settings_default_max_clouds():
    return QSettings().value("GammaEarth/max_clouds", "100")


CDSE_LIMIT = 100


def cdse_error_detail(response):
    """The reason CDSE gives for a rejected request, e.g. ': Limit of ... exceeds ...'."""
    try:
        detail = response.json().get("detail")
        if isinstance(detail, dict):
            detail = detail.get("message")
        return f": {detail}" if detail else ""
    except (ValueError, AttributeError):
        return ""


PRODUCTS = ["MS", "TCI", "NDVI", "IRP"]
DEFAULT_JOBS_LIMIT = 50
MAX_JOBS_LIMIT = 200


def settings_default_products():
    """Products ticked last time (QSettings stores a single value as a plain string)."""
    saved = QSettings().value("GammaEarth/products", ["MS"])
    if isinstance(saved, str):
        saved = [saved]
    return [p for p in (saved or []) if p in PRODUCTS] or ["MS"]


def settings_default_jobs_limit():
    try:
        return max(1, min(int(QSettings().value("GammaEarth/jobs_limit", DEFAULT_JOBS_LIMIT)), MAX_JOBS_LIMIT))
    except (TypeError, ValueError):
        return DEFAULT_JOBS_LIMIT


def error_text(response):
    """The API's {"error": "..."} message, or the raw body."""
    try:
        return response.json().get("error") or response.text
    except ValueError:
        return response.text


def effective_products(selected):
    """Products to download for the ticked ones. NDVI and IRP are not available
    as GeoTIFFs yet, so they fall back to TCI (downloaded once even if TCI is
    also ticked). Returns (products, fallback_used)."""
    products, fallback = [], False
    for product in selected:
        if product in ("NDVI", "IRP"):
            fallback = True
            product = "TCI"
        if product not in products:
            products.append(product)
    return products, fallback


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
# Request Worker (any HTTP call in a background thread so QGIS never freezes)
# ============================================================
class RequestWorker(QObject):
    result = pyqtSignal(object)   # the requests.Response
    error = pyqtSignal(str)

    def __init__(self, method, url, **kwargs):
        super().__init__()
        self.method = method
        self.url = url
        self.kwargs = kwargs

    def run(self):
        try:
            self.result.emit(requests.request(self.method, self.url, **self.kwargs))
        except Exception as e:
            self.error.emit(str(e))


# ============================================================
# Credits Worker (GET <API_BASE_URL>/<api_key> in the background)
# ============================================================
class CreditsWorker(QObject):
    result = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, api_key, limit=DEFAULT_JOBS_LIMIT):
        super().__init__()
        self.api_key = api_key
        self.limit = limit

    def run(self):
        try:
            r = requests.get(
                f"{api_base_url()}/{self.api_key}",
                params={"limit": self.limit},
                headers=api_headers(),
                timeout=30,
            )
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
        self.setMinimumWidth(520)
        self.setMinimumHeight(420)
        # Open at a comfortable size but never taller/wider than the screen, so the
        # Submit button stays reachable and the window can always be resized.
        screen = QApplication.primaryScreen().availableGeometry()
        self.resize(min(820, int(screen.width() * 0.9)), min(780, int(screen.height() * 0.85)))

        root = QVBoxLayout()        # API key, tabs, status log, buttons
        layout = QVBoxLayout()      # "New job" tab
        credits_layout = QVBoxLayout()   # "Credits" tab

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

        self.cdse_button = QPushButton("Check Sentinel-2 dates in CDSE  \u25B6")
        self.cdse_button.setCursor(Qt.PointingHandCursor)
        self.cdse_button.setMinimumHeight(30)
        self.cdse_button.setStyleSheet(
            "QPushButton { background-color: #1565c0; color: white; font-weight: bold;"
            " border: 1px solid #0d47a1; border-radius: 4px; padding: 5px 12px; }"
            "QPushButton:hover { background-color: #1976d2; }"
            "QPushButton:pressed { background-color: #0d47a1; }"
            "QPushButton:disabled { background-color: #9e9e9e; border-color: #757575; }"
        )
        layout.addWidget(self.cdse_button)

        # --- Gamma Earth date field ---
        layout.addWidget(QLabel("Date (YYYY-MM-DD)"))
        self.date_input = QLineEdit()
        layout.addWidget(self.date_input)

        layout.addWidget(QLabel("Date range (days)"))
        self.date_range_input = QLineEdit(settings_default_range())
        self.date_range_input.setToolTip(
            "How many days before the date to look for an image. "
            "Images up to 3 days after the date are also considered. "
            "Without a cloud limit, the clearest image in that period is used."
        )
        layout.addWidget(self.date_range_input)

        layout.addWidget(QLabel("Max cloud cover (%)"))
        self.max_clouds_input = QLineEdit(settings_default_max_clouds())
        self.max_clouds_input.setToolTip(
            "100 = no limit: the clearest image in the date range is used. "
            "Below 100: only images with at most this cloud cover over the AOI are used, "
            "and the one closest to the date among them. "
            "If none qualifies, no job is created and no credits are used."
        )
        layout.addWidget(self.max_clouds_input)

        # --- API key & product type ---
        settings = QSettings()
        self.api_key_input = QLineEdit(settings.value("GammaEarth/api_key", ""))
        self.api_key_input.setEchoMode(QLineEdit.Password)
        root.addWidget(QLabel("API key"))
        root.addWidget(self.api_key_input)

        # --- Credits ---
        self.credits_label = QLabel("Credits: not checked")
        self.credits_button = QPushButton("Check credits")
        credits_row = QHBoxLayout()
        credits_row.addWidget(self.credits_label, 1)
        credits_row.addWidget(QLabel("Jobs shown"))
        self.jobs_limit_input = QSpinBox()
        self.jobs_limit_input.setRange(1, MAX_JOBS_LIMIT)
        self.jobs_limit_input.setValue(settings_default_jobs_limit())
        self.jobs_limit_input.setToolTip("How many of your latest jobs to list (1-%d)." % MAX_JOBS_LIMIT)
        credits_row.addWidget(self.jobs_limit_input)
        credits_row.addWidget(self.credits_button)
        credits_layout.addLayout(credits_row)

        self.jobs_table = QTableWidget(0, 5)
        self.jobs_table.setHorizontalHeaderLabels(
            ["Job ID", "PID", "Date", "Status", "Credits used"]
        )
        self.jobs_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.jobs_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.jobs_table.verticalHeader().setVisible(False)
        self.jobs_table.setTextElideMode(Qt.ElideNone)
        self.jobs_table.setAlternatingRowColors(True)
        header = self.jobs_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setStretchLastSection(True)
        self.jobs_table.setVisible(False)
        credits_layout.addWidget(self.jobs_table, 1)

        # Shown until the first "Check credits" returns jobs.
        self.jobs_hint = QLabel(
            "<i>Enter your API key and click <b>Check credits</b> to list your latest jobs.</i>"
        )
        self.jobs_hint.setWordWrap(True)
        self.jobs_hint.setAlignment(Qt.AlignTop)
        credits_layout.addWidget(self.jobs_hint)

        # --- Products (one or more) ---
        products_box = QGroupBox("Products")
        self.product_checks = {}
        products_row = QHBoxLayout()
        products_box.setLayout(products_row)
        selected = settings_default_products()
        for product in PRODUCTS:
            box = QCheckBox(product)
            box.setChecked(product in selected)
            self.product_checks[product] = box
            products_row.addWidget(box)
        products_row.addStretch(1)
        layout.addWidget(products_box)

        # --- Output folder ---
        layout.addWidget(QLabel("Output folder"))
        self.folder_input = QLineEdit(settings.value("GammaEarth/last_output_folder", ""))
        self.folder_browse = QPushButton("Browse")
        self.folder_browse.clicked.connect(self.browse_folder)

        row = QHBoxLayout()
        row.addWidget(self.folder_input)
        row.addWidget(self.folder_browse)
        layout.addLayout(row)

        layout.addStretch(1)

        # --- Tabs: the form scrolls on small screens; credits get a roomy tab ---
        form_widget = QWidget()
        form_widget.setLayout(layout)
        form_scroll = QScrollArea()
        form_scroll.setWidgetResizable(True)
        form_scroll.setFrameShape(QFrame.NoFrame)
        form_scroll.setWidget(form_widget)

        credits_widget = QWidget()
        credits_widget.setLayout(credits_layout)

        self.tabs = QTabWidget()
        self.tabs.addTab(form_scroll, "New job")
        self.tabs.addTab(credits_widget, "Credits")
        root.addWidget(self.tabs, 1)

        # --- Status log (scrollable, selectable) ---
        self.status_edit = QPlainTextEdit()
        self.status_edit.setReadOnly(True)
        self.status_edit.setMinimumHeight(70)
        self.status_edit.setMaximumHeight(110)
        self.status_edit.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        root.addWidget(self.status_edit)

        # --- Progress bar ---
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        root.addWidget(self.progress)

        # --- Gamma Earth branding (small marketing text) ---
        self.brand_label = QLabel(
            "<small><i>Gamma Earth S2DR3 – Super-resolution for Sentinel-2. "
            "Requires a Gamma Earth API key and Data Allowance. Learn more at https://gamma.earth</i></small>"
        )
        root.addWidget(self.brand_label)

        # --- Buttons ---
        self.submit_button = QPushButton("Submit")
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.close)
        root.addWidget(self.submit_button)
        root.addWidget(self.close_button)

        self.setLayout(root)

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
            if status == "RELEASED" and job.get("release_reason") == "PID_ALREADY_PROCESSED":
                used_text = "0 (already processed)"
            values = [
                job.get("job_id", ""),
                job.get("pid") or "",
                (job.get("reserved_at") or "")[:10],
                status.capitalize(),
                used_text,
            ]
            for col, value in enumerate(values):
                self.jobs_table.setItem(row, col, QTableWidgetItem(value))
        self.jobs_table.setVisible(bool(jobs))
        self.jobs_hint.setText("<i>No jobs yet for this API key.</i>")
        self.jobs_hint.setVisible(not jobs)

    def set_progress(self, v: int):
        self.progress.setRange(0, 100)
        self.progress.setVisible(True)
        self.progress.setValue(v)

    def set_busy(self, message=None):
        """Show an animated 'working' bar and lock the CDSE button while a request is
        in flight; set_busy(None) restores it. The Submit button is managed by the
        submit flow itself (it stays locked until the downloads finish)."""
        busy = message is not None
        if busy:
            self.set_status(message)
            self.progress.setRange(0, 0)          # indeterminate / animated
            self.progress.setVisible(True)
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
            self.progress.setVisible(False)
        self.cdse_button.setEnabled(not busy)

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
            "max_clouds": self.max_clouds_input.text(),
            "api_key": self.api_key_input.text(),
            "products": [p for p, box in self.product_checks.items() if box.isChecked()],
            "jobs_limit": self.jobs_limit_input.value(),
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
        self.request_workers = set()
        self.poll_worker = None
        self.download_thread = None
        self.download_worker = None
        self.pending_urls = []

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

        max_clouds = d["max_clouds"].strip()
        try:
            max_clouds_value = float(max_clouds)
        except ValueError:
            max_clouds_value = -1
        if not 0 <= max_clouds_value <= 100:
            self.dialog.set_status(
                "Max cloud cover must be a number between 0 and 100", True, clear=True
            )
            return

        if not d["products"]:
            self.dialog.set_status("Tick at least one product", True, clear=True)
            return

        QSettings().setValue("GammaEarth/api_key", api_key)
        QSettings().setValue("GammaEarth/date_range", date_range)
        QSettings().setValue("GammaEarth/max_clouds", max_clouds)
        QSettings().setValue("GammaEarth/products", d["products"])
        QSettings().setValue("GammaEarth/last_output_folder", d["output_folder"])
        self.output_folder = d["output_folder"]

        aoi = self.build_aoi(d)
        if not aoi:
            return

        self.dialog.submit_button.setEnabled(False)
        self.submit_job(api_key, aoi, d["date"], date_range, max_clouds, d["products"])

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
    def submit_job(self, api_key, aoi, date, date_range, max_clouds, products):
        url = f"{api_base_url()}/{api_key}"
        payload = {
            "date": date,
            "date_range": date_range,
            "max_clouds": max_clouds,
            "aoi": aoi,
            "client": client_id(),
        }

        self.dialog.set_busy(
            "Submitting Gamma Earth job… selecting the image can take up to a minute"
        )
        self.run_request(
            "POST", url,
            lambda r: self.on_submit_response(api_key, products, r),
            lambda msg: self.on_submit_error(api_key, msg),
            json=payload, headers=api_headers(), timeout=90,
        )

    def on_submit_response(self, api_key, products, r):
        self.dialog.set_busy(None)
        try:
            print(f"[GammaEarth] Submit response: {r.status_code} — {r.text}")
            if r.status_code in (400, 401, 402, 404, 503):
                # 404: no image meets the date range and cloud limit; 400: invalid input;
                # 401: missing/unknown API key; 402: not enough credits; 503: credit
                # reservation failed. Nothing was reserved, so tell the user plainly.
                self.dialog.set_status(error_text(r), True)
                self.dialog.submit_button.setEnabled(True)
                return
            if r.status_code != 200:
                self.dialog.set_status(
                    f"Server error ({r.status_code}): {r.text}", True
                )
                self.dialog.submit_button.setEnabled(True)
                return

            data = r.json()
            print("[GammaEarth] Response JSON keys:", list(data.keys()))
            if data.get("error"):
                self.dialog.set_status(str(data["error"]), True)
                self.dialog.submit_button.setEnabled(True)
                return

            to_download, fallback = effective_products(products)
            if fallback:
                self.dialog.set_status(
                    "NDVI and IRP are not yet available as direct GeoTIFF; "
                    "downloading TCI instead.",
                    error=False,
                )

            urls = []
            for product in to_download:
                tif_path = self.resolve_output_path(data, product)
                if not tif_path:
                    self.dialog.set_status(
                        f"No output path for product '{product}' in server response", True
                    )
                    self.dialog.submit_button.setEnabled(True)
                    return
                urls.append(storage_url(tif_path))

            job_id = data.get("job_id")
            self.job_id = job_id
            if job_id:
                self.dialog.set_status(f"Job submitted (job_id: {job_id})")
            if data.get("date"):
                clouds = data.get("cloud_cover")
                cloud_text = f", {clouds}% cloud cover" if clouds is not None else ""
                self.dialog.set_status(f"Image date: {data['date']}{cloud_text}")
            if "requested_credits" in data:
                balance = data.get("remaining_total_balance")
                left = f", {balance} credits left" if balance is not None else ""
                self.dialog.set_status(
                    f"Reserved {data['requested_credits']} credits{left}"
                )
                self.refresh_credits()
            elif data.get("credits_charged") == 0:
                self.dialog.set_status(
                    data.get("credits_note") or "No credits charged for this job"
                )

            self.pending_urls = urls
            self.process_next_product()

        except Exception as e:
            print(f"[GammaEarth] Exception in submit_job: {e}")
            self.dialog.set_status(
                f"Error submitting job: {redact(str(e), api_key)}", True
            )
            self.dialog.submit_button.setEnabled(True)

    def on_submit_error(self, api_key, msg):
        self.dialog.set_busy(None)
        self.dialog.set_status(f"Error submitting job: {redact(msg, api_key)}", True)

    def run_request(self, method, url, on_result, on_error, **kwargs):
        """Run an HTTP request off the UI thread; callbacks run on the UI thread."""
        thread = QThread()
        worker = RequestWorker(method, url, **kwargs)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.result.connect(on_result)
        worker.error.connect(on_error)
        for signal in (worker.result, worker.error):
            signal.connect(thread.quit)
        thread.finished.connect(lambda: self.request_workers.discard((thread, worker)))
        thread.finished.connect(thread.deleteLater)
        self.request_workers.add((thread, worker))
        thread.start()

    # ========================================================
    # Credits
    # ========================================================
    def refresh_credits(self, *_, announce_job=False):
        """Fetch balance + per-job credits for the key in the dialog."""
        api_key = self.dialog.api_key_input.text().strip()
        if not api_key or "/" in api_key:
            self.dialog.set_status("Enter a valid API key to check credits", True)
            return

        limit = self.dialog.jobs_limit_input.value()
        QSettings().setValue("GammaEarth/jobs_limit", limit)
        self.dialog.credits_button.setEnabled(False)
        thread = QThread()
        worker = CreditsWorker(api_key, limit)
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
                    elif job.get("release_reason") == "PID_ALREADY_PROCESSED":
                        msg = (
                            "No credits charged: this area and date were already "
                            "processed with your API key"
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
    def process_next_product(self):
        """Poll and download the next ticked product, one at a time. When none
        are left the dialog is re-enabled and the credits table refreshed."""
        if not self.pending_urls:
            self.dialog.submit_button.setEnabled(True)
            self.refresh_credits(announce_job=True)
            return
        self.start_polling(self.pending_urls.pop(0))

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
        self.process_next_product()

    def on_poll_error(self, msg):
        self.dialog.set_status(f"Polling error: {msg}", True)
        self.process_next_product()

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
            self.process_next_product()
            return

        raster = QgsRasterLayer(path, os.path.basename(path))
        if raster.isValid():
            QgsProject.instance().addMapLayer(raster)
            self.dialog.set_status("Raster loaded")
        else:
            self.dialog.set_status("Failed to load raster", True)

        self.process_next_product()

    def on_download_error(self, err):
        self.dialog.set_status(f"Download error: {err}", True)
        self.process_next_product()

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
            "limit": CDSE_LIMIT,  # CDSE rejects limit > 100 for sentinel-2-l2a (HTTP 400)
        }

        # Date range filter
        if from_date or to_date:
            start = f"{from_date}T00:00:00Z" if from_date else ".."
            end = f"{to_date}T23:59:59Z" if to_date else ".."
            params["datetime"] = f"{start}/{end}"

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

        self.dialog.set_busy(debug_line + "\nSearching Copernicus CDSE…")
        self.run_request(
            "GET", url,
            lambda resp: self.on_cdse_response(max_cloud, resp),
            lambda msg: (
                self.dialog.set_busy(None),
                self.dialog.set_status(f"CDSE STAC query failed: {msg}", True, clear=False),
            ),
            params=params, timeout=30,
        )

    def on_cdse_response(self, max_cloud, resp):
        self.dialog.set_busy(None)
        try:
            print(f"[GammaEarth] CDSE STAC URL: {resp.url}")

            if resp.status_code != 200:
                self.dialog.set_status(
                    f"CDSE STAC error: HTTP {resp.status_code}{cdse_error_detail(resp)}",
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
            if len(features) >= CDSE_LIMIT:
                header += f" - first {CDSE_LIMIT} shown, narrow the dates to see all"
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

