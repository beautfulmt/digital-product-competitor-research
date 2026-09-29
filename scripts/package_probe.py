#!/usr/bin/env python3
"""Create a reproducible, read-only inventory of an application package."""

from __future__ import annotations

import argparse
import hashlib
import json
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

ZIP_KINDS = {".apk", ".aab", ".apks", ".ipa", ".msix", ".appx", ".zip"}
TEXT_EXT = {".json", ".xml", ".plist", ".strings", ".txt", ".html", ".htm", ".js", ".css", ".yaml", ".yml", ".ini", ".conf", ".swift", ".kt", ".java", ".dart", ".ts", ".tsx", ".jsx", ".md", ".proto", ".sql", ".storyboard", ".xib"}
SIGNAL_RE = re.compile(r"(?:https?://|\b(?:route|deeplink|page|screen|view|controller|feature|flag|experiment|pay|price|order|subscribe|member|premium|trial|aigc|llm|gpt|agent|model|prompt|chat|search|recommend|share|push|notification|analytics|track|event|onboard|login|signup|api|graphql)\b|登录|注册|订阅|会员|付费|订单|推荐|搜索|分享|消息|通知|活动|助手|模型|课程|直播|购物|结算)", re.I)
MAX_ENTRIES = 250_000
MAX_EXPANDED = 16 * 1024**3
MAX_SIGNAL_LINES = 20_000


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(command: list[str], timeout: int = 600, max_chars: int | None = 100_000) -> tuple[bool, str]:
    try:
        result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, check=False)
        output = result.stdout
        if max_chars is not None and len(output) > max_chars:
            output = output[:max_chars - 10_000] + "\n[output truncated]\n" + output[-10_000:]
        return result.returncode == 0, output
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)


def safe_extract_zip(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    target_root = target.resolve()
    expanded = 0
    with zipfile.ZipFile(source) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_ENTRIES:
            raise RuntimeError(f"Archive has more than {MAX_ENTRIES} entries")
        for info in entries:
            name = info.filename.replace("\\", "/")
            if not name or name.startswith("/") or "\x00" in name:
                raise RuntimeError(f"Unsafe archive member: {name!r}")
            destination = (target / name).resolve()
            if not destination.is_relative_to(target_root):
                raise RuntimeError(f"Archive member escapes target: {name!r}")
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                continue
            expanded += info.file_size
            if expanded > MAX_EXPANDED:
                raise RuntimeError("Archive expands beyond size limit")
            if info.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as original, destination.open("wb") as output:
                shutil.copyfileobj(original, output, 1024 * 1024)


def unpack(source: Path, work: Path, deep: bool, notes: list[str]) -> list[tuple[str, Path]]:
    kind = source.suffix.lower()
    extracted = work / "extracted"
    roots: list[tuple[str, Path]] = []
    if source.is_dir() and kind == ".app":
        roots.append(("app", source))
        notes.append("Scanned the supplied .app directory without executing it")
    elif kind in ZIP_KINDS:
        safe_extract_zip(source, extracted)
        roots.append(("package", extracted))
    elif kind == ".dmg" and shutil.which("hdiutil"):
        with tempfile.TemporaryDirectory(prefix="competitor-dmg-") as mount:
            ok, detail = run(["hdiutil", "attach", str(source), "-readonly", "-nobrowse", "-mountpoint", mount], 120)
            if not ok:
                raise RuntimeError(f"DMG mount failed: {detail}")
            try:
                shutil.copytree(mount, extracted, dirs_exist_ok=True, symlinks=False)
            finally:
                run(["hdiutil", "detach", mount, "-quiet"], 120)
        roots.append(("package", extracted))
    elif kind == ".pkg" and shutil.which("pkgutil"):
        ok, detail = run(["pkgutil", "--expand-full", str(source), str(extracted)], 600)
        if not ok:
            if extracted.exists():
                shutil.rmtree(extracted)
            ok, detail = run(["pkgutil", "--expand", str(source), str(extracted)], 600)
            if ok:
                notes.append("PKG component payloads may still need manual inspection")
        if ok:
            roots.append(("package", extracted))
        else:
            notes.append(f"PKG expansion unavailable: {detail}")
    elif kind in {".exe", ".msi"}:
        sevenzip = shutil.which("7zz") or shutil.which("7z")
        if sevenzip:
            extracted.mkdir(parents=True, exist_ok=True)
            ok, detail = run([sevenzip, "x", "-y", f"-o{extracted}", str(source)], 600)
            if ok:
                roots.append(("package", extracted))
            else:
                notes.append(f"Archive extraction unavailable: {detail}")
        elif kind == ".msi" and shutil.which("msiextract"):
            extracted.mkdir(parents=True, exist_ok=True)
            ok, detail = run(["msiextract", "-C", str(extracted), str(source)], 600)
            if ok:
                roots.append(("package", extracted))
            else:
                notes.append(f"MSI extraction unavailable: {detail}")
        else:
            notes.append("No compatible EXE/MSI extraction tool found; binary-only scan")
    else:
        notes.append(f"No extraction method for {kind or 'this path'}; binary-only scan")

    if deep and kind == ".apk":
        if shutil.which("apktool"):
            decoded = work / "apktool"
            ok, detail = run(["apktool", "d", "-f", "--no-src", "-o", str(decoded), str(source)], 600)
            if ok:
                roots.append(("apktool", decoded))
            else:
                notes.append(f"apktool failed: {detail}")
        else:
            notes.append("apktool unavailable")
        if shutil.which("jadx"):
            decompiled = work / "jadx"
            ok, detail = run(["jadx", "--show-bad-code", "--no-res", "-d", str(decompiled), str(source)], 900)
            if ok:
                roots.append(("jadx", decompiled))
            else:
                notes.append(f"jadx failed: {detail}")
        else:
            notes.append("jadx unavailable")

    if kind == ".apks":
        nested_root = work / "nested-apks"
        for nested_apk in sorted(extracted.rglob("*.apk"))[:30]:
            target = nested_root / nested_apk.stem
            try:
                safe_extract_zip(nested_apk, target)
                roots.append((f"nested-{nested_apk.stem}", target))
            except (OSError, RuntimeError, zipfile.BadZipFile) as error:
                notes.append(f"Nested APK {nested_apk.name} could not be expanded: {error}")

    if not roots:
        roots.append(("binary", source.parent))
    return roots


def package_metadata(source: Path, roots: list[tuple[str, Path]], notes: list[str]) -> dict:
    result = {"name": source.stem, "id": "", "version": "", "build": "", "platform": "unknown", "executable": ""}
    kind = source.suffix.lower()
    result["platform"] = "android" if kind in {".apk", ".aab", ".apks"} else "ios" if kind == ".ipa" else "windows" if kind in {".exe", ".msi", ".msix", ".appx"} else "macos" if kind in {".dmg", ".pkg", ".app"} else "unknown"
    if kind in {".apk", ".apks"}:
        apk_source = source
        if kind == ".apks":
            candidates = sorted((root / "extracted").rglob("*.apk"), key=lambda path: ("base" not in path.name.lower(), len(path.name)))
            if candidates:
                apk_source = candidates[0]
        for tool in ("aapt2", "aapt"):
            if not shutil.which(tool):
                continue
            ok, output = run([tool, "dump", "badging", str(apk_source)], 90)
            if ok:
                match = re.search(r"package: name='([^']+)' versionCode='([^']*)' versionName='([^']*)'", output)
                if match:
                    result.update({"id": match.group(1), "build": match.group(2), "version": match.group(3)})
                label = re.search(r"application-label:'([^']+)'", output)
                if label:
                    result["name"] = label.group(1)
                break
        for label, root in roots:
            if label == "apktool":
                manifest = root / "AndroidManifest.xml"
                if manifest.exists():
                    text = manifest.read_text(encoding="utf-8", errors="replace")[:100_000]
                    match = re.search(r'<manifest[^>]*\bpackage="([^"]+)"', text)
                    if match and not result["id"]:
                        result["id"] = match.group(1)
                    match = re.search(r'android:versionName="([^"]+)"', text)
                    if match and not result["version"]:
                        result["version"] = match.group(1)
    for _, root in roots:
        if not root.is_dir():
            continue
        plists = sorted(root.rglob("Info.plist"), key=lambda path: (len(path.parts), str(path)))
        for plist in plists[:30]:
            if ".app" not in str(plist):
                continue
            try:
                with plist.open("rb") as stream:
                    info = plistlib.load(stream)
            except (OSError, ValueError, TypeError):
                continue
            if isinstance(info, dict) and info.get("CFBundleIdentifier"):
                result.update({
                    "name": str(info.get("CFBundleDisplayName") or info.get("CFBundleName") or result["name"]),
                    "id": str(info.get("CFBundleIdentifier") or ""),
                    "version": str(info.get("CFBundleShortVersionString") or ""),
                    "build": str(info.get("CFBundleVersion") or ""),
                    "platform": "ios" if kind == ".ipa" else "macos",
                    "executable": str(info.get("CFBundleExecutable") or ""),
                })
                return result
    for _, root in roots:
        if not root.is_dir():
            continue
        for manifest in list(root.rglob("AppxManifest.xml"))[:10]:
            try:
                document = ElementTree.parse(manifest)
                identity = next((element for element in document.iter() if element.tag.rsplit("}", 1)[-1] == "Identity"), None)
            except ElementTree.ParseError:
                identity = None
            if identity is not None:
                result.update({"id": identity.attrib.get("Name", ""), "version": identity.attrib.get("Version", ""), "platform": "windows"})
                return result
    if not result["id"]:
        notes.append("Product identity could not be read from a manifest; verify manually")
    return result


def collect(source: Path, roots: list[tuple[str, Path]], notes: list[str]) -> tuple[list[dict], list[dict], list[dict]]:
    manifest: list[dict] = []
    components = defaultdict(lambda: {"files": 0, "bytes": 0})
    signals: list[dict] = []
    for label, root in roots:
        files = [root] if root.is_file() else (path for path in root.rglob("*") if path.is_file() and not path.is_symlink())
        for path in files:
            if label == "binary" and path != source:
                continue
            if len(manifest) >= MAX_ENTRIES:
                notes.append(f"Manifest truncated at {MAX_ENTRIES} files")
                break
            try:
                size = path.stat().st_size
                digest = sha256_file(path)
            except OSError:
                continue
            rel = path.name if root.is_file() else path.relative_to(root).as_posix()
            qualified = f"{label}/{rel}"
            component = "/".join(qualified.split("/")[:3])
            manifest.append({"path": qualified, "bytes": size, "sha256": digest, "component": component})
            components[component]["files"] += 1
            components[component]["bytes"] += size
            if len(signals) >= MAX_SIGNAL_LINES or size > 2 * 1024 * 1024 or path.suffix.lower() not in TEXT_EXT:
                continue
            try:
                raw = path.read_bytes()
                text = raw.decode("utf-8", errors="replace") if raw.count(b"\x00") < len(raw) // 12 else raw.decode("utf-16", errors="replace")
            except OSError:
                continue
            for line_number, line in enumerate(text.splitlines(), 1):
                snippet = " ".join(line.strip().split())[:280]
                if len(snippet) < 4 or not SIGNAL_RE.search(snippet):
                    continue
                signals.append({"path": qualified, "line": line_number, "text": snippet})
                if len(signals) >= MAX_SIGNAL_LINES:
                    notes.append(f"Text signals truncated at {MAX_SIGNAL_LINES} lines")
                    break
    manifest.sort(key=lambda item: item["path"])
    component_list = [{"name": name, **data} for name, data in components.items()]
    component_list.sort(key=lambda item: (-item["bytes"], item["name"]))
    return manifest, component_list, signals


def collect_deep_signals(source: Path, roots: list[tuple[str, Path]], metadata: dict, notes: list[str]) -> tuple[list[dict], list[dict]]:
    binary_signals: list[dict] = []
    asset_names: list[dict] = []
    candidates: list[Path] = []
    executable = metadata.get("executable")
    if executable:
        for _, root in roots:
            if not root.is_dir():
                continue
            for path in root.rglob(executable):
                if path.is_file() and (".app/" in path.as_posix() or ".app/Contents/MacOS/" in path.as_posix()):
                    candidates.append(path)
                    break
    if source.suffix.lower() in {".exe", ".msi"} and not candidates:
        candidates.append(source)
    if shutil.which("strings"):
        for path in candidates[:6]:
            try:
                process = subprocess.Popen(["strings", "-a", str(path)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, errors="replace")
                assert process.stdout is not None
                for line_number, line in enumerate(process.stdout, 1):
                    snippet = " ".join(line.strip().split())[:280]
                    if len(snippet) >= 4 and SIGNAL_RE.search(snippet):
                        binary_signals.append({"path": str(path), "text": snippet})
                    if len(binary_signals) >= 10_000 or line_number >= 1_000_000:
                        notes.append(f"Binary strings scan capped for {path.name}")
                        process.kill()
                        break
                process.stdout.close()
                process.wait(timeout=30)
            except (OSError, subprocess.TimeoutExpired) as error:
                notes.append(f"Could not scan binary strings: {path.name}: {error}")
    if shutil.which("assetutil"):
        for _, root in roots:
            if not root.is_dir():
                continue
            for car in list(root.rglob("Assets.car"))[:20]:
                ok, output = run(["assetutil", "--info", str(car)], 120, max_chars=None)
                if not ok:
                    continue
                try:
                    records = json.loads(output)
                except json.JSONDecodeError:
                    notes.append(f"Asset catalog metadata could not be parsed: {car.name}")
                    continue
                for record in records if isinstance(records, list) else []:
                    if isinstance(record, dict) and record.get("Name"):
                        asset_names.append({"catalog": str(car), "name": str(record["Name"])})
                        if len(asset_names) >= 30_000:
                            notes.append("Asset catalog names truncated at 30000")
                            break
    return binary_signals, asset_names


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("workdir", type=Path)
    parser.add_argument("--deep", action="store_true", help="Use available APK decoding tools")
    args = parser.parse_args()
    source = args.source.expanduser().resolve()
    work = args.workdir.expanduser().resolve()
    if not source.exists():
        parser.error(f"Source not found: {source}")
    if work.exists() and any(work.iterdir()):
        parser.error(f"Work directory must be empty: {work}")
    work.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []
    roots = unpack(source, work, args.deep, notes)
    metadata = package_metadata(source, roots, notes)
    manifest, components, signals = collect(source, roots, notes)
    binary_signals, asset_names = collect_deep_signals(source, roots, metadata, notes) if args.deep else ([], [])
    artifact_sha256 = sha256_file(source) if source.is_file() else hashlib.sha256(json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    inventory = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": str(source),
        "artifact": {"kind": source.suffix.lower().lstrip("."), "sha256": artifact_sha256, "bytes": source.stat().st_size if source.is_file() else sum(item["bytes"] for item in manifest)},
        "product": metadata,
        "coverage": {"roots": [{"label": label, "path": str(root)} for label, root in roots], "notes": notes},
        "manifest": manifest,
        "components": components,
        "signals": signals,
        "binary_signals": binary_signals,
        "asset_names": asset_names,
    }
    output = work / "inventory.json"
    output.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Inventory: {output}\nFiles: {len(manifest)}; text signals: {len(signals)}; binary signals: {len(binary_signals)}; asset names: {len(asset_names)}; product: {metadata['name']} {metadata['version']}")
    for note in notes:
        print(f"Coverage note: {note}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, zipfile.BadZipFile) as error:
        print(f"Probe failed: {error}", file=sys.stderr)
        sys.exit(1)
