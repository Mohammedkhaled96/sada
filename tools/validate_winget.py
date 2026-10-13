"""Validate the winget manifests in winget/ before they are submitted.

winget rejects a submission for small, avoidable reasons: the three manifests
disagreeing on a version, a placeholder left in an URL, or a hash that is not
64 hex characters. This catches those in CI instead of in review.

Run: python tools/validate_winget.py
"""

from __future__ import annotations

import glob
import os
import re
import sys

import yaml

IDENTIFIER = "MohammedKhaled.Sada"
SHA256_RE = re.compile(r"^[0-9A-Fa-f]{64}$")
REQUIRED_TYPES = {"version", "installer", "defaultLocale"}


def main() -> int:
    paths = sorted(glob.glob(os.path.join("winget", "*.yaml")))
    if not paths:
        print("FAIL no manifests found in winget/")
        return 1

    errors: list[str] = []
    versions: set[str] = set()
    types: set[str] = set()

    for path in paths:
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle)

        name = os.path.basename(path)
        if data.get("PackageIdentifier") != IDENTIFIER:
            errors.append(f"{name}: PackageIdentifier is {data.get('PackageIdentifier')!r}, expected {IDENTIFIER!r}")
        versions.add(str(data.get("PackageVersion")))
        types.add(data.get("ManifestType"))

        for key, value in data.items():
            if key.endswith("Url") and isinstance(value, str) and "REPLACE" in value.upper():
                errors.append(f"{name}: {key} still contains a placeholder: {value}")

        for installer in data.get("Installers") or []:
            url = installer.get("InstallerUrl", "")
            digest = installer.get("InstallerSha256", "")
            if "REPLACE" in url.upper():
                errors.append(f"{name}: InstallerUrl still contains a placeholder")
            if not url.startswith("https://"):
                errors.append(f"{name}: InstallerUrl must be https: {url!r}")
            if not SHA256_RE.match(str(digest)):
                errors.append(f"{name}: InstallerSha256 is not 64 hex characters: {digest!r}")

        print(f"ok   {name}  ({data.get('ManifestType')}, v{data.get('PackageVersion')})")

    if len(versions) > 1:
        errors.append(f"manifests disagree on PackageVersion: {sorted(versions)}")
    missing = REQUIRED_TYPES - types
    if missing:
        errors.append(f"missing required manifest type(s): {sorted(missing)}")

    for error in errors:
        print(f"FAIL {error}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
