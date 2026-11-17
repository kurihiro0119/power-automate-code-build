"""インポート前に検出できる誤りをチェックする。

Power Automate のインポート画面はエラーの理由をほとんど教えてくれないので、
「存在しないアクションを参照している」などの典型的な失敗をここで先に止めます。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Set

from . import catalog, flowdef, naming, params

ERROR, WARNING, INFO = "error", "warning", "info"

KNOWN_ACTION_TYPES = {
    "Compose", "InitializeVariable", "SetVariable", "IncrementVariable", "DecrementVariable",
    "AppendToArrayVariable", "AppendToStringVariable", "If", "Switch", "Foreach", "Until",
    "Scope", "Terminate", "Wait", "Query", "Select", "Join", "Table", "ParseJson",
    "Response", "Http", "Expression", "Workflow", "Function", "ApiConnection",
    "ApiConnectionWebhook", "Changeset",
} | flowdef.CONNECTOR_TYPES
KNOWN_TRIGGER_TYPES = {"Request", "Recurrence", "Http", "HttpWebhook", "ApiConnection",
                       "ApiConnectionWebhook"} | flowdef.CONNECTOR_TYPES
RUN_AFTER_STATUSES = {"Succeeded", "Failed", "Skipped", "TimedOut"}
VARIABLE_ACTION_TYPES = {"SetVariable", "IncrementVariable", "DecrementVariable",
                         "AppendToArrayVariable", "AppendToStringVariable"}

REF_RE = re.compile(r"\b(body|outputs|actions|items|result|variables|parameters)\(\s*'((?:[^']|'')+)'\s*\)")
BAD_NAME_CHARS_RE = re.compile(r"[\s<>%&\\?/]")


@dataclass
class Issue:
    level: str
    flow: str
    where: str
    message: str

    def format(self) -> str:
        label = {ERROR: "エラー", WARNING: "警告", INFO: "情報"}[self.level]
        loc = f"{self.flow}" + (f" > {self.where}" if self.where else "")
        return f"[{label}] {loc}: {self.message}"


def validate_solution_meta(solution) -> List[Issue]:
    issues: List[Issue] = []
    name = solution.unique_name

    def err(msg: str) -> None:
        issues.append(Issue(ERROR, "solution.json", "", msg))

    if not naming.SOLUTION_NAME_RE.match(name):
        err(f"uniqueName '{name}' は英字始まりの半角英数字とアンダースコアだけにしてください")
    if not naming.VERSION_RE.match(solution.version):
        err(f"version '{solution.version}' は 1.0.0.0 のような 4 つの数字にしてください")
    if not naming.PREFIX_RE.match(str(solution.publisher.get("prefix", ""))):
        err("publisher.prefix は英小文字始まりの 2〜8 文字 (英小文字・数字) にしてください")
    if not naming.SOLUTION_NAME_RE.match(str(solution.publisher.get("uniqueName", ""))):
        err("publisher.uniqueName は英字始まりの半角英数字にしてください")
    if not solution.flows:
        err("flows/ に *.flow.json が 1 つもありません")
    seen: Dict[str, str] = {}
    ids: Dict[str, str] = {}
    for flow in solution.flows:
        key = flow.name.lower()
        if key in seen:
            err(f"フロー名 '{flow.name}' が重複しています ({seen[key]} と {flow.path.name})")
        seen[key] = flow.path.name
        if flow.explicit_id and not naming.GUID_RE.match(flow.explicit_id.lower()):
            err(f"{flow.path.name}: id '{flow.explicit_id}' は GUID 形式ではありません")
        fid = solution.flow_id(flow)
        if fid in ids:
            err(f"フロー ID {fid} が重複しています ({ids[fid]} と {flow.path.name})")
        ids[fid] = flow.path.name
    return issues


def validate_flow(flow, definition: Dict[str, Any]) -> List[Issue]:
    """definition は normalize 済み・パラメータ置換済みのものを渡す。"""
    issues: List[Issue] = []

    def add(level: str, where: str, msg: str) -> None:
        issues.append(Issue(level, flow.name, where, msg))

    if not naming.FLOW_NAME_RE.match(flow.name):
        add(ERROR, "", "name は英字始まりの半角英数字とアンダースコア (80 文字以内) にしてください。"
                       "日本語の名前は displayName に書きます")
    if flow.state not in ("on", "off"):
        add(ERROR, "", f"state は on か off にしてください (現在: {flow.state})")

    # --- トリガー -----------------------------------------------------------
    triggers = definition.get("triggers")
    if not isinstance(triggers, dict) or len(triggers) != 1:
        add(ERROR, "triggers", "トリガーはちょうど 1 つ必要です")
        triggers = triggers if isinstance(triggers, dict) else {}
    for tname, trig in triggers.items():
        if not isinstance(trig, dict) or "type" not in trig:
            add(ERROR, f"triggers.{tname}", "type がありません")
            continue
        if trig["type"] not in KNOWN_TRIGGER_TYPES:
            add(WARNING, f"triggers.{tname}", f"見慣れないトリガー type '{trig['type']}' です")
        if trig["type"] == "Recurrence" and not isinstance(trig.get("recurrence"), dict):
            add(ERROR, f"triggers.{tname}", "Recurrence トリガーには recurrence (frequency / interval) が必要です")
        if trig["type"] in ("Http", "HttpWebhook"):
            add(WARNING, f"triggers.{tname}", "HTTP はプレミアム機能です (Power Automate Free では実行できません)")

    # --- アクション ---------------------------------------------------------
    actions = definition.get("actions")
    if not isinstance(actions, dict):
        add(ERROR, "actions", "actions はオブジェクトで書いてください")
        return issues
    if not actions:
        add(WARNING, "actions", "アクションが 1 つもありません")

    all_names: Dict[str, str] = {}
    foreach_names: Set[str] = set()
    for name, action, path, top in flowdef.iter_actions(actions):
        where = f"{path}.{name}"
        lower = name.lower()
        if lower in all_names:
            add(ERROR, where, f"アクション名 '{name}' が重複しています (入れ子の中も含めてフロー全体で一意にする必要があります。もう一方: {all_names[lower]})")
        all_names[lower] = where
        if BAD_NAME_CHARS_RE.search(name):
            add(ERROR, where, "アクション名に空白や記号 (< > % & \\ ? /) は使えません。空白はアンダースコアにします")
        if len(name) > 80:
            add(ERROR, where, "アクション名は 80 文字以内にしてください")
        atype = action.get("type")
        if not atype:
            add(ERROR, where, "type がありません")
            continue
        if atype not in KNOWN_ACTION_TYPES:
            add(WARNING, where, f"見慣れないアクション type '{atype}' です (タイプミスでなければ無視して構いません)")
        if atype == "Foreach":
            foreach_names.add(name)
            if "foreach" not in action:
                add(ERROR, where, "Foreach には foreach (繰り返す配列の式) が必要です")
        if atype == "If" and "expression" not in action:
            add(ERROR, where, "If (条件) には expression が必要です")
        if atype == "InitializeVariable" and not top:
            add(ERROR, where, "「変数を初期化する」はフローの最上位にしか置けません (ループや条件の中は不可)")
        if atype == "Http":
            add(WARNING, where, "HTTP アクションはプレミアム機能です (Power Automate Free では実行できません)")
        if atype in ("ApiConnection", "ApiConnectionWebhook"):
            add(WARNING, where, f"type '{atype}' は旧形式です。ソリューションでは OpenApiConnection 系を使ってください")
        if atype in flowdef.CONNECTOR_TYPES:
            _check_connector(add, where, action)

    for tname, trig in triggers.items():
        if isinstance(trig, dict) and trig.get("type") in flowdef.CONNECTOR_TYPES:
            _check_connector(add, f"triggers.{tname}", trig)

    # --- runAfter -----------------------------------------------------------
    for path, scope, _top in flowdef.iter_scopes(actions):
        graph: Dict[str, List[str]] = {}
        for name, action in scope.items():
            if not isinstance(action, dict):
                add(ERROR, f"{path}.{name}", "アクションはオブジェクトで書いてください")
                continue
            run_after = action.get("runAfter", {})
            if not isinstance(run_after, dict):
                add(ERROR, f"{path}.{name}", "runAfter はオブジェクトで書いてください")
                continue
            graph[name] = list(run_after)
            for dep, statuses in run_after.items():
                if dep not in scope:
                    hint = "別の階層にあります。runAfter は同じ階層のアクションしか指定できません" \
                        if dep.lower() in all_names else "そのようなアクションはありません"
                    add(ERROR, f"{path}.{name}", f"runAfter の '{dep}' が見つかりません ({hint})")
                if not isinstance(statuses, list) or not statuses:
                    add(ERROR, f"{path}.{name}", f"runAfter['{dep}'] は [\"Succeeded\"] のような配列にしてください")
                else:
                    bad = [s for s in statuses if s not in RUN_AFTER_STATUSES]
                    if bad:
                        add(ERROR, f"{path}.{name}", f"runAfter の状態 {bad} は使えません (使えるのは Succeeded / Failed / Skipped / TimedOut)")
        if scope and not any(not deps for deps in graph.values()):
            add(ERROR, path, "先頭になるアクション (runAfter が空のもの) がありません")
        cyc = _find_cycle(graph)
        if cyc:
            add(ERROR, path, "runAfter が循環しています: " + " -> ".join(cyc))

    # --- 式の中の参照 -------------------------------------------------------
    variables = {v.lower() for v in flowdef.variable_names(definition)}
    parameters = set((definition.get("parameters") or {}).keys())
    reported: Set[str] = set()
    for text in flowdef.all_strings({"t": triggers, "a": actions}):
        for func, target in REF_RE.findall(text):
            target = target.replace("''", "'")
            key = f"{func}:{target}"
            if key in reported:
                continue
            problem = None
            if func == "variables":
                if target.lower() not in variables:
                    problem = f"変数 '{target}' は「変数を初期化する」(InitializeVariable) で定義されていません"
            elif func == "parameters":
                if target not in parameters:
                    problem = f"パラメータ '{target}' は定義されていません"
            elif func == "items":
                if target not in foreach_names:
                    problem = f"items('{target}') の '{target}' という Foreach (Apply to each) はありません"
            elif target.lower() not in all_names:
                problem = f"{func}('{target}') の '{target}' というアクションはありません"
            if problem:
                reported.add(key)
                add(ERROR, "式", problem)

    for name, action, path, _top in flowdef.iter_actions(actions):
        if action.get("type") in VARIABLE_ACTION_TYPES:
            var = (action.get("inputs") or {}).get("name")
            if isinstance(var, str) and var.lower() not in variables:
                add(ERROR, f"{path}.{name}", f"変数 '{var}' は初期化されていません")

    return issues


def _check_connector(add, where: str, node: Dict[str, Any]) -> None:
    host = flowdef.host_of(node)
    conn = host.get("connectionName")
    op = host.get("operationId")
    if not isinstance(conn, str) or not conn:
        add(ERROR, where, "inputs.host.connectionName がありません (例: shared_sharepointonline)")
        return
    if not isinstance(op, str) or not op:
        add(ERROR, where, "inputs.host.operationId がありません (例: GetItems)")
    api = flowdef.api_name_of(host) or conn
    if not api.startswith("shared_"):
        add(WARNING, where, f"API 名 '{api}' が shared_ で始まっていません (カスタムコネクタは別途ソリューションに含める必要があります)")
        return
    entry = catalog.info(api)
    if not entry:
        add(INFO, where, f"コネクタ '{api}' はカタログ未掲載です (使えますが operationId は実物のエクスポートで確認してください)")
        return
    if entry.get("tier") == "premium":
        add(WARNING, where, f"{entry['displayName']} はプレミアム コネクタです (Power Automate Free では実行できません)")
    if isinstance(op, str) and op and op not in entry.get("operations", {}):
        add(INFO, where, f"operationId '{op}' は {entry['displayName']} のカタログに未掲載です (綴りを確認してください)")


def _find_cycle(graph: Dict[str, List[str]]) -> List[str]:
    state: Dict[str, int] = {}
    stack: List[str] = []

    def visit(node: str) -> List[str]:
        state[node] = 1
        stack.append(node)
        for dep in graph.get(node, []):
            if dep not in graph:
                continue
            if state.get(dep) == 1:
                return stack[stack.index(dep):] + [dep]
            if state.get(dep, 0) == 0:
                found = visit(dep)
                if found:
                    return found
        stack.pop()
        state[node] = 2
        return []

    for node in graph:
        if state.get(node, 0) == 0:
            found = visit(node)
            if found:
                return found
    return []


def unresolved_token_issues(flow, definition: Dict[str, Any], declared: Set[str], strict: bool) -> List[Issue]:
    issues: List[Issue] = []
    for token in sorted(params.find_tokens(definition)):
        if token not in declared:
            issues.append(Issue(ERROR, flow.name, "params",
                                f"{{{{{token}}}}} が solution.json の params に宣言されていません"))
        else:
            issues.append(Issue(ERROR if strict else WARNING, flow.name, "params",
                                f"{{{{{token}}}}} の値がありません (params.local.json か --param {token}=... で指定)"))
    return issues
