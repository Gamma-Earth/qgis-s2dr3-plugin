# Changelog

## [1.1.0] - 2026-10-21

- First release on the official QGIS plugin repository.
- The API key is now stored in the QGIS Authentication Manager (QGIS settings are used only if it is unavailable). A key saved by an earlier version is migrated automatically.
- The dialog explains what data is sent to which service, with a link to the privacy policy and terms.
- Clear messages when the API key is missing or invalid, or when credits are exhausted.
- Uses `qgis.PyQt`; minimum QGIS version is now 3.34.
- Removed the `GAMMA_EARTH_API_URL` environment variable and `GammaEarth/api_url` setting, and all debug output.
- Max cloud cover field: no limit (default 100) uses the clearest image in the date range, a lower value uses the closest image under that limit.
- Several products can be ticked and are downloaded from the same job.
- New "New job" and "Credits" tabs; the Credits tab lists your latest jobs with their PID (number of jobs is configurable).
- Job submission and the Copernicus date search no longer freeze QGIS.
- Fix: Copernicus date search failed with HTTP 400.
- The plugin identifies itself to the API (qgis-plugin/1.1).
