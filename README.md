# Gamma Earth S2DR3 QGIS Plugin

QGIS plugin for 10x super-resolution of Sentinel-2 imagery using the
[Gamma Earth](https://gamma.earth) S2DR3 API. Define an area of interest,
pick an acquisition date, and the plugin submits the job, waits for the result,
downloads the GeoTIFF and loads it into your QGIS project.

## Requirements

- QGIS 3.0 or later
- A Gamma Earth **API key** with available Data Allowance (see https://gamma.earth)
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
3. Enter the **Date** (`YYYY-MM-DD`), the **Date range** (days before the date, default 7),
   the **Max cloud cover** (%, default 100), your **API key**, the **Products** to download
   (tick one or more of `MS`, `TCI`, `NDVI`, `IRP`) and an **Output folder**.
   Gamma Earth looks at the images taken between *date - date range* and 3 days after the date.
   With the default max cloud cover of 100 (no limit) it uses the **clearest** one over your AOI
   (equally clear: the closest to the date). With a lower max cloud cover it keeps only the images
   at or under that limit and uses the one **closest to the date**.
   If no image qualifies, the plugin shows why and no credits are used.
4. Click **Submit**. Progress is shown in the status log; each ticked product is
   downloaded to the output folder and added to the project. All products of a job
   share one cost. `NDVI` and `IRP` are not available as GeoTIFF yet and download as `TCI`.

The dialog has two tabs: **New job** and **Credits**. In **Credits**, click **Check credits** to see your balance (purchased and bonus) and a table of
recent jobs (job ID, PID, date, status and credits used). *Jobs shown* sets how many
are listed (up to 200). After submitting, the plugin reports how
many credits were reserved, and once the result is downloaded it reports the final
cost of the job. Credits reserved for a job that fails are returned to your balance.
Processing the same area and image date again with the same API key is free: no credits
are used, or they are returned when the repeat job completes.

The API key, date range, max cloud cover, products, jobs shown and output folder are remembered between sessions.

## Support

Report issues at https://github.com/Gamma-Earth/gamma-earth-plugin/issues
or contact info@gamma.earth.

For a development setup (symlinking the repo into QGIS) see
[DEVELOPMENT.md](DEVELOPMENT.md).
