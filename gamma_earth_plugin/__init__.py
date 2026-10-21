# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026 Gamma Earth


def classFactory(iface):
    try:
        import requests  # noqa: F401  (ships with QGIS)
    except ImportError:
        from qgis.core import Qgis
        iface.messageBar().pushMessage(
            "Gamma Earth S2DR3",
            "The Python package 'requests' is missing from this QGIS installation, "
            "so the plugin cannot start.",
            level=Qgis.Critical,
        )
        return _Disabled()
    from .gamma_earth_plugin import GammaEarthPlugin
    return GammaEarthPlugin(iface)


class _Disabled:
    def initGui(self):
        pass

    def unload(self):
        pass
