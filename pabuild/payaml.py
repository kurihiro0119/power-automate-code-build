"""Power Apps のソースコード (*.pa.yaml, スキーマ v3) の読み込みと検証。

標準ライブラリだけで動かすため、pa.yaml が使う YAML のサブセット
(マッピング・シーケンス・プレーン/引用符付きスカラー・ブロック スカラー | >) だけを読みます。
アンカー・タグ・複数行のフロー記法 ([ ... ] を改行して書く) には対応しません。

スキーマ: https://raw.githubusercontent.com/microsoft/PowerApps-Tooling/master/schemas/pa-yaml/v3.0/pa.schema.yaml
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

HEADER = """# ************************************************************************************************
# Warning: YAML source code for Canvas Apps should only be used to review changes made within Power Apps Studio and for minor edits (Preview).
# Use the maker portal to create and edit your Power Apps.
#
# The schema file for Canvas Apps is available at https://go.microsoft.com/fwlink/?linkid=2304907
#
# For more information, visit https://go.microsoft.com/fwlink/?linkid=2292623
# ************************************************************************************************
"""

TOP_LEVEL_KEYS = {"App", "Screens", "ComponentDefinitions", "DataSources", "EditorState"}
SCREEN_KEYS = {"Properties", "Children"}
CONTROL_KEYS = {"Control", "Variant", "MetadataKey", "Layout", "IsLocked", "Group", "Properties",
                "Children", "ComponentName", "ComponentLibraryUniqueName"}
CONTROL_TYPE_RE = re.compile(r"^([A-Z][a-zA-Z0-9]*/)?[A-Z][a-zA-Z0-9]*(@\d+\.\d+\.\d+)?$")
DISALLOWED_CONTROLS = {"AppInfo", "HostControl", "Screen", "AppTest", "TestCase", "TestSuite"}
UNSUPPORTED_CONTROLS = {"CommandComponent", "DataComponent", "FunctionComponent"}


class PaYamlError(Exception):
    def __init__(self, line: int, message: str):
        super().__init__(f"{line}行: {message}")
        self.line = line
        self.message = message


# --------------------------------------------------------------------------- 読み込み


@dataclass
class _Line:
    no: int
    indent: int
    text: str  # インデントを除いた本文


KEY_RE = re.compile(r"""^(?P<key>'(?:[^']|'')*'|"(?:[^"\\]|\\.)*"|[^\s'"#\-][^:]*?|-[^\s:][^:]*?)\s*:(?:\s+(?P<val>.*)|$)""")


def _strip_comment(text: str) -> str:
    """引用符の外にある ' #' 以降をコメントとして落とす (値が = で始まる数式は対象外)。"""
    if text.lstrip().startswith("="):
        return text.rstrip()
    quote = ""
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
        elif ch == "#" and (i == 0 or text[i - 1] in " \t"):
            return text[:i].rstrip()
    return text.rstrip()


def _unquote(text: str, line: int) -> Any:
    t = text.strip()
    if t == "" or t in ("~", "null", "Null", "NULL"):
        return None
    if t.startswith("'"):
        if not t.endswith("'") or len(t) < 2:
            raise PaYamlError(line, f"引用符が閉じていません: {t}")
        return t[1:-1].replace("''", "'")
    if t.startswith('"'):
        if not t.endswith('"') or len(t) < 2:
            raise PaYamlError(line, f"引用符が閉じていません: {t}")
        body = t[1:-1]
        return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}.get(m.group(1), m.group(1)), body)
    if t in ("true", "True"):
        return True
    if t in ("false", "False"):
        return False
    return t


class _Parser:
    def __init__(self, text: str):
        self.raw = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        self.lines: List[_Line] = []
        i = 0
        while i < len(self.raw):
            line = self.raw[i]
            if "\t" in line[: len(line) - len(line.lstrip())]:
                raise PaYamlError(i + 1, "インデントにタブは使えません (半角スペースにしてください)")
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                self.lines.append(_Line(i + 1, len(line) - len(line.lstrip(" ")), line.strip()))
            i += 1
        self.pos = 0

    # ブロック スカラーは元の行から直接読む
    def _block_scalar(self, header: str, parent_indent: int, line_no: int) -> str:
        style = header[0]
        chomp = header[1:2]
        body: List[str] = []
        idx = line_no  # raw のインデックス = 次の行
        block_indent: Optional[int] = None
        while idx < len(self.raw):
            line = self.raw[idx]
            if line.strip() == "":
                body.append("")
                idx += 1
                continue
            ind = len(line) - len(line.lstrip(" "))
            if ind <= parent_indent:
                break
            if block_indent is None:
                block_indent = ind
            if ind < block_indent:
                break
            body.append(line[block_indent:])
            idx += 1
        # 消費した行を self.lines から飛ばす
        while self.pos < len(self.lines) and self.lines[self.pos].no <= idx:
            self.pos += 1
        while body and body[-1] == "":
            body.pop()
        text = "\n".join(body) if style == "|" else re.sub(r"(?<!\n)\n(?!\n)", " ", "\n".join(body))
        if chomp != "-":
            text += "\n"
        return text

    def parse(self) -> Any:
        if not self.lines:
            return None
        node = self._node(self.lines[0].indent)
        if self.pos < len(self.lines):
            ln = self.lines[self.pos]
            raise PaYamlError(ln.no, "インデントがずれています")
        return node

    def _node(self, indent: int) -> Any:
        ln = self.lines[self.pos]
        if ln.text == "-" or ln.text.startswith("- "):
            return self._seq(indent)
        return self._map(indent)

    def _value_after_key(self, val: Optional[str], indent: int, line_no: int) -> Any:
        if val is not None:
            v = _strip_comment(val)
            if v and v[0] in "|>":
                if not re.fullmatch(r"[|>][-+]?", v):
                    raise PaYamlError(line_no, f"ブロック スカラーの指定が読めません: {v}")
                return self._block_scalar(v, indent, line_no)
            if v.startswith(("&", "*", "!")):
                raise PaYamlError(line_no, "アンカー・エイリアス・タグは使えません")
            if v.startswith(("[", "{")) and not v.endswith(("]", "}")):
                raise PaYamlError(line_no, "複数行のフロー記法は使えません。1 行で書くか、数式は = から始めてください")
            if not v.startswith(("'", '"')) and re.search(r":\s", v):
                raise PaYamlError(
                    line_no,
                    "プレーン値に ': ' は書けません。全角コロン '：' にするか、値をブロック スカラー (|-) で書いてください",
                )
            if v != "":
                return _unquote(v, line_no)
        # 値が次の行以降 (子ブロック) にある
        if self.pos < len(self.lines):
            nxt = self.lines[self.pos]
            if nxt.indent > indent or (nxt.indent == indent and nxt.text.startswith("- ")):
                return self._node(nxt.indent)
        return None

    def _map(self, indent: int) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        while self.pos < len(self.lines):
            ln = self.lines[self.pos]
            if ln.indent < indent:
                break
            if ln.indent > indent:
                raise PaYamlError(ln.no, "インデントがずれています")
            if ln.text.startswith("- "):
                break
            m = KEY_RE.match(ln.text)
            if not m:
                raise PaYamlError(ln.no, f"「名前: 値」の形ではありません: {ln.text}")
            key = _unquote(m.group("key"), ln.no)
            key = str(key) if key is not None else ""
            if key in out:
                raise PaYamlError(ln.no, f"キー '{key}' が重複しています")
            self.pos += 1
            out[key] = self._value_after_key(m.group("val"), indent, ln.no)
            _LINES[id(out), key] = ln.no
        return out

    def _seq(self, indent: int) -> List[Any]:
        out: List[Any] = []
        while self.pos < len(self.lines):
            ln = self.lines[self.pos]
            if ln.indent != indent or not (ln.text == "-" or ln.text.startswith("- ")):
                if ln.indent > indent:
                    raise PaYamlError(ln.no, "インデントがずれています")
                break
            rest = ln.text[1:].lstrip(" ")
            if rest == "":
                self.pos += 1
                out.append(self._node(self.lines[self.pos].indent) if self.pos < len(self.lines)
                           and self.lines[self.pos].indent > indent else None)
                continue
            # "- key: ..." は、ダッシュの後ろの位置から始まるマッピングとして読む
            child_indent = indent + (len(ln.text) - len(rest))
            self.lines[self.pos] = _Line(ln.no, child_indent, rest)
            if KEY_RE.match(rest):
                out.append(self._map(child_indent))
            else:
                self.pos += 1
                out.append(_unquote(_strip_comment(rest), ln.no))
        return out


# 各マッピングのキーが何行目にあったか (エラー表示用)。id(dict), key -> 行番号
_LINES: Dict[Tuple[int, str], int] = {}


def parse(text: str) -> Any:
    return _Parser(text).parse()


def line_of(mapping: Dict[str, Any], key: str) -> int:
    return _LINES.get((id(mapping), key), 0)


# --------------------------------------------------------------------------- 検証


@dataclass
class YamlIssue:
    level: str  # "error" | "warning" | "info"
    file: str
    line: int
    message: str


@dataclass
class AppSource:
    """1 アプリ分の pa.yaml を合わせたもの。"""
    screens: Dict[str, str]  # 画面名 -> ファイル名
    screens_order: List[str]
    controls: Dict[str, str]  # コントロール名 -> ファイル名
    components: Set[str]
    start_screen: Optional[str]
    has_editor_state: bool
    issues: List[YamlIssue]


def _check_props(issues: List[YamlIssue], fname: str, owner: Dict[str, Any], where: str) -> None:
    props = owner.get("Properties")
    if props is None:
        return
    if not isinstance(props, dict):
        issues.append(YamlIssue("error", fname, line_of(owner, "Properties"), f"{where}: Properties はマッピングで書いてください"))
        return
    for key, val in props.items():
        if val is None:
            continue
        if not isinstance(val, str) or not val.startswith("="):
            issues.append(YamlIssue(
                "error", fname, line_of(props, key),
                f"{where}.{key}: 値は Power Fx の数式として = で始めてください (例: {key}: =\"文字列\" / =100 / =true)"))


def _check_children(issues: List[YamlIssue], fname: str, owner: Dict[str, Any], where: str,
                    controls: Dict[str, str], components: Set[str]) -> None:
    children = owner.get("Children")
    if children is None:
        return
    if not isinstance(children, list):
        issues.append(YamlIssue("error", fname, line_of(owner, "Children"),
                                f"{where}: Children は「- 名前:」のシーケンスで書いてください"))
        return
    for item in children:
        if not isinstance(item, dict) or len(item) != 1:
            issues.append(YamlIssue("error", fname, line_of(owner, "Children"),
                                    f"{where}: Children の各要素は「- コントロール名:」を 1 つだけ持つマッピングにしてください"))
            continue
        name, body = next(iter(item.items()))
        line = line_of(item, name)
        if name in controls:
            issues.append(YamlIssue("error", fname, line, f"コントロール名 '{name}' が重複しています (アプリ全体で一意。{controls[name]} にもあります)"))
        controls.setdefault(name, fname)
        if not isinstance(body, dict):
            issues.append(YamlIssue("error", fname, line, f"{name}: Control などを持つマッピングにしてください"))
            continue
        for key in body:
            if key not in CONTROL_KEYS:
                issues.append(YamlIssue("error", fname, line_of(body, key), f"{name}: '{key}' は書けません (使えるもの: {', '.join(sorted(CONTROL_KEYS))})"))
        ctype = body.get("Control")
        if not isinstance(ctype, str) or not ctype:
            issues.append(YamlIssue("error", fname, line, f"{name}: Control (例: Label / Classic/Button / Gallery) が必要です"))
        elif ctype in ("CanvasComponent", "CodeComponent"):
            if not body.get("ComponentName"):
                issues.append(YamlIssue("error", fname, line, f"{name}: {ctype} には ComponentName が必要です"))
            elif ctype == "CanvasComponent" and body.get("ComponentName") not in components and not body.get("ComponentLibraryUniqueName"):
                issues.append(YamlIssue("warning", fname, line, f"{name}: コンポーネント '{body.get('ComponentName')}' の定義が Src/Components にありません"))
        elif not CONTROL_TYPE_RE.match(ctype):
            issues.append(YamlIssue("error", fname, line_of(body, "Control"), f"{name}: Control '{ctype}' の形が不正です (例: Label, Classic/Button, Gallery@2.15.0)"))
        elif ctype.split("@")[0] in DISALLOWED_CONTROLS | UNSUPPORTED_CONTROLS:
            issues.append(YamlIssue("error", fname, line_of(body, "Control"), f"{name}: Control '{ctype}' は pa.yaml では使えません"))
        _check_props(issues, fname, body, name)
        _check_children(issues, fname, body, name, controls, components)


def analyze(files: Dict[str, str]) -> AppSource:
    """files: Src からの相対パス -> 本文 (パラメータ置換済み)。"""
    _LINES.clear()
    issues: List[YamlIssue] = []
    docs: Dict[str, Dict[str, Any]] = {}
    for fname, text in sorted(files.items()):
        try:
            doc = parse(text)
        except PaYamlError as e:
            issues.append(YamlIssue("error", fname, e.line, e.message))
            continue
        if doc is None:
            issues.append(YamlIssue("warning", fname, 0, "中身が空です"))
            continue
        if not isinstance(doc, dict):
            issues.append(YamlIssue("error", fname, 1, "最上位は App: / Screens: / ComponentDefinitions: のマッピングにしてください"))
            continue
        for key in doc:
            if key not in TOP_LEVEL_KEYS:
                hint = " (画面は Screens: の下に書きます)" if key not in ("App",) else ""
                issues.append(YamlIssue("error", fname, line_of(doc, key), f"最上位に '{key}' は書けません{hint}"))
        docs[fname] = doc

    components: Set[str] = set()
    for fname, doc in docs.items():
        comps = doc.get("ComponentDefinitions")
        if isinstance(comps, dict):
            components |= set(comps)

    screens: Dict[str, str] = {}
    controls: Dict[str, str] = {}
    start: Optional[str] = None
    order: List[str] = []
    has_editor_state = False
    app_seen = ""
    for fname, doc in docs.items():
        if "App" in doc:
            if app_seen:
                issues.append(YamlIssue("error", fname, line_of(doc, "App"), f"App: は 1 ファイルにだけ書いてください ({app_seen} にもあります)"))
            app_seen = fname
            app = doc.get("App") or {}
            if not isinstance(app, dict) or set(app) - {"Properties"}:
                issues.append(YamlIssue("error", fname, line_of(doc, "App"), "App: の下に書けるのは Properties だけです"))
            elif isinstance(app, dict):
                _check_props(issues, fname, app, "App")
                ss = (app.get("Properties") or {}).get("StartScreen") if isinstance(app.get("Properties"), dict) else None
                if isinstance(ss, str) and re.fullmatch(r"=\s*'?([A-Za-z_][^']*?)'?\s*", ss):
                    start = re.fullmatch(r"=\s*'?([A-Za-z_][^']*?)'?\s*", ss).group(1)
        if "EditorState" in doc:
            has_editor_state = True
            st = doc.get("EditorState") or {}
            if isinstance(st, dict) and isinstance(st.get("ScreensOrder"), list):
                order = [str(s) for s in st["ScreensOrder"]]
        scr = doc.get("Screens")
        if scr is None:
            continue
        if not isinstance(scr, dict):
            issues.append(YamlIssue("error", fname, line_of(doc, "Screens"), "Screens: の下は「画面名:」のマッピングにしてください"))
            continue
        for sname, body in scr.items():
            line = line_of(scr, sname)
            if sname in screens:
                issues.append(YamlIssue("error", fname, line, f"画面名 '{sname}' が重複しています ({screens[sname]} にもあります)"))
            screens[sname] = fname
            if sname in controls:
                issues.append(YamlIssue("error", fname, line, f"画面名 '{sname}' がコントロール名と重複しています"))
            controls.setdefault(sname, fname)
            if body is None:
                continue
            if not isinstance(body, dict):
                issues.append(YamlIssue("error", fname, line, f"画面 {sname}: Properties / Children を持つマッピングにしてください"))
                continue
            for key in body:
                if key not in SCREEN_KEYS:
                    issues.append(YamlIssue("error", fname, line_of(body, key), f"画面 {sname}: '{key}' は書けません (Properties / Children だけ)"))
            _check_props(issues, fname, body, sname)
            _check_children(issues, fname, body, sname, controls, components)

    for fname, doc in docs.items():
        comps = doc.get("ComponentDefinitions")
        if isinstance(comps, dict):
            for cname, body in comps.items():
                if isinstance(body, dict):
                    _check_children(issues, fname, body, cname, controls, components)

    if not screens:
        issues.append(YamlIssue("error", "Src", 0, "画面が 1 つもありません (Screens: を書いた *.pa.yaml が必要です)"))
    if start and start not in screens:
        issues.append(YamlIssue("error", app_seen, 0, f"StartScreen の '{start}' という画面がありません"))
    for s in order:
        if s not in screens:
            issues.append(YamlIssue("error", "_EditorState.pa.yaml", 0, f"ScreensOrder の '{s}' という画面がありません"))
    if not order:
        names = list(screens)
        if start in names:
            names.remove(start)
            names.insert(0, start)
        order = names
    else:
        order += [s for s in screens if s not in order]
    return AppSource(screens, order, controls, components, start, has_editor_state, issues)


def editor_state_yaml(screens_order: List[str], components: List[str]) -> str:
    lines = [HEADER + "EditorState:", "  ScreensOrder:"]
    lines += [f"    - {_yaml_key(s)}" for s in screens_order]
    if components:
        lines.append("  ComponentDefinitionsOrder:")
        lines += [f"    - {_yaml_key(c)}" for c in components]
    return "\n".join(lines) + "\n"


def _yaml_key(name: str) -> str:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        return name
    return "'" + name.replace("'", "''") + "'"
