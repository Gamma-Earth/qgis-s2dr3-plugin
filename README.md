# Gamma Earth S2DR3 - QGIS plugin

Gamma Earth S2DR3 gives you 10x super-resolution of Sentinel-2 imagery inside QGIS, using
the [Gamma Earth](https://gamma.earth) S2DR3 service. Define an area of interest, pick an
acquisition date, and the plugin submits the job, waits for the result, downloads the
GeoTIFF and loads it into your QGIS project.

## Requirements

- QGIS 3.34 or later
- **A Gamma Earth API key and paid credits.** The plugin is only a client for a paid
  service: every job uses credits, and it does not work without a key. Get a key and
  credits at <https://gamma.earth>.
- Internet access (the `requests` Python package is used; it ships with QGIS)

## Installation

From the QGIS plugin catalogue:

1. In QGIS open *Plugins > Manage and Install Plugins...*
2. Search for **Gamma Earth S2DR3** in the *All* tab and click *Install Plugin*.
3. Open the plugin from the **Gamma Earth** toolbar icon or the *Plugins > Gamma Earth* menu.

To install a zip manually (for example a pre-release from the
[GitHub releases](https://github.com/Gamma-Earth/qgis-s2dr3-plugin/releases)), use
*Plugins > Manage and Install Plugins... > Install from ZIP*.

## API key

Paste your key into the **API key** field the first time you use the plugin. It is stored in
the QGIS Authentication Manager (QGIS may ask you to set a master password), not in a
project file. If the Authentication Manager cannot be used, it is kept in your QGIS
profile settings instead. A key saved in plain text by plugin version 1.1 or earlier is
moved to the Authentication Manager automatically.

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

## Data sent to external services

The plugin sends data only when you click a button, and nothing else (no telemetry,
no analytics):

| Action | Sent to | Data |
|---|---|---|
| Submit a job | `api.gamma.earth` | your API key, the area of interest as GeoJSON, the date, date range and maximum cloud cover, and a client identifier (`qgis-plugin/<version>`) |
| Check credits | `api.gamma.earth` | your API key and the number of jobs to list |
| Check Sentinel-2 dates | `stac.dataspace.copernicus.eu` (Copernicus Data Space) | the bounding box of the area, the date range and the cloud limit; no key |
| Download results | the storage URL returned by the service | a plain download request |

Privacy policy and terms: <https://demo.gamma.earth/legal/index.html>

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).

## Support and issues

Report issues at <https://github.com/Gamma-Earth/qgis-s2dr3-plugin/issues>
or contact info@gamma.earth. See the [CHANGELOG](CHANGELOG.md) for release notes.

For development, packaging and releasing see [DEVELOPMENT.md](DEVELOPMENT.md).
