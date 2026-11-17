"""新しいソリューション / フローの雛形を作る。"""
from __future__ import annotations

import re
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


# --------------------------------------------------------------------------- キャンバス アプリ


def app_templates_dir(root: Path) -> Path:
    return root / "templates" / "apps"


def list_app_templates(root: Path) -> List[Dict[str, Any]]:
    out = []
    base = app_templates_dir(root)
    if not base.is_dir():
        return out
    for d in sorted(base.iterdir()):
        meta_path = d / "template.json"
        if not meta_path.is_file():
            continue  # bases/ など
        meta = read_json(meta_path)
        texts = [p.read_text(encoding="utf-8") for p in sorted((d / "Src").rglob("*.pa.yaml"))]
        tokens = set()
        for t in texts:
            tokens |= params.find_tokens(t)
        out.append({
            "id": d.name,
            "title": meta.get("title", d.name),
            "summary": meta.get("summary", ""),
            "verified": meta.get("verified", ""),
            "dataSources": meta.get("dataSources", []),
            "params": sorted(tokens),
            "meta": meta,
            "path": d,
        })
    return out


def add_base(root: Path, source: Path, base_id: str, title: str = "", app_name: str = "",
             target: Path = None) -> Path:
    """Studio で保存した .msapp か、キャンバス アプリ入りのソリューション zip をベースとして登録する。"""
    import zipfile
    from . import canvas

    if target is None:
        if not re.match(r"^[a-z0-9][a-z0-9-]{0,47}$", base_id):
            raise ProjectError("ベース ID は英小文字・数字・ハイフンにしてください (例: blank-tablet)")
        target = canvas.bases_dir(root) / base_id
    try:
        zf = zipfile.ZipFile(source)
    except (OSError, zipfile.BadZipFile) as e:
        raise ProjectError(f"{source}: zip / .msapp として開けません ({e})") from e
    with zf:
        names = set(zf.namelist())
        if "customizations.xml" in names:
            meta = canvas.capture_from_solution(zf, target, app_name)
        else:
            meta = canvas.capture_from_msapp(source.read_bytes(), target)
    if title:
        meta["title"] = title
        write_json(target / "base.json", meta)
    return target


def new_app(root: Path, sol_dir: Path, app_name: str, template_id: str, base_id: str = "",
            base_file: str = "", display_name: str = "") -> Path:
    from . import canvas

    if not naming.FLOW_NAME_RE.match(app_name):
        raise ProjectError("アプリ名は英字始まりの半角英数字とアンダースコアだけにしてください (日本語名は --display で指定)")
    tpl = {t["id"]: t for t in list_app_templates(root)}.get(template_id)
    if not tpl:
        ids = ", ".join(t["id"] for t in list_app_templates(root)) or "(なし)"
        raise ProjectError(f"アプリ テンプレート '{template_id}' がありません。使えるもの: {ids}")
    target = sol_dir / "apps" / app_name
    if (target / "app.json").exists():
        raise ProjectError(f"{target} は既にあります")

    base_ref = ""
    if base_file:
        pass  # アプリ専用のベースとして下で取り込む
    elif base_id:
        if not (canvas.bases_dir(root) / base_id / "base.msapp").is_file():
            known = ", ".join(b.id for b in canvas.list_bases(root)) or "(なし)"
            raise ProjectError(f"ベース '{base_id}' がありません。登録済み: {known}")
        base_ref = base_id
    else:
        bases = canvas.list_bases(root)
        if len(bases) == 1:
            base_ref = bases[0].id
        elif not bases:
            raise ProjectError(
                "ベースの .msapp が登録されていません。Power Apps Studio で空のキャンバス アプリを作って保存し、"
                "そのアプリを入れたソリューションをエクスポートして "
                "`python3 -m pabuild app-base <zip> --id blank-tablet` で登録してください "
                "(手順: docs/app-authoring.md)。または --base-file <zip|msapp> を指定してください")
        else:
            raise ProjectError("ベースが複数あります。--base で選んでください: " + ", ".join(b.id for b in bases))

    if base_file:
        add_base(root, Path(base_file), "", target=target / "base")
    src_root = tpl["path"] / "Src"
    for p in sorted(src_root.rglob("*.pa.yaml")):
        dest = target / "Src" / p.relative_to(src_root)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    meta = tpl["meta"]
    doc: Dict[str, Any] = {
        "name": app_name,
        "displayName": display_name or meta.get("title", app_name),
        "description": meta.get("summary", ""),
    }
    if base_ref:
        doc["base"] = base_ref
    if meta.get("postImport"):
        doc["postImport"] = meta["postImport"]
    write_json(target / "app.json", doc)

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
