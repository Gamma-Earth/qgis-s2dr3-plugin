# Gamma Earth S2DR3 QGIS Plugin

QGIS plugin for 10x super-resolution of Sentinel-2 imagery using the
[Gamma Earth](https://gamma.earth) S2DR3 API. Define an area of interest,
pick an acquisition date, and the plugin submits the job, waits for the result,
downloads the GeoTIFF and loads it into your QGIS project.

## Requirements

- QGIS 3.0 or later
- A Gamma Earth **User ID** with available Data Allowance (see https://gamma.earth)
- Internet access (the `requests` Python package is used; it ships with QGIS)

## Installation

1. Clone or download this repository.
2. Copy the folder into your QGIS plugins directory, named `gamma_earth_plugin`:
   - macOS: `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/`
   - Linux: `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`
   - Windows: `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\`
3. Restart QGIS and enable **S2DR3** in *Plugins > Manage and Install Plugins*.

## Usage

Open the plugin from the **Gamma Earth** toolbar icon or the *Plugins > Gamma Earth* menu.

1. **AOI input method** - choose one:
   - the selected polygon of the active layer,
   - a square defined by center longitude/latitude and side length (km),
   - a GeoJSON file.
2. **Find a date (optional)** - enter a date range and maximum cloud cover, then
   click *Check Sentinel-2 dates in CDSE* to list available acquisitions from the
   Copernicus Data Space STAC catalogue.
3. Enter the **Date** (`YYYY-MM-DD`), your **User ID**, the **Product type**
   (`MS`, `TCI`, `NDVI` or `IRP`) and an **Output folder**.
4. Click **Submit**. Progress is shown in the status log; the result is
   downloaded to the output folder and added to the project.

The last User ID and output folder are remembered between sessions.

## Support

Report issues at https://github.com/Gamma-Earth/gamma-earth-plugin/issues
or contact info@gamma.earth.
