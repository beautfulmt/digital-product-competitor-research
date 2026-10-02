#!/usr/bin/env python3
"""Render an editorial HTML report and maintain portable version snapshots."""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import zlib
from collections import defaultdict
from datetime import datetime, timezone
from html import escape
from pathlib import Path

SNAPSHOT_ID = "competitor-snapshot"
SNAPSHOT_RE = re.compile(r'<script\s+id="competitor-snapshot"[^>]*>(.*?)</script>', re.S | re.I)


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def compact_state(state: dict) -> dict:
    if "products" in state:
        return {
            "schema_version": state.get("schema_version", 1),
            "generated_at": state.get("generated_at", ""),
            "products": [compact_state(item) for item in state.get("products", []) if isinstance(item, dict)],
        }
    keep = ("schema_version", "generated_at", "product", "artifact", "coverage", "manifest")
    result = {key: state[key] for key in keep if key in state}
    module_fields = ("id", "name", "parent_id", "purpose", "related_ids")
    result["modules"] = [
        {key: module[key] for key in module_fields if key in module}
        for module in state.get("modules", []) if isinstance(module, dict)
    ]
    feature_fields = (
        "id", "name", "status", "module_id", "parent_id", "purpose", "scenario",
        "entry", "conditions", "journey", "states", "inputs", "outputs", "rules",
        "access", "related_ids", "interpretation",
    )
    result["features"] = [
        {key: feature[key] for key in feature_fields if key in feature}
        for feature in state.get("features", []) if isinstance(feature, dict)
    ]
    return result


def snapshot_tag(state: dict) -> str:
    packed = zlib.compress(json.dumps(compact_state(state), ensure_ascii=False, separators=(",", ":")).encode("utf-8"), level=9)
    encoded = base64.b64encode(packed).decode("ascii")
    return f'<script id="{SNAPSHOT_ID}" type="application/octet-stream" data-encoding="zlib-base64">{encoded}</script>'


def extract_snapshot(html_path: Path) -> dict:
    html = html_path.read_text(encoding="utf-8")
    match = SNAPSHOT_RE.search(html)
    if not match:
        raise ValueError(f"No {SNAPSHOT_ID} found in {html_path}")
    compressed = base64.b64decode(match.group(1).strip(), validate=True)
    state = json.loads(zlib.decompress(compressed))
    if not isinstance(state, dict) or state.get("schema_version") != 1:
        raise ValueError("Unsupported snapshot schema")
    return compact_state(state)


def embed_snapshot(html_path: Path, state_path: Path, output: Path) -> None:
    html = html_path.read_text(encoding="utf-8")
    tag = snapshot_tag(load_json(state_path))
    if SNAPSHOT_RE.search(html):
        html = SNAPSHOT_RE.sub(lambda _: tag, html, count=1)
    elif re.search(r"</body\s*>", html, re.I):
        html = re.sub(r"</body\s*>", lambda _: tag + "\n</body>", html, count=1, flags=re.I)
    else:
        raise ValueError("HTML has no closing body tag")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")


def init_state(inventory: dict) -> dict:
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "product": inventory.get("product", {}),
        "artifact": inventory.get("artifact", {}),
        "coverage": inventory.get("coverage", {}),
        "manifest": inventory.get("manifest", []),
        "modules": [],
        "features": [],
    }


def state_members(state: dict) -> list[dict]:
    if "products" in state:
        members = state["products"]
        if not isinstance(members, list) or not all(isinstance(member, dict) for member in members):
            raise ValueError("products must be an array of product states")
        return members
    return [state]


def one_new_state(state: dict) -> dict:
    members = state_members(state)
    if len(members) != 1:
        raise ValueError("Use one product state at a time for baseline search or diff")
    return members[0]


def matching_old_state(old: dict, new: dict) -> dict:
    members = state_members(old)
    if len(members) == 1:
        return members[0]
    product = new.get("product", {})
    matches = [member for member in members if member.get("product", {}).get("id") == product.get("id") and member.get("product", {}).get("platform") == product.get("platform")]
    if not matches:
        raise ValueError("Old report has no matching product and platform")
    return matches[0]


def plain(value: object) -> str:
    return escape(str(value or ""), quote=True)


def paragraphs(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return "".join(f"<p>{plain(part)}</p>" for part in re.split(r"\n\s*\n", text) if part.strip())


def block_html(block: dict) -> str:
    kind = block.get("type")
    if kind == "visuals":
        figures = []
        for item in block.get("items", []):
            if not isinstance(item, dict):
                continue
            src = str(item.get("src", ""))
            if not re.match(r"^data:image/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=]+$", src):
                continue
            caption = f'<figcaption>{plain(item.get("caption"))}</figcaption>' if item.get("caption") else ""
            figures.append(f'<figure><img src="{src}" alt="{plain(item.get("alt"))}" loading="lazy">{caption}</figure>')
        return '<div class="visuals">' + "".join(figures) + "</div>" if figures else ""
    if kind == "text":
        body = paragraphs(block.get("body"))
        if not body:
            return ""
        title = f"<h3>{plain(block['title'])}</h3>" if block.get("title") else ""
        return f'<article class="prose-block">{title}{body}</article>'
    if kind == "cards":
        items = [item for item in block.get("items", []) if isinstance(item, dict) and (item.get("title") or item.get("body"))]
        if not items:
            return ""
        cards = []
        for item in items:
            cards.append(f'<article class="card"><h3>{plain(item.get("title"))}</h3>{paragraphs(item.get("body"))}</article>')
        return '<div class="cards">' + "".join(cards) + "</div>"
    if kind == "flow":
        steps = [step for step in block.get("steps", []) if isinstance(step, dict) and (step.get("title") or step.get("body"))]
        if not steps:
            return ""
        rendered = []
        for index, step in enumerate(steps, 1):
            rendered.append(f'<li><span class="step-index">{index:02}</span><div><h3>{plain(step.get("title"))}</h3>{paragraphs(step.get("body"))}</div></li>')
        return '<ol class="flow">' + "".join(rendered) + "</ol>"
    if kind == "table":
        columns = block.get("columns", [])
        rows = block.get("rows", [])
        if not isinstance(columns, list) or not isinstance(rows, list) or not columns or not rows:
            return ""
        head = "".join(f"<th scope=\"col\">{plain(col)}</th>" for col in columns)
        body = "".join("<tr>" + "".join(f"<td>{plain(row[i] if i < len(row) else '')}</td>" for i in range(len(columns))) + "</tr>" for row in rows if isinstance(row, list))
        if not body:
            return ""
        return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'
    return ""


CSS = r"""
:root{--paper:#f7f5ef;--ink:#1d2524;--muted:#68716d;--line:#d8ddd5;--accent:#b84f38;--soft:#ebece4;--white:#fffefa}
.hero.solo{grid-template-columns:1fr}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--paper);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Noto Sans CJK SC","Microsoft YaHei",sans-serif;font-size:16px;line-height:1.75}
body:before{content:"";position:absolute;top:0;right:0;width:min(42vw,560px);height:560px;background:radial-gradient(circle at 70% 30%,rgba(184,79,56,.11),transparent 62%);pointer-events:none}
a{color:inherit}.page{max-width:1440px;margin:0 auto;padding:0 clamp(24px,5.6vw,92px)}.masthead{position:relative;display:flex;justify-content:space-between;align-items:center;gap:20px;padding:28px 0;border-bottom:1px solid var(--line);font-size:11px;font-weight:700;letter-spacing:.18em;text-transform:uppercase}.masthead .mark{display:inline-flex;align-items:center;gap:10px}.mark i{display:block;width:12px;height:12px;border-radius:50%;background:var(--accent)}.masthead .date{color:var(--muted);font-weight:500;letter-spacing:.08em}
.hero{position:relative;display:grid;grid-template-columns:minmax(0,1.4fr) minmax(260px,.6fr);gap:7vw;padding:clamp(72px,12vw,160px) 0 92px;align-items:end}.kicker,.section-index{font-size:11px;font-weight:700;letter-spacing:.16em;text-transform:uppercase;color:var(--accent)}.hero h1{font-family:"Songti SC","Noto Serif CJK SC",Georgia,serif;font-weight:500;letter-spacing:-.045em;line-height:1.1;font-size:clamp(54px,7vw,112px);margin:28px 0 24px;max-width:13ch}.hero h1:after{content:".";color:var(--accent)}.subtitle{font-size:clamp(18px,2vw,25px);line-height:1.5;max-width:34ch;color:#45504c;margin:0}.hero-aside{border-left:1px solid var(--line);padding-left:34px;align-self:end}.hero-aside .label{font-size:11px;letter-spacing:.12em;color:var(--muted);text-transform:uppercase}.hero-aside p{font-family:"Songti SC",Georgia,serif;font-size:clamp(19px,2vw,26px);line-height:1.5;margin:10px 0 28px}.meta{display:flex;flex-wrap:wrap;gap:12px 20px}.meta span{display:inline-flex;gap:8px;font-size:12px;color:var(--muted)}.meta b{font-weight:600;color:var(--ink)}
.nav{position:sticky;top:0;z-index:20;background:rgba(247,245,239,.93);backdrop-filter:blur(14px);border-top:1px solid var(--line);border-bottom:1px solid var(--line)}.nav-inner{display:flex;gap:26px;overflow-x:auto;scrollbar-width:none}.nav-inner::-webkit-scrollbar{display:none}.nav a{flex:none;padding:18px 0;text-decoration:none;font-size:12px;font-weight:600;color:var(--muted);border-bottom:2px solid transparent;white-space:nowrap}.nav a:hover,.nav a:focus-visible{color:var(--accent);border-color:var(--accent)}
.takeaways{padding:84px 0 94px}.section-top{display:flex;justify-content:space-between;gap:24px;align-items:baseline;border-bottom:1px solid var(--ink);padding-bottom:18px;margin-bottom:34px}.section-top h2{font-family:"Songti SC",Georgia,serif;font-size:clamp(35px,4vw,62px);font-weight:500;line-height:1.15;letter-spacing:-.04em;margin:0}.section-top span{font-size:12px;color:var(--muted)}.takeaway-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:22px}.takeaway{padding:26px 26px 24px;background:var(--white);border:1px solid var(--line);min-height:250px;display:flex;flex-direction:column}.takeaway .num{font-family:Georgia,serif;color:var(--accent);font-size:18px}.takeaway h3{font-size:21px;line-height:1.35;margin:38px 0 12px;font-weight:600}.takeaway p{font-size:14px;color:#4f5a55;margin:0}
.report-section{display:grid;grid-template-columns:minmax(180px,23%) minmax(0,1fr);gap:7vw;padding:90px 0 110px;border-top:1px solid var(--line);scroll-margin-top:70px}.section-side{position:sticky;top:96px;align-self:start}.section-side .section-index{display:block;margin-bottom:18px}.section-side h2{font-family:"Songti SC",Georgia,serif;font-size:clamp(34px,3.2vw,52px);line-height:1.15;letter-spacing:-.03em;font-weight:500;margin:0}.section-side p{font-size:13px;color:var(--muted);margin:22px 0 0;max-width:24ch}.section-content{display:grid;gap:38px}.prose-block{max-width:760px}.prose-block h3,.card h3,.flow h3{font-size:20px;line-height:1.4;margin:0 0 14px;font-weight:650}.prose-block p{font-size:16px;line-height:1.9;margin:0 0 18px;max-width:70ch}.cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.card{background:var(--white);border:1px solid var(--line);padding:30px;min-height:210px}.card p,.flow p{margin:0;color:#505a55;font-size:14px;line-height:1.8}.flow{list-style:none;padding:0;margin:0;border-top:1px solid var(--line)}.flow li{display:grid;grid-template-columns:78px 1fr;gap:20px;border-bottom:1px solid var(--line);padding:24px 0}.step-index{font-family:Georgia,serif;font-size:27px;color:var(--accent);line-height:1}.table-wrap{overflow-x:auto;border-top:1px solid var(--ink)}table{border-collapse:collapse;width:100%;min-width:540px;font-size:14px;text-align:left}th{font-size:11px;letter-spacing:.08em;color:var(--muted);font-weight:650}th,td{border-bottom:1px solid var(--line);padding:18px 16px 18px 0;vertical-align:top}td:first-child{font-weight:650;min-width:120px}.footer{border-top:1px solid var(--ink);padding:36px 0 70px;display:flex;justify-content:space-between;gap:24px;color:var(--muted);font-size:12px}.footer p{margin:0;max-width:72ch}.footer .stamp{color:var(--accent);font-family:Georgia,serif;font-size:22px;white-space:nowrap}
@media(max-width:900px){.hero{grid-template-columns:1fr;gap:45px}.hero-aside{border-left:0;border-top:1px solid var(--line);padding:22px 0 0}.hero-aside p{max-width:50ch;margin-bottom:18px}.takeaway-grid{grid-template-columns:1fr 1fr}.report-section{grid-template-columns:1fr;gap:36px}.section-side{position:static}.section-side p{max-width:60ch}}
@media(max-width:620px){.masthead{font-size:9px}.masthead .date{display:none}.hero{padding:78px 0 66px}.hero h1{font-size:clamp(48px,13vw,76px)}.takeaways{padding:60px 0}.takeaway-grid,.cards{grid-template-columns:1fr}.takeaway{min-height:0}.takeaway h3{margin-top:24px}.report-section{padding:65px 0 78px}.section-top h2{font-size:38px}.flow li{grid-template-columns:50px 1fr}.footer{display:block}.footer .stamp{display:block;margin-top:20px}}
@media print{body{background:white}.nav{display:none}.hero{padding:50px 0}.report-section{break-inside:avoid}.section-side{position:static}.takeaway,.card{break-inside:avoid}.footer{padding-bottom:20px}}
.hero h1:after{content:none}
.visuals{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.visuals figure{margin:0;padding:22px;background:var(--white);border:1px solid var(--line);display:flex;align-items:center;gap:22px}.visuals img{width:96px;height:96px;object-fit:cover;border-radius:22px;flex:none}.visuals figcaption{font-size:13px;color:var(--muted)}@media(max-width:620px){.visuals{grid-template-columns:1fr}.visuals img{width:76px;height:76px;border-radius:18px}}
.takeaway-grid.columns-2{grid-template-columns:repeat(2,minmax(0,1fr))}@media(max-width:620px){.takeaway-grid.columns-2{grid-template-columns:1fr}}
"""


def render(content: dict, state: dict) -> str:
    if not content.get("title"):
        raise ValueError("content.title is required")
    sections_html = []
    nav_links = []
    for section in content.get("sections", []):
        if not isinstance(section, dict):
            continue
        blocks = "".join(block_html(block) for block in section.get("blocks", []) if isinstance(block, dict))
        if not blocks:
            continue
        section_id = re.sub(r"[^a-zA-Z0-9_-]", "-", str(section.get("id") or f"section-{len(sections_html)+1}"))
        label = str(section.get("label") or section.get("title") or f"部分 {len(sections_html)+1}")
        title = str(section.get("title") or label)
        nav_links.append(f'<a href="#{plain(section_id)}">{plain(label)}</a>')
        intro = f"<p>{plain(section['intro'])}</p>" if section.get("intro") else ""
        sections_html.append(f'<section class="report-section" id="{plain(section_id)}"><div class="section-side"><span class="section-index">{len(sections_html)+1:02} / {plain(label)}</span><h2>{plain(title)}</h2>{intro}</div><div class="section-content">{blocks}</div></section>')
    takeaways = [item for item in content.get("takeaways", []) if isinstance(item, dict) and (item.get("title") or item.get("body"))]
    if not takeaways and not sections_html:
        raise ValueError("Report needs at least one substantive takeaway or section")
    takeaway_html = ""
    if takeaways:
        rendered = []
        for index, item in enumerate(takeaways, 1):
            rendered.append(f'<article class="takeaway"><span class="num">{index:02}</span><h3>{plain(item.get("title"))}</h3>{paragraphs(item.get("body"))}</article>')
        grid_class = "takeaway-grid columns-2" if len(takeaways) in {2, 4} else "takeaway-grid"
        takeaway_html = f'<section class="takeaways" id="overview"><div class="section-top"><h2>核心发现</h2></div><div class="{grid_class}">' + "".join(rendered) + "</div></section>"
        nav_links.insert(0, '<a href="#overview">核心发现</a>')
    meta = "".join(f'<span>{plain(item.get("label"))}<b>{plain(item.get("value"))}</b></span>' for item in content.get("meta", []) if isinstance(item, dict) and item.get("value"))
    nav = '<nav class="nav"><div class="page nav-inner">' + "".join(nav_links) + "</div></nav>" if nav_links else ""
    footnote = paragraphs(content.get("footnote"))
    footer = f'<footer class="footer">{footnote}</footer>' if footnote else ""
    eyebrow = f'<div class="kicker">{plain(content["eyebrow"])}</div>' if content.get("eyebrow") else ""
    subtitle = f'<p class="subtitle">{plain(content["subtitle"])}</p>' if content.get("subtitle") else ""
    aside = f'<div class="hero-aside"><div class="label">关键判断</div><p>{plain(content.get("lead"))}</p><div class="meta">{meta}</div></div>' if content.get("lead") else ""
    hero_class = "hero" if aside else "hero solo"
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light"><title>{plain(content["title"])}</title><style>{CSS}</style></head>
<body><div class="page"><header class="masthead"><span class="mark"><i></i>竞品观察</span></header><section class="{hero_class}"><div>{eyebrow}<h1>{plain(content["title"])}</h1>{subtitle}</div>{aside}</section></div>{nav}<main class="page">{takeaway_html}{''.join(sections_html)}{footer}</main>{snapshot_tag(state)}</body></html>'''


def read_state(path: Path) -> dict:
    return extract_snapshot(path) if path.suffix.lower() in {".html", ".htm"} else load_json(path)


def record_candidates(old_records: list, new_records: list) -> dict:
    old = {item["id"]: item for item in old_records if isinstance(item, dict) and item.get("id")}
    new = {item["id"]: item for item in new_records if isinstance(item, dict) and item.get("id")}
    common = old.keys() & new.keys()
    return {
        "added_ids": sorted(new.keys() - old.keys()),
        "removed_ids": sorted(old.keys() - new.keys()),
        "changed_ids": sorted(
            key for key in common
            if any(old[key][field] != new[key][field] for field in (old[key].keys() & new[key].keys()) - {"id"})
        ),
        "detail_added_ids": sorted(key for key in common if new[key].keys() - old[key].keys()),
        "detail_missing_ids": sorted(key for key in common if old[key].keys() - new[key].keys()),
    }


def compare(old: dict, new: dict) -> dict:
    old_product, new_product = old.get("product", {}), new.get("product", {})
    old_id, new_id = old_product.get("id"), new_product.get("id")
    old_platform, new_platform = old_product.get("platform"), new_product.get("platform")
    if old_platform and new_platform and old_platform != new_platform:
        identity = "different-platform"
    elif old_id and new_id and old_id != new_id:
        identity = "different-id-review-client-track"
    elif old_id and new_id and old_id == new_id:
        identity = "same-product-id"
    else:
        identity = "identity-unverified"
    old_files = {item["path"]: item for item in old.get("manifest", []) if isinstance(item, dict) and item.get("path")}
    new_files = {item["path"]: item for item in new.get("manifest", []) if isinstance(item, dict) and item.get("path")}
    added = sorted(new_files.keys() - old_files.keys())
    removed = sorted(old_files.keys() - new_files.keys())
    changed = sorted(path for path in old_files.keys() & new_files.keys() if old_files[path].get("sha256") != new_files[path].get("sha256"))
    removed_by_hash: dict[str, list[str]] = defaultdict(list)
    for path in removed:
        removed_by_hash[str(old_files[path].get("sha256"))].append(path)
    renames = []
    for path in added:
        matches = removed_by_hash.get(str(new_files[path].get("sha256")), [])
        if matches:
            renames.append({"old": matches.pop(0), "new": path})
    component_changes: dict[str, dict[str, int]] = defaultdict(lambda: {"added": 0, "removed": 0, "changed": 0})
    for path in added:
        component_changes[str(new_files[path].get("component", "unknown"))]["added"] += 1
    for path in removed:
        component_changes[str(old_files[path].get("component", "unknown"))]["removed"] += 1
    for path in changed:
        component_changes[str(new_files[path].get("component", "unknown"))]["changed"] += 1
    old_modules, new_modules = old.get("modules", []), new.get("modules", [])
    module_candidates = {"status": "module-map-unavailable"}
    if old_modules and new_modules:
        module_candidates = {"status": "compared", **record_candidates(old_modules, new_modules)}
    return {
        "identity": identity,
        "old": {"name": old_product.get("name"), "version": old_product.get("version"), "platform": old_platform, "artifact_sha256": old.get("artifact", {}).get("sha256")},
        "new": {"name": new_product.get("name"), "version": new_product.get("version"), "platform": new_platform, "artifact_sha256": new.get("artifact", {}).get("sha256")},
        "file_counts": {"old": len(old_files), "new": len(new_files), "added": len(added), "removed": len(removed), "changed": len(changed), "possible_renames": len(renames)},
        "added_paths": added,
        "removed_paths": removed,
        "changed_paths": changed,
        "possible_renames": renames,
        "component_changes": [{"component": component, **counts} for component, counts in sorted(component_changes.items(), key=lambda pair: (-sum(pair[1].values()), pair[0]))],
        "module_candidates": module_candidates,
        "feature_candidates": record_candidates(old.get("features", []), new.get("features", [])),
        "interpretation_notice": "File, module and feature differences are investigation leads, not confirmed user-facing changes. Newly recorded or missing detail fields indicate analysis coverage changes and need review.",
    }


def find_baselines(directory: Path, new: dict) -> dict:
    new = one_new_state(new)
    product = new.get("product", {})
    product_id, platform = product.get("id"), product.get("platform")
    if not product_id or not platform or platform == "unknown":
        return {"status": "identity-unverified", "matches": [], "note": "Verify product ID and platform before selecting a baseline."}
    matches = []
    for path in directory.rglob("*.html"):
        try:
            old_states = state_members(extract_snapshot(path))
        except (OSError, ValueError, json.JSONDecodeError, zlib.error):
            continue
        for old in old_states:
            old_product = old.get("product", {})
            if old_product.get("id") != product_id or old_product.get("platform") != platform:
                continue
            if old.get("artifact", {}).get("sha256") == new.get("artifact", {}).get("sha256"):
                continue
            matches.append({"path": str(path), "product_id": product_id, "platform": platform, "version": old_product.get("version", ""), "generated_at": old.get("generated_at", ""), "modified_at": path.stat().st_mtime})
    matches.sort(key=lambda item: (item["generated_at"], item["modified_at"]), reverse=True)
    return {"status": "matches-found" if matches else "no-baseline", "matches": matches, "note": "Review candidate version and client identity before diffing."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, arguments in {
        "init-state": ("inventory", "output"),
        "render": ("content", "state", "output"),
        "embed": ("html", "state", "output"),
        "extract": ("html", "output"),
        "diff": ("old", "new", "output"),
        "find-baseline": ("directory", "new", "output"),
    }.items():
        command = sub.add_parser(name)
        for argument in arguments:
            command.add_argument(argument, type=Path)
    merge = sub.add_parser("merge-states")
    merge.add_argument("output", type=Path)
    merge.add_argument("states", type=Path, nargs="+")
    args = parser.parse_args()
    if args.command == "init-state":
        write_json(args.output, init_state(load_json(args.inventory)))
    elif args.command == "render":
        html = render(load_json(args.content), load_json(args.state))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(html, encoding="utf-8")
    elif args.command == "embed":
        embed_snapshot(args.html, args.state, args.output)
    elif args.command == "extract":
        write_json(args.output, extract_snapshot(args.html))
    elif args.command == "diff":
        new = one_new_state(read_state(args.new))
        write_json(args.output, compare(matching_old_state(read_state(args.old), new), new))
    elif args.command == "find-baseline":
        write_json(args.output, find_baselines(args.directory, read_state(args.new)))
    elif args.command == "merge-states":
        members = [one_new_state(read_state(path)) for path in args.states]
        keys = [(member.get("product", {}).get("id"), member.get("product", {}).get("platform")) for member in members]
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate product and platform in merged state")
        write_json(args.output, {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(), "products": members})
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, json.JSONDecodeError, zlib.error) as error:
        print(f"Report operation failed: {error}", file=sys.stderr)
        sys.exit(1)
