"""ソース (solutions/<dir>/solution.json と flows/*.flow.json) の読み込み。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import naming


class ProjectError(Exception):
    """ソースの構造が壊れているときのエラー (メッセージはそのまま利用者に見せる)。"""


def read_json(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8-sig") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise ProjectError(f"{path}: JSON の文法エラー ({e.lineno}行 {e.colno}列): {e.msg}") from e
    except FileNotFoundError as e:
        raise ProjectError(f"{path}: ファイルがありません") from e


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


@dataclass
class Flow:
    path: Path
    name: str
    display_name: str
    description: str
    state: str  # "on" | "off"
    explicit_id: Optional[str]
    definition: Dict[str, Any]
    post_import: List[str] = field(default_factory=list)


@dataclass
class Solution:
    dir: Path
    unique_name: str
    display_name: str
    description: str
    version: str
    publisher: Dict[str, Any]
    params_spec: Dict[str, Dict[str, Any]]
    connref_overrides: Dict[str, Dict[str, str]]
    flows: List[Flow]
    apps: List[Any] = field(default_factory=list)  # canvas.App

    @property
    def prefix(self) -> str:
        return self.publisher["prefix"]

    def flow_id(self, flow: Flow) -> str:
        return (flow.explicit_id or naming.flow_guid(self.unique_name, flow.name)).lower()


DEFAULT_PUBLISHER = {
    "uniqueName": "labpublisher",
    "displayName": "Lab Publisher",
    "prefix": "lab",
    "optionValuePrefix": 10000,
}


def _normalize_params_spec(raw: Any, where: Path) -> Dict[str, Dict[str, Any]]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ProjectError(f"{where}: params はオブジェクトで書いてください")
    spec: Dict[str, Dict[str, Any]] = {}
    for key, val in raw.items():
        if isinstance(val, dict) and (set(val) <= {"description", "default", "example"}):
            spec[key] = dict(val)
        else:
            spec[key] = {"default": val}
    return spec


def load_flow(path: Path) -> Flow:
    raw = read_json(path)
    if not isinstance(raw, dict):
        raise ProjectError(f"{path}: フロー定義はオブジェクトで書いてください")
    default_name = path.name[: -len(".flow.json")] if path.name.endswith(".flow.json") else path.stem
    name = raw.get("name", default_name)
    definition = raw.get("definition")
    if not isinstance(definition, dict):
        raise ProjectError(f"{path}: definition (triggers / actions を持つオブジェクト) が必要です")
    state = str(raw.get("state", "on")).lower()
    post = raw.get("postImport", [])
    if isinstance(post, str):
        post = [post]
    return Flow(
        path=path,
        name=name,
        display_name=raw.get("displayName", name),
        description=raw.get("description", ""),
        state=state,
        explicit_id=(raw.get("id") or None),
        definition=definition,
        post_import=list(post),
    )


def load_solution(sol_dir: Path) -> Solution:
    manifest_path = sol_dir / "solution.json"
    raw = read_json(manifest_path)
    if not isinstance(raw, dict) or "uniqueName" not in raw:
        raise ProjectError(f"{manifest_path}: uniqueName が必要です")
    publisher = dict(DEFAULT_PUBLISHER)
    publisher.update(raw.get("publisher") or {})
    flows = [load_flow(p) for p in sorted((sol_dir / "flows").glob("*.flow.json"))]
    from .canvas import load_apps  # canvas は project を import するので遅延読み込み
    return Solution(
        dir=sol_dir,
        unique_name=raw["uniqueName"],
        display_name=raw.get("displayName", raw["uniqueName"]),
        description=raw.get("description", ""),
        version=str(raw.get("version", "1.0.0.0")),
        publisher=publisher,
        params_spec=_normalize_params_spec(raw.get("params"), manifest_path),
        connref_overrides=raw.get("connectionReferences") or {},
        flows=flows,
        apps=load_apps(sol_dir),
    )


def find_solutions(root: Path) -> List[Path]:
    base = root / "solutions"
    if not base.is_dir():
        return []
    return sorted(p.parent for p in base.glob("*/solution.json"))


def resolve_solution_dir(root: Path, name: str) -> Path:
    """ディレクトリ名・uniqueName・パスのどれでも指定できるようにする。"""
    cand = Path(name)
    if (cand / "solution.json").is_file():
        return cand
    cand = root / "solutions" / name
    if (cand / "solution.json").is_file():
        return cand
    for d in find_solutions(root):
        try:
            if read_json(d / "solution.json").get("uniqueName") == name:
                return d
        except ProjectError:
            continue
    known = ", ".join(d.name for d in find_solutions(root)) or "(なし)"
    raise ProjectError(f"ソリューション '{name}' が見つかりません。存在するもの: {known}")
