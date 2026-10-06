# Development and staging setup

How to run this repository's code directly in QGIS (no copying).

## 1. Make the repo discoverable by QGIS (symlink)

QGIS loads Python plugins from the `plugins` folder of your profile. Instead of copying
the repo there, create a symlink named `gamma_earth_plugin` that points to your clone.
Edits to the repo then show up in QGIS immediately.

The link name must be exactly `gamma_earth_plugin` (it has to match the Python package
name used by `__init__.py`).

### macOS

```bash
ln -s /path/to/gamma-earth-plugin \
  "$HOME/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/gamma_earth_plugin"
```

### Linux

```bash
ln -s /path/to/gamma-earth-plugin \
  "$HOME/.local/share/QGIS/QGIS3/profiles/default/python/plugins/gamma_earth_plugin"
```

### Windows (Command Prompt, as administrator or with Developer Mode on)

```bat
mklink /D "%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\gamma_earth_plugin" "C:\path\to\gamma-earth-plugin"
```

Replace `/path/to/gamma-earth-plugin` with the folder you cloned. If you use a QGIS
profile other than `default`, replace `default` in the path with its name.

### Enable the plugin

1. Restart QGIS.
2. Open *Plugins > Manage and Install Plugins > Installed* and tick **S2DR3**.
3. Open it from the **Gamma Earth** toolbar icon or the *Plugins > Gamma Earth* menu.

Tip: install the **Plugin Reloader** plugin to reload your changes without restarting QGIS.

To remove the link later, delete the symlink only (`rm <link>` on macOS/Linux,
`rmdir <link>` on Windows). Do not delete the folder it points to.
