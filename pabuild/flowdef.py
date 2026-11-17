"""ワークフロー定義 (Logic Apps のワークフロー定義言語) の走査と正規化。"""
from __future__ import annotations

import copy
from typing import Any, Dict, Iterator, List, Optional, Tuple

SCHEMA_URL = "https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#"
API_ID_PREFIX = "/providers/Microsoft.PowerApps/apis/"
AUTH_EXPR = "@parameters('$authentication')"

CONNECTOR_TYPES = {
    "OpenApiConnection",
    "OpenApiConnectionWebhook",
    "OpenApiConnectionNotification",
}

STANDARD_PARAMETERS = {
    "$connections": {"defaultValue": {}, "type": "Object"},
    "$authentication": {"defaultValue": {}, "type": "SecureObject"},
}


def child_action_maps(action: Dict[str, Any]) -> Iterator[Tuple[str, Dict[str, Any]]]:
    """Scope / If / Switch / Foreach / Until の内側にあるアクション群を (ラベル, 辞書) で返す。"""
    if isinstance(action.get("actions"), dict):
        yield "actions", action["actions"]
    els = action.get("else")
    if isinstance(els, dict) and isinstance(els.get("actions"), dict):
        yield "else", els["actions"]
    cases = action.get("cases")
    if isinstance(cases, dict):
        for case_name, case in cases.items():
            if isinstance(case, dict) and isinstance(case.get("actions"), dict):
                yield f"cases.{case_name}", case["actions"]
    default = action.get("default")
    if isinstance(default, dict) and isinstance(default.get("actions"), dict):
        yield "default", default["actions"]


def iter_scopes(actions: Dict[str, Any], path: str = "actions") -> Iterator[Tuple[str, Dict[str, Any], bool]]:
    """全スコープを (パス, アクション辞書, 最上位かどうか) で返す。"""
    yield path, actions, path == "actions"
    for name, action in actions.items():
        if not isinstance(action, dict):
            continue
        for label, child in child_action_maps(action):
            sub = f"{path}.{name}" if label == "actions" else f"{path}.{name}.{label}"
            for item in iter_scopes(child, sub):
                yield item[0], item[1], False


def iter_actions(actions: Dict[str, Any]) -> Iterator[Tuple[str, Dict[str, Any], str, bool]]:
    """全アクションを (名前, 本体, スコープのパス, 最上位かどうか) で返す。"""
    for path, scope, top in iter_scopes(actions):
        for name, action in scope.items():
            if isinstance(action, dict):
                yield name, action, path, top


def iter_connector_nodes(definition: Dict[str, Any]) -> Iterator[Tuple[str, str, Dict[str, Any]]]:
    """コネクタを使うトリガー/アクションを (種別, 名前, 本体) で返す。"""
    for name, trig in (definition.get("triggers") or {}).items():
        if isinstance(trig, dict) and trig.get("type") in CONNECTOR_TYPES:
            yield "trigger", name, trig
    for name, action, _path, _top in iter_actions(definition.get("actions") or {}):
        if action.get("type") in CONNECTOR_TYPES:
            yield "action", name, action


def host_of(node: Dict[str, Any]) -> Dict[str, Any]:
    inputs = node.get("inputs")
    if isinstance(inputs, dict) and isinstance(inputs.get("host"), dict):
        return inputs["host"]
    return {}


def api_name_of(host: Dict[str, Any]) -> Optional[str]:
    api_id = host.get("apiId")
    if isinstance(api_id, str) and api_id.startswith(API_ID_PREFIX):
        return api_id[len(API_ID_PREFIX):]
    conn = host.get("connectionName")
    return conn if isinstance(conn, str) else None


def normalize(definition: Dict[str, Any]) -> Dict[str, Any]:
    """書き手が省略できる定型部分を補った新しい定義を返す。

    - $schema / contentVersion / parameters ($connections, $authentication)
    - コネクタ系アクションの host.apiId と inputs.authentication
    - runAfter の省略 (= 先頭アクション) を {} に
    """
    src = copy.deepcopy(definition)
    out: Dict[str, Any] = {
        "$schema": src.pop("$schema", SCHEMA_URL),
        "contentVersion": src.pop("contentVersion", "1.0.0.0"),
    }
    parameters = dict(STANDARD_PARAMETERS)
    parameters.update(src.pop("parameters", None) or {})
    out["parameters"] = parameters
    out["triggers"] = src.pop("triggers", {})
    out["actions"] = src.pop("actions", {})
    out.update(src)  # outputs, description など未知のキーは保持

    for _kind, _name, node in iter_connector_nodes(out):
        inputs = node.setdefault("inputs", {})
        host = inputs.setdefault("host", {})
        conn = host.get("connectionName")
        if isinstance(conn, str) and "apiId" not in host:
            host["apiId"] = API_ID_PREFIX + conn
        inputs.setdefault("authentication", AUTH_EXPR)

    for _path, scope, _top in iter_scopes(out["actions"]):
        for action in scope.values():
            if isinstance(action, dict):
                action.setdefault("runAfter", {})
    return out


def connections_used(definition: Dict[str, Any]) -> Dict[str, str]:
    """{connectionName: API 名 (shared_xxx)} を出現順で返す。"""
    used: Dict[str, str] = {}
    for _kind, _name, node in iter_connector_nodes(definition):
        host = host_of(node)
        conn = host.get("connectionName")
        api = api_name_of(host)
        if isinstance(conn, str) and api:
            used.setdefault(conn, api)
    return used


def all_strings(node: Any) -> Iterator[str]:
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for k, v in node.items():
            if isinstance(k, str):
                yield k
            yield from all_strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from all_strings(v)


def variable_names(definition: Dict[str, Any]) -> List[str]:
    names: List[str] = []
    for _name, action, _path, _top in iter_actions(definition.get("actions") or {}):
        if action.get("type") == "InitializeVariable":
            for var in (action.get("inputs") or {}).get("variables") or []:
                if isinstance(var, dict) and isinstance(var.get("name"), str):
                    names.append(var["name"])
    return names
