#!/usr/bin/env python3
"""Generate exact third-party notices from a packaged Python/Rust runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from collections import defaultdict
from importlib import metadata
from pathlib import Path
from typing import Any

LICENSE_NAMES = ("license", "licence", "copying", "notice", "authors", "copyright", "unlicense")
SPDX_IDS = (
    "0BSD",
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "BSL-1.0",
    "CC0-1.0",
    "CDLA-Permissive-2.0",
    "GPL-2.0-only",
    "GPL-2.0-or-later",
    "GPL-3.0-only",
    "GPL-3.0-or-later",
    "ISC",
    "LGPL-2.1-only",
    "LGPL-2.1-or-later",
    "LGPL-3.0-only",
    "LGPL-3.0-or-later",
    "LLVM-exception",
    "MIT",
    "MIT-0",
    "MIT-CMU",
    "MPL-2.0",
    "OFL-1.1",
    "PSF-2.0",
    "Unicode-3.0",
    "Unlicense",
    "Zlib",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--python-root", type=Path)
    parser.add_argument("--site-packages", type=Path, required=True)
    parser.add_argument("--cargo-manifest", type=Path, required=True)
    parser.add_argument("--target", default="aarch64-apple-darwin")
    parser.add_argument("--manual-manifest", type=Path)
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def clean_license(value: str | None) -> str:
    if not value:
        return "UNKNOWN"
    value = " ".join(value.split())
    aliases = {
        "MIT License": "MIT",
        "BSD License": "BSD",
        "BSD 3-Clause License": "BSD-3-Clause",
        "Unlicense license": "Unlicense",
    }
    return aliases.get(value, value if len(value) <= 180 else "See included license text")


def project_url(package_metadata: metadata.PackageMetadata) -> str:
    for entry in package_metadata.get_all("Project-URL") or []:
        _, _, value = entry.partition(",")
        if value.strip():
            return value.strip()
    return package_metadata.get("Home-page") or ""


def is_notice_path(path: str) -> bool:
    name = Path(path).name.lower()
    return any(token in name for token in LICENSE_NAMES)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").strip()


def section_store() -> tuple[dict[str, dict[str, Any]], defaultdict[str, list[str]]]:
    return {}, defaultdict(list)


def add_section(
    sections: dict[str, dict[str, Any]],
    owners: defaultdict[str, list[str]],
    *,
    label: str,
    source: str,
    text: str,
) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"
    digest = hashlib.sha256(normalized.encode()).hexdigest()
    if digest not in sections:
        sections[digest] = {"label": label, "source": source, "text": normalized}
    if label not in owners[digest]:
        owners[digest].append(label)
    return digest


def python_components(
    site_packages: Path,
    sections: dict[str, dict[str, Any]],
    owners: defaultdict[str, list[str]],
    manual_license_files: dict[str, list[Path]],
) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    distributions = sorted(
        metadata.distributions(path=[str(site_packages)]),
        key=lambda dist: (dist.metadata.get("Name") or "").lower(),
    )
    for dist in distributions:
        package_metadata = dist.metadata
        name = package_metadata.get("Name") or "UNKNOWN"
        expression = package_metadata.get("License-Expression") or package_metadata.get("License")
        if not expression:
            classifiers = package_metadata.get_all("Classifier") or []
            approved = [
                item.removeprefix("License :: OSI Approved :: ")
                for item in classifiers
                if item.startswith("License :: OSI Approved :: ")
            ]
            expression = " OR ".join(approved)
        if not expression and name.lower() == "layerlab":
            # Older locally staged builds predate the explicit PEP 621 field.
            expression = "Apache-2.0"
        if not expression:
            raise RuntimeError(f"Python package has no declared license: {name} {dist.version}")

        files: list[Path] = []
        for item in dist.files or []:
            if not is_notice_path(str(item)):
                continue
            path = Path(dist.locate_file(item))
            if path.is_file():
                files.append(path)
        files.extend(manual_license_files.get(f"python:{name.lower()}", []))

        refs: list[str] = []
        for path in sorted(set(files)):
            refs.append(
                add_section(
                    sections,
                    owners,
                    label=f"Python package {name} {dist.version}",
                    source=str(path),
                    text=read_text(path),
                )
            )
        if not refs:
            raise RuntimeError(f"Python package has no license/notice text: {name} {dist.version}")

        components.append(
            {
                "ecosystem": "python",
                "name": name,
                "version": dist.version,
                "license": clean_license(expression),
                "url": project_url(package_metadata),
                "delivery": "bundled",
                "licenseSections": sorted(set(refs)),
            }
        )
    return components


def cargo_metadata(cargo_manifest: Path, target: str) -> dict[str, Any]:
    command = [
        "cargo",
        "metadata",
        "--manifest-path",
        str(cargo_manifest),
        "--format-version",
        "1",
        "--locked",
        "--filter-platform",
        target,
    ]
    return json.loads(subprocess.check_output(command, text=True))


def cargo_runtime_ids(metadata: dict[str, Any], manifest: Path) -> set[str]:
    packages = metadata["packages"]
    root = next(
        package
        for package in packages
        if Path(package["manifest_path"]).resolve() == manifest.resolve()
    )
    nodes = {node["id"]: node for node in metadata["resolve"]["nodes"]}
    seen: set[str] = set()
    pending = [root["id"]]
    while pending:
        package_id = pending.pop()
        if package_id in seen:
            continue
        seen.add(package_id)
        for dependency in nodes[package_id]["deps"]:
            if any(kind.get("kind") is None for kind in dependency["dep_kinds"]):
                pending.append(dependency["pkg"])
    return seen


def spdx_ids(expression: str) -> list[str]:
    normalized = expression.replace("/", " OR ")
    return [item for item in SPDX_IDS if re.search(rf"(?<![\w.-]){re.escape(item)}(?![\w.-])", normalized)]


def rust_components(
    manifest: Path,
    target: str,
    catalog: Path,
    sections: dict[str, dict[str, Any]],
    owners: defaultdict[str, list[str]],
) -> list[dict[str, Any]]:
    metadata = cargo_metadata(manifest, target)
    packages = {package["id"]: package for package in metadata["packages"]}
    components: list[dict[str, Any]] = []
    package_ids = sorted(
        cargo_runtime_ids(metadata, manifest),
        key=lambda package_id: (
            packages[package_id]["name"].lower(),
            packages[package_id]["version"],
            package_id,
        ),
    )
    for package_id in package_ids:
        package = packages[package_id]
        base = Path(package["manifest_path"]).parent
        files: list[Path] = []
        for pattern in ("LICENSE*", "LICENCE*", "COPYING*", "NOTICE*", "COPYRIGHT*", "UNLICENSE*"):
            files.extend(path for path in base.glob(pattern) if path.is_file())

        expression = package.get("license") or "UNKNOWN"
        refs: list[str] = []
        for path in sorted(set(files)):
            refs.append(
                add_section(
                    sections,
                    owners,
                    label=f"Rust crate {package['name']} {package['version']}",
                    source=str(path),
                    text=read_text(path),
                )
            )
        if not refs:
            for identifier in spdx_ids(expression):
                path = catalog / f"{identifier}.txt"
                if path.is_file():
                    refs.append(
                        add_section(
                            sections,
                            owners,
                            label=f"Rust crate {package['name']} {package['version']}",
                            source=str(path),
                            text=read_text(path),
                        )
                    )
        if expression == "UNKNOWN" or not refs:
            raise RuntimeError(
                f"Rust crate license is incomplete: {package['name']} {package['version']} "
                f"({expression})"
            )

        components.append(
            {
                "ecosystem": "rust",
                "name": package["name"],
                "version": package["version"],
                "license": expression,
                "url": package.get("repository") or package.get("homepage") or "",
                "delivery": "bundled",
                "licenseSections": sorted(set(refs)),
            }
        )
    return sorted(components, key=lambda item: (item["name"].lower(), item["version"]))


def manual_components(
    manifest: Path,
    repo_root: Path,
    sections: dict[str, dict[str, Any]],
    owners: defaultdict[str, list[str]],
) -> tuple[list[dict[str, Any]], dict[str, list[Path]]]:
    data = json.loads(manifest.read_text())
    components: list[dict[str, Any]] = []
    python_overrides: dict[str, list[Path]] = {}
    for item in data.get("components", []):
        files = [(repo_root / path).resolve() for path in item.get("licenseFiles", [])]
        if item.get("pythonPackage"):
            python_overrides[f"python:{item['pythonPackage'].lower()}"] = files
            continue
        refs = []
        for path in files:
            if not path.is_file():
                raise RuntimeError(f"Manual license file not found: {path}")
            refs.append(
                add_section(
                    sections,
                    owners,
                    label=f"{item['name']} {item.get('version', '')}".strip(),
                    source=str(path),
                    text=read_text(path),
                )
            )
        component = {key: value for key, value in item.items() if key != "licenseFiles"}
        component["ecosystem"] = component.get("ecosystem", "manual")
        component["version"] = component.get("version", "")
        component["url"] = component.get("url", "")
        component["licenseSections"] = sorted(set(refs))
        components.append(component)
    return components, python_overrides


def add_python_runtime(
    python_root: Path | None,
    sections: dict[str, dict[str, Any]],
    owners: defaultdict[str, list[str]],
) -> list[dict[str, Any]]:
    if python_root is None:
        return []
    candidates = sorted(python_root.glob("lib/python*/LICENSE.txt"))
    if not candidates:
        raise RuntimeError(f"Python runtime LICENSE.txt not found under {python_root}")
    license_path = candidates[-1]
    version = license_path.parent.name.removeprefix("python")
    ref = add_section(
        sections,
        owners,
        label=f"Python runtime {version}",
        source=str(license_path),
        text=read_text(license_path),
    )
    return [
        {
            "ecosystem": "runtime",
            "name": "Python",
            "version": version,
            "license": "PSF-2.0 and bundled third-party notices",
            "url": "https://www.python.org/",
            "delivery": "bundled",
            "licenseSections": [ref],
        }
    ]


def relative_sources(sections: dict[str, dict[str, Any]], repo_root: Path) -> None:
    for section in sections.values():
        path = Path(section["source"])
        try:
            section["source"] = str(path.resolve().relative_to(repo_root.resolve()))
        except ValueError:
            section["source"] = path.name


def write_outputs(
    output: Path,
    components: list[dict[str, Any]],
    sections: dict[str, dict[str, Any]],
    owners: defaultdict[str, list[str]],
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    components.sort(key=lambda item: (item["ecosystem"], item["name"].lower(), item["version"]))
    inventory = {
        "schemaVersion": 1,
        "componentCount": len(components),
        "components": components,
    }
    (output / "THIRD_PARTY_INVENTORY.json").write_text(
        json.dumps(inventory, indent=2, ensure_ascii=False) + "\n"
    )

    lines = [
        "# Third-party software inventory",
        "",
        "This file is generated from the exact packaged dependency metadata. "
        "Each component remains licensed by its respective owner.",
        "",
        "| Scope | Component | Version | License / terms | Delivery | Source |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in components:
        url = item["url"]
        source = f"[source]({url})" if url else ""
        cells = [
            item["ecosystem"],
            item["name"],
            item["version"],
            item["license"],
            item.get("delivery", "bundled"),
            source,
        ]
        lines.append("| " + " | ".join(str(cell).replace("|", "\\|") for cell in cells) + " |")
    lines.extend(
        [
            "",
            "Full license and notice texts are in `THIRD_PARTY_LICENSES.txt`. "
            "Machine-readable component-to-text references are in `THIRD_PARTY_INVENTORY.json`.",
            "",
        ]
    )
    (output / "THIRD_PARTY_NOTICES.md").write_text("\n".join(lines))

    text_lines = [
        "LAYERLAB THIRD-PARTY LICENSES AND NOTICES",
        "========================================",
        "",
        "Generated from the packaged Python runtime, the target-specific Rust dependency graph,",
        "and explicitly declared downloaded/runtime components.",
        "",
    ]
    for index, digest in enumerate(sorted(sections), start=1):
        section = sections[digest]
        labels = ", ".join(sorted(owners[digest]))
        text_lines.extend(
            [
                f"[{index}] {labels}",
                f"Source: {section['source']}",
                f"SHA256: {digest}",
                "-" * 78,
                section["text"].rstrip(),
                "",
                "=" * 78,
                "",
            ]
        )
    (output / "THIRD_PARTY_LICENSES.txt").write_text("\n".join(text_lines))


def main() -> None:
    args = parse_args()
    repo_root = args.repo_root.resolve()
    manual_manifest = args.manual_manifest or repo_root / "packaging/third-party-components.json"
    catalog = args.catalog or repo_root / "packaging/license-texts"
    sections, owners = section_store()

    manual, overrides = manual_components(manual_manifest, repo_root, sections, owners)
    components = add_python_runtime(args.python_root, sections, owners)
    components.extend(python_components(args.site_packages, sections, owners, overrides))
    components.extend(rust_components(args.cargo_manifest, args.target, catalog, sections, owners))
    components.extend(manual)
    relative_sources(sections, repo_root)
    write_outputs(args.output, components, sections, owners)
    print(
        f"license bundle: {len(components)} components, "
        f"{len(sections)} unique license/notice texts -> {args.output}"
    )


if __name__ == "__main__":
    main()
