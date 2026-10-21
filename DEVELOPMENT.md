# Development, packaging and releasing

How to run this repository's code directly in QGIS (no copying), and how releases are built.

## 1. Make the repo discoverable by QGIS (symlink)

QGIS loads Python plugins from the `plugins` folder of your profile. Instead of copying
the repo there, create a symlink named `gamma_earth_plugin` that points to the `gamma_earth_plugin/`
folder inside your clone (the plugin code lives in that folder, not in the repo root).
Edits to the repo then show up in QGIS immediately.

The link name must be exactly `gamma_earth_plugin` (it has to match the Python package
name used by `__init__.py`).

### macOS

```bash
ln -s /path/to/qgis-s2dr3-plugin/gamma_earth_plugin \
  "$HOME/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/gamma_earth_plugin"
```

### Linux

```bash
ln -s /path/to/qgis-s2dr3-plugin/gamma_earth_plugin \
  "$HOME/.local/share/QGIS/QGIS3/profiles/default/python/plugins/gamma_earth_plugin"
```

### Windows (Command Prompt, as administrator or with Developer Mode on)

```bat
mklink /D "%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\gamma_earth_plugin" "C:\path\to\qgis-s2dr3-plugin\gamma_earth_plugin"
```

Replace `/path/to/qgis-s2dr3-plugin` with the folder you cloned. If you use a QGIS
profile other than `default`, replace `default` in the path with its name.

### Enable the plugin

1. Restart QGIS.
2. Open *Plugins > Manage and Install Plugins > Installed* and tick **Gamma Earth S2DR3**.
3. Open it from the **Gamma Earth** toolbar icon or the *Plugins > Gamma Earth* menu.

Tip: install the **Plugin Reloader** plugin to reload your changes without restarting QGIS.

To remove the link later, delete the symlink only (`rm <link>` on macOS/Linux,
`rmdir <link>` on Windows). Do not delete the folder it points to.

## 2. Build and validate a package locally

```bash
python -m venv .venv && .venv/bin/pip install qgis-plugin-ci
git commit ...   # qgis-plugin-ci builds from committed files
.venv/bin/qgis-plugin-ci package 1.1.0
python scripts/validate_package.py gamma_earth_plugin.1.1.0.zip \
  --changelog CHANGELOG.md --root-license LICENSE --expect-version 1.1.0
```

The zip is built from git (`git archive`), so untracked files, caches and `.DS_Store`
never get in. `scripts/validate_package.py` fails on forbidden files, an invalid layout or
metadata, a missing LICENSE, or a CHANGELOG/metadata version mismatch. CI runs the same
checks on every push and pull request.

## 3. Release

1. Update `version=` in `gamma_earth_plugin/metadata.txt` and add a
   `## [x.y.z] - YYYY-MM-DD` section to `CHANGELOG.md` (semantic version, three numbers).
2. Merge to `main`, then tag and push: `git tag vX.Y.Z && git push origin vX.Y.Z`.
3. The *Release* workflow builds and validates the zip and attaches it to a GitHub release.
4. Upload the zip at <https://plugins.qgis.org/plugins/add/> (first version), or enable the
   optional upload job in `.github/workflows/release.yml` (see the comments there) for
   later versions.
