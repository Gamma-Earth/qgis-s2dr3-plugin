#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check a built plugin zip against the plugins.qgis.org requirements.

Usage: validate_package.py PLUGIN.zip [--changelog CHANGELOG.md] [--expect-version X.Y]
                                      [--root-license LICENSE]

Exits non-zero (and lists every problem) if the zip is not acceptable.
"""
import argparse
import configparser
import re
import sys
import zipfile
from pathlib import PurePosixPath

REQUIRED_FILES = ["__init__.py", "metadata.txt", "icon.png", "LICENSE"]
REQUIRED_METADATA = [
    "name", "qgisMinimumVersion", "description", "about", "version", "author",
    "email", "repository", "tracker", "homepage", "icon", "tags",
]
FORBIDDEN_DIRS = {
    "__pycache__", "__macosx", ".git", ".github", ".idea", ".vscode", ".venv",
    "venv", "tests", "test", "docs", "doc", "scripts", "dist", "build",
}
FORBIDDEN_NAMES = {".ds_store", ".gitignore", ".gitattributes", ".qgis-plugin-ci",
                   "thumbs.db", "development.md"}
FORBIDDEN_SUFFIXES = {".pyc", ".pyo", ".so", ".dll", ".dylib", ".exe", ".bin", ".o",
                      ".a", ".zip", ".tar", ".gz", ".whl", ".orig", ".rej", ".swp"}
MAX_ZIP_BYTES = 25 * 1024 * 1024
VERSION_RE = re.compile(r"^\d+(\.\d+){1,2}([-.]?[A-Za-z0-9]+)?$")


def changelog_versions(text):
    return re.findall(r"^##\s*\[?v?([0-9][^\]\s]*)\]?", text, flags=re.M)


def validate(zip_path, changelog=None, expect_version=None, root_license=None):
    problems = []
    zf = zipfile.ZipFile(zip_path)
    if zf.testzip() is not None:
        problems.append("zip is corrupt")
    names = [n for n in zf.namelist()]
    files = [n for n in names if not n.endswith("/")]

    tops = {PurePosixPath(n).parts[0] for n in names}
    if len(tops) != 1:
        problems.append(f"expected exactly one top-level folder, found: {sorted(tops)}")
        return problems
    top = tops.pop()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", top):
        problems.append(f"top-level folder '{top}' is not a valid Python module name")
    if any(len(PurePosixPath(n).parts) == 1 and not n.endswith("/") for n in names):
        problems.append("files found outside the top-level folder")

    for req in REQUIRED_FILES:
        if f"{top}/{req}" not in files:
            problems.append(f"missing required file: {req}")

    for n in files:
        p = PurePosixPath(n)
        parts_l = [x.lower() for x in p.parts[1:]]
        if any(x in FORBIDDEN_DIRS for x in parts_l[:-1]):
            problems.append(f"forbidden folder in zip: {n}")
        elif p.name.lower() in FORBIDDEN_NAMES or p.name.startswith("._"):
            problems.append(f"forbidden file in zip: {n}")
        elif p.suffix.lower() in FORBIDDEN_SUFFIXES:
            problems.append(f"forbidden file type in zip: {n}")
    if sum(i.file_size for i in zf.infolist()) > MAX_ZIP_BYTES:
        problems.append("plugin is larger than 25 MB uncompressed")

    meta = None
    if f"{top}/metadata.txt" in files:
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read_string(zf.read(f"{top}/metadata.txt").decode("utf-8"))
            meta = parser["general"]
        except (configparser.Error, KeyError, UnicodeDecodeError) as exc:
            problems.append(f"metadata.txt is not valid: {exc}")
    if meta is not None:
        for key in REQUIRED_METADATA:
            if not meta.get(key, "").strip():
                problems.append(f"metadata.txt: missing or empty '{key}'")
        version = meta.get("version", "").strip()
        if version and not VERSION_RE.match(version):
            problems.append(f"metadata.txt: version '{version}' is not like 1.2 or 1.2.3")
        if not re.fullmatch(r"\d+\.\d+(\.\d+)?", meta.get("qgisMinimumVersion", "")):
            problems.append("metadata.txt: qgisMinimumVersion must look like 3.34")
        for key in ("repository", "tracker", "homepage"):
            if meta.get(key) and not meta[key].startswith("https://"):
                problems.append(f"metadata.txt: '{key}' must be an https URL")
        if meta.get("icon") and f"{top}/{meta['icon']}" not in files:
            problems.append(f"metadata.txt: icon '{meta['icon']}' not in the zip")
        about = meta.get("about", "").lower()
        for word in ("api key", "credits", "privacy"):
            if word not in about:
                problems.append(f"metadata.txt: 'about' must mention '{word}'")
        if "experimental" not in meta:
            problems.append("metadata.txt: 'experimental' should be set explicitly")
        if meta.get("name", "").strip().lower().endswith(("plugin", "plug-in")):
            problems.append("metadata.txt: name should not end with 'plugin'")
        if expect_version and version != expect_version:
            problems.append(f"metadata.txt version {version} != expected {expect_version}")
        if changelog:
            versions = changelog_versions(open(changelog, encoding="utf-8").read())
            if not versions or versions[0] != version:
                problems.append(
                    f"CHANGELOG top version {versions[:1]} != metadata version {version}")

    license_name = f"{top}/LICENSE"
    if license_name in files:
        text = zf.read(license_name).decode("utf-8", "replace")
        if "GNU GENERAL PUBLIC LICENSE" not in text or "Version 3" not in text:
            problems.append("LICENSE is not the GPL-3.0 text")
        if root_license and open(root_license, encoding="utf-8").read() != text:
            problems.append("repository LICENSE differs from the packaged LICENSE")

    for n in files:
        if n.endswith(".py"):
            source = zf.read(n).decode("utf-8")
            if "SPDX-License-Identifier:" not in source:
                problems.append(f"missing SPDX header: {n}")
            try:
                compile(source, n, "exec")
            except SyntaxError as exc:
                problems.append(f"syntax error in {n}: {exc}")
            if re.search(r"^\s*print\(", source, flags=re.M):
                problems.append(f"debug print() left in {n}")
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("zip")
    ap.add_argument("--changelog")
    ap.add_argument("--expect-version")
    ap.add_argument("--root-license")
    a = ap.parse_args()
    problems = validate(a.zip, a.changelog, a.expect_version, a.root_license)
    if problems:
        print(f"INVALID: {a.zip}")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"OK: {a.zip} passes all checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
