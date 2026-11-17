"""{{TOKEN}} 形式のビルド時パラメータ。

サイト URL や宛先メールアドレスのように環境ごとに変わる値をフロー定義から追い出し、
solution.json (既定値) < params.local.json (Git 管理外) < --param K=V の順で上書きします。
"""
from __future__ import annotations

import copy
import re
from typing import Any, Dict, Iterable, Set, Tuple

TOKEN_RE = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")
WHOLE_TOKEN_RE = re.compile(r"^\{\{([A-Z][A-Z0-9_]*)\}\}$")


def find_tokens(node: Any) -> Set[str]:
    found: Set[str] = set()
    if isinstance(node, str):
        found.update(TOKEN_RE.findall(node))
    elif isinstance(node, dict):
        for k, v in node.items():
            found.update(TOKEN_RE.findall(k))
            found.update(find_tokens(v))
    elif isinstance(node, list):
        for v in node:
            found.update(find_tokens(v))
    return found


def substitute(node: Any, values: Dict[str, Any]) -> Any:
    """トークンを置換した新しいオブジェクトを返す。値が無いトークンはそのまま残す。

    文字列全体が 1 つのトークンなら値の型 (数値・真偽値・配列など) を保つ。
    """
    if isinstance(node, str):
        m = WHOLE_TOKEN_RE.match(node)
        if m and m.group(1) in values:
            return copy.deepcopy(values[m.group(1)])

        def repl(match: "re.Match[str]") -> str:
            key = match.group(1)
            if key not in values:
                return match.group(0)
            val = values[key]
            if isinstance(val, bool):
                return "true" if val else "false"
            return str(val)

        return TOKEN_RE.sub(repl, node)
    if isinstance(node, dict):
        return {substitute(k, values) if isinstance(k, str) else k: substitute(v, values) for k, v in node.items()}
    if isinstance(node, list):
        return [substitute(v, values) for v in node]
    return node


def resolve(
    spec: Dict[str, Dict[str, Any]],
    local: Dict[str, Any],
    overrides: Dict[str, Any],
    used: Iterable[str],
    use_examples: bool = False,
) -> Tuple[Dict[str, Any], Set[str], Set[str]]:
    """(確定した値, 値が無いトークン, example で埋めたトークン) を返す。"""
    values: Dict[str, Any] = {}
    from_example: Set[str] = set()
    for key, meta in spec.items():
        if meta.get("default") is not None:
            values[key] = meta["default"]
    values.update({k: v for k, v in local.items() if v is not None})
    values.update(overrides)
    missing: Set[str] = set()
    for key in used:
        if key in values:
            continue
        example = spec.get(key, {}).get("example")
        if use_examples and example is not None:
            values[key] = example
            from_example.add(key)
        else:
            missing.add(key)
    return values, missing, from_example


def parse_cli_value(text: str) -> Any:
    low = text.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    return text
