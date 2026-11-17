"""エクスポートしたソリューション zip をこのリポジトリのソース形式に取り込む。

デザイナーで作ったフローを「見本」としてコード化するための逆方向の変換です。
フローの GUID と接続参照の論理名は保持するので、再ビルドして再インポートすると元のフローが更新されます。
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, Dict

from . import canvas, flowdef
from .project import ProjectError, write_json


def _strip_boilerplate(definition: Dict[str, Any]) -> Dict[str, Any]:
    d = dict(definition)
    if d.get("$schema") == flowdef.SCHEMA_URL:
        d.pop("$schema")
    if d.get("contentVersion") in ("1.0.0.0", "undefined"):
        d.pop("contentVersion", None)
    params = dict(d.get("parameters") or {})
    for key, std in flowdef.STANDARD_PARAMETERS.items():
        if params.get(key) == std:
            params.pop(key)
    if params:
        d["parameters"] = params
    else:
        d.pop("parameters", None)
    for _kind, _name, node in flowdef.iter_connector_nodes(d):
        inputs = node.get("inputs") or {}
        if inputs.get("authentication") == flowdef.AUTH_EXPR:
            inputs.pop("authentication")
        host = inputs.get("host") or {}
        if host.get("apiId") == flowdef.API_ID_PREFIX + str(host.get("connectionName")):
            host.pop("apiId")
    return d


def import_solution_zip(zip_path: Path, root: Path, dir_name: str = "", force: bool = False) -> Path:
    try:
        zf = zipfile.ZipFile(zip_path)
    except (OSError, zipfile.BadZipFile) as e:
        raise ProjectError(f"{zip_path}: zip として開けません ({e})") from e
    with zf:
        names = set(zf.namelist())
        if "solution.xml" not in names or "customizations.xml" not in names:
            if "manifest.json" in names:
                raise ProjectError(
                    "これはレガシー パッケージ (.zip) です。import はソリューション zip 専用です。"
                    "中の definition.json の properties.definition を *.flow.json の definition に貼ってください"
                )
            raise ProjectError(f"{zip_path}: solution.xml / customizations.xml がありません")
        sol = ET.fromstring(zf.read("solution.xml")).find("SolutionManifest")
        cust = ET.fromstring(zf.read("customizations.xml"))
        if sol is None:
            raise ProjectError("solution.xml に SolutionManifest がありません")
        if (sol.findtext("Managed") or "0") != "0":
            raise ProjectError("マネージド ソリューションは取り込めません。アンマネージドでエクスポートしてください")

        def loc_name(node) -> str:
            el = node.find("LocalizedNames/LocalizedName") if node is not None else None
            return el.get("description", "") if el is not None else ""

        unique = sol.findtext("UniqueName") or "Imported"
        pub = sol.find("Publisher")
        manifest: Dict[str, Any] = {
            "uniqueName": unique,
            "displayName": loc_name(sol) or unique,
            "description": (sol.find("Descriptions/Description").get("description", "")
                            if sol.find("Descriptions/Description") is not None else ""),
            "version": sol.findtext("Version") or "1.0.0.0",
            "publisher": {
                "uniqueName": pub.findtext("UniqueName") if pub is not None else "labpublisher",
                "displayName": loc_name(pub) or "Lab Publisher",
                "prefix": pub.findtext("CustomizationPrefix") if pub is not None else "lab",
                "optionValuePrefix": int(pub.findtext("CustomizationOptionValuePrefix") or 10000) if pub is not None else 10000,
            },
            "params": {},
        }
        display_by_logical = {
            cr.get("connectionreferencelogicalname"): cr.findtext("connectionreferencedisplayname") or ""
            for cr in cust.findall("connectionreferences/connectionreference")
        }
        target = root / "solutions" / (dir_name or unique)
        if target.exists() and any(target.iterdir()) and not force:
            raise ProjectError(f"{target} は既にあります。上書きするなら --force、別名にするなら --name を付けてください")

        overrides: Dict[str, Dict[str, str]] = {}
        count = 0
        for wf in cust.findall("Workflows/Workflow"):
            if (wf.findtext("Category") or "") != "5":
                continue  # モダン フロー (クラウド フロー) 以外は対象外
            json_name = (wf.findtext("JsonFileName") or "").lstrip("/")
            if json_name not in names:
                raise ProjectError(f"zip 内に {json_name} がありません")
            doc = json.loads(zf.read(json_name).decode("utf-8-sig"))
            props = doc.get("properties", {})
            guid = (wf.get("WorkflowId") or "").strip("{}").lower()
            stem = Path(json_name).name
            stem = re.sub(r"-[0-9A-Fa-f-]{36}\.json$", "", stem)
            name = re.sub(r"[^A-Za-z0-9_]", "", stem)
            if not name or not name[0].isalpha():
                name = f"Flow{count + 1}"
            for conn, ref in (props.get("connectionReferences") or {}).items():
                logical = (ref.get("connection") or {}).get("connectionReferenceLogicalName")
                if logical:
                    entry = {"logicalName": logical}
                    if display_by_logical.get(logical):
                        entry["displayName"] = display_by_logical[logical]
                    overrides[conn] = entry
            flow_doc: Dict[str, Any] = {
                "name": name,
                "displayName": wf.get("Name") or name,
                "description": wf.get("Description") or "",
                "state": "on" if (wf.findtext("StateCode") or "1") == "1" else "off",
                "id": guid,
                "definition": _strip_boilerplate(props.get("definition") or {}),
            }
            write_json(target / "flows" / f"{name}.flow.json", flow_doc)
            count += 1
        app_count = _import_canvas_apps(zf, target)
        if count == 0 and app_count == 0:
            raise ProjectError("この zip にはクラウド フローもキャンバス アプリも含まれていません")
        if overrides:
            manifest["connectionReferences"] = overrides
        write_json(target / "solution.json", manifest)
        return target


def _app_dir_name(schema: str, display: str, taken: set) -> str:
    # スキーマ名は <prefix>_<名前>_<5桁> の形。真ん中を使い、無理なら表示名から作る
    m = re.match(r"^[a-z0-9]+_(.+?)(_[0-9a-f]{5})?$", schema)
    cand = m.group(1) if m else schema
    for text in (cand, display, "App"):
        name = re.sub(r"[^A-Za-z0-9_]", "", text or "")
        if name and name[0].isalpha():
            break
    else:
        name = "App"
    name = name[:1].upper() + name[1:]
    base, i = name, 2
    while name.lower() in taken:
        name, i = f"{base}{i}", i + 1
    taken.add(name.lower())
    return name


def _import_canvas_apps(zf: zipfile.ZipFile, target: Path) -> int:
    """キャンバス アプリを apps/<名前>/ (app.json + Src + base/) に取り出す。"""
    entries = canvas._canvas_entries(zf)
    taken: set = set()
    for _xml, display, info in entries:
        name = _app_dir_name(info["name"], display, taken)
        app_dir = target / "apps" / name
        canvas.capture_from_solution(zf, app_dir / "base", info["name"])
        src = canvas.read_msapp_src((app_dir / "base" / "base.msapp").read_bytes())
        for rel, text in src.items():
            dest = app_dir / "Src" / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")
        write_json(app_dir / "app.json", {
            "name": name,
            "displayName": display or name,
            "description": "",
            "schemaName": info["name"],
        })
    return len(entries)
