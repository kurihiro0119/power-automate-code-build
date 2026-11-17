"""新しいソリューション / フローの雛形を作る。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from . import flowdef, naming, params
from .project import DEFAULT_PUBLISHER, ProjectError, read_json, write_json


def templates_dir(root: Path) -> Path:
    return root / "templates" / "flows"


def list_templates(root: Path) -> List[Dict[str, Any]]:
    out = []
    for path in sorted(templates_dir(root).glob("*.flow.json")):
        raw = read_json(path)
        meta = raw.get("template") or {}
        conns = sorted(set(flowdef.connections_used(flowdef.normalize(raw.get("definition") or {})).values()))
        out.append({
            "id": path.name[: -len(".flow.json")],
            "title": meta.get("title", raw.get("displayName", "")),
            "summary": meta.get("summary", ""),
            "verified": meta.get("verified", ""),
            "connectors": conns,
            "params": sorted(params.find_tokens(raw.get("definition") or {})),
            "path": path,
        })
    return out


def new_solution(root: Path, unique_name: str, display_name: str = "", prefix: str = "",
                 publisher: str = "", description: str = "") -> Path:
    if not naming.SOLUTION_NAME_RE.match(unique_name):
        raise ProjectError("ソリューション名は英字始まりの半角英数字とアンダースコアだけにしてください (例: SalesAutomation)")
    target = root / "solutions" / unique_name
    if (target / "solution.json").exists():
        raise ProjectError(f"{target} は既にあります")
    pub = dict(DEFAULT_PUBLISHER)
    if prefix:
        if not naming.PREFIX_RE.match(prefix):
            raise ProjectError("prefix は英小文字始まりの 2〜8 文字にしてください (例: lab)")
        pub["prefix"] = prefix
    if publisher:
        pub["uniqueName"] = publisher
        pub["displayName"] = publisher
    write_json(target / "solution.json", {
        "uniqueName": unique_name,
        "displayName": display_name or unique_name,
        "description": description,
        "version": "1.0.0.0",
        "publisher": pub,
        "params": {},
    })
    (target / "flows").mkdir(parents=True, exist_ok=True)
    return target


def new_flow(root: Path, sol_dir: Path, flow_name: str, template_id: str, display_name: str = "") -> Path:
    if not naming.FLOW_NAME_RE.match(flow_name):
        raise ProjectError("フロー名は英字始まりの半角英数字とアンダースコアだけにしてください (日本語名は --display で指定)")
    tpl_path = templates_dir(root) / f"{template_id}.flow.json"
    if not tpl_path.is_file():
        ids = ", ".join(t["id"] for t in list_templates(root))
        raise ProjectError(f"テンプレート '{template_id}' がありません。使えるもの: {ids}")
    target = sol_dir / "flows" / f"{flow_name}.flow.json"
    if target.exists():
        raise ProjectError(f"{target} は既にあります")
    raw = read_json(tpl_path)
    meta = raw.pop("template", {}) or {}
    doc: Dict[str, Any] = {"name": flow_name, "displayName": display_name or raw.get("displayName", flow_name)}
    for key, val in raw.items():
        if key not in ("name", "displayName", "id"):
            doc[key] = val
    write_json(target, doc)

    # テンプレートが必要とするパラメータを solution.json に追記 (既存の宣言は触らない)
    manifest_path = sol_dir / "solution.json"
    manifest = read_json(manifest_path)
    spec = manifest.setdefault("params", {})
    added = False
    for key, pmeta in (meta.get("params") or {}).items():
        if key not in spec:
            spec[key] = pmeta
            added = True
    if added:
        write_json(manifest_path, manifest)
    return target
