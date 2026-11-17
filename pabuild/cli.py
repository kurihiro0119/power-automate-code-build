"""コマンドライン: python3 -m pabuild <command>"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__, build as builder, guide, importer, naming, params, scaffold
from .project import ProjectError, find_solutions, load_solution, read_json, resolve_solution_dir, write_json
from .validate import ERROR, INFO, WARNING


def _default_root() -> Path:
    cwd = Path.cwd()
    if (cwd / "solutions").is_dir() or (cwd / "templates").is_dir():
        return cwd
    return Path(__file__).resolve().parent.parent


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)


def _selected(root: Path, names: List[str]) -> List[Path]:
    if names:
        return [resolve_solution_dir(root, n) for n in names]
    dirs = find_solutions(root)
    if not dirs:
        raise ProjectError("solutions/ にソリューションがありません。`new-solution` で作成してください")
    return dirs


def _parse_overrides(pairs: Optional[List[str]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise ProjectError(f"--param は NAME=値 の形で指定してください: {pair}")
        key, val = pair.split("=", 1)
        out[key.strip()] = params.parse_cli_value(val)
    return out


def _print_issues(prep, show_info: bool) -> None:
    for issue in prep.issues:
        if issue.level == INFO and not show_info:
            continue
        stream = sys.stderr if issue.level == ERROR else sys.stdout
        print("  " + issue.format(), file=stream)


def cmd_list(args, root: Path) -> int:
    for d in find_solutions(root):
        s = load_solution(d)
        print(f"{d.name}  ({s.display_name} / v{s.version})")
        for f in s.flows:
            print(f"  - {f.name}: {f.display_name}")
    return 0


def cmd_templates(args, root: Path) -> int:
    for t in scaffold.list_templates(root):
        print(f"{t['id']}")
        print(f"    {t['title']} — {t['summary']}")
        print(f"    コネクタ: {', '.join(t['connectors']) or 'なし'}"
              + (f" / パラメータ: {', '.join(t['params'])}" if t["params"] else ""))
        if t["verified"]:
            print(f"    検証状況: {t['verified']}")
    return 0


def cmd_new_solution(args, root: Path) -> int:
    target = scaffold.new_solution(root, args.name, args.display, args.prefix, args.publisher, args.description)
    print(f"作成しました: {_rel(target)}")
    print(f"次: python3 -m pabuild new-flow {target.name} <FlowName> --template manual-hello")
    return 0


def cmd_new_flow(args, root: Path) -> int:
    sol_dir = resolve_solution_dir(root, args.solution)
    target = scaffold.new_flow(root, sol_dir, args.name, args.template, args.display)
    print(f"作成しました: {_rel(target)}")
    print(f"次: python3 -m pabuild build {sol_dir.name}")
    return 0


def cmd_validate(args, root: Path) -> int:
    failed = False
    for d in _selected(root, args.solutions):
        prep = builder.prepare(load_solution(d), _parse_overrides(args.param), strict_params=False, use_examples=True)
        errors = len(prep.errors)
        warns = len([i for i in prep.issues if i.level == WARNING])
        print(f"{d.name}: フロー {len(prep.flows)} 件 / エラー {errors} 件 / 警告 {warns} 件")
        _print_issues(prep, args.verbose)
        failed = failed or bool(errors)
    return 1 if failed else 0


def cmd_build(args, root: Path) -> int:
    out_dir = Path(args.out) if args.out else root / "dist"
    failed = False
    for d in _selected(root, args.solutions):
        solution = load_solution(d)
        prep = builder.prepare(solution, _parse_overrides(args.param), strict_params=True,
                               use_examples=args.use_examples)
        print(f"{d.name}:")
        _print_issues(prep, args.verbose)
        if prep.errors:
            print(f"  → エラー {len(prep.errors)} 件のためビルドを中止しました", file=sys.stderr)
            failed = True
            continue
        zip_path = out_dir / builder.solution_zip_name(solution)
        builder.write_zip(zip_path, builder.solution_files(prep))
        print(f"  ソリューション: {_rel(zip_path)}")
        legacy_names: List[str] = []
        if args.legacy:
            for pf in prep.flows:
                name = f"{solution.unique_name}_{pf.flow.name}_legacy.zip"
                builder.write_zip(out_dir / "legacy" / name, builder.legacy_package_files(prep, pf))
                legacy_names.append(name)
            print(f"  レガシー パッケージ: {_rel(out_dir / 'legacy')} ({len(legacy_names)} 件)")
        guide_path = out_dir / (zip_path.stem + "_IMPORT.md")
        guide_path.write_text(guide.render(prep, legacy_names), encoding="utf-8")
        print(f"  インポート手順: {_rel(guide_path)}")
        if prep.example_tokens:
            print("  [警告] 例の値で埋めたパラメータがあります: " + ", ".join(sorted(prep.example_tokens)))
    return 1 if failed else 0


def cmd_bump(args, root: Path) -> int:
    d = resolve_solution_dir(root, args.solution)
    path = d / "solution.json"
    manifest = read_json(path)
    old = str(manifest.get("version", "1.0.0.0"))
    if not naming.VERSION_RE.match(old):
        raise ProjectError(f"version '{old}' が 1.0.0.0 形式ではありません")
    manifest["version"] = naming.bump_version(old, args.part)
    write_json(path, manifest)
    print(f"{d.name}: {old} → {manifest['version']}")
    return 0


def cmd_params(args, root: Path) -> int:
    d = resolve_solution_dir(root, args.solution)
    solution = load_solution(d)
    used = set()
    for f in solution.flows:
        used |= params.find_tokens(f.definition)
    local = builder.load_local_params(solution)
    values, missing, _ = params.resolve(solution.params_spec, local, {}, used)
    if not used:
        print("このソリューションはパラメータを使っていません")
        return 0
    for key in sorted(used):
        meta = solution.params_spec.get(key, {})
        status = "未設定" if key in missing else f"= {values[key]!r}"
        print(f"{key}: {status}")
        if meta.get("description"):
            print(f"    {meta['description']}")
        if meta.get("example") is not None:
            print(f"    例: {meta['example']!r}")
    if args.init:
        path = d / "params.local.json"
        doc = dict(local)
        for key in sorted(missing):
            doc.setdefault(key, solution.params_spec.get(key, {}).get("example", ""))
        write_json(path, doc)
        print(f"\n{_rel(path)} を書き出しました。値を自分の環境のものに書き換えてください (Git には入りません)")
    elif missing:
        print(f"\n未設定が {len(missing)} 件あります。`params {d.name} --init` で params.local.json の雛形を作れます")
    return 0


def cmd_import(args, root: Path) -> int:
    target = importer.import_solution_zip(Path(args.zip), root, args.name, args.force)
    print(f"取り込みました: {_rel(target)}")
    print("環境固有の値 (サイト URL・メールアドレスなど) は {{NAME}} に置き換え、solution.json の params に宣言してください")
    return 0


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pabuild", description="Power Automate のフローをコードからビルドする")
    p.add_argument("--version", action="version", version=f"pabuild {__version__}")
    p.add_argument("--root", help="リポジトリのルート (既定: カレントディレクトリ)")
    sub = p.add_subparsers(dest="command", metavar="<command>")

    sub.add_parser("list", help="ソリューションとフローの一覧").set_defaults(func=cmd_list)
    sub.add_parser("templates", help="フロー テンプレートの一覧").set_defaults(func=cmd_templates)

    s = sub.add_parser("new-solution", help="ソリューションの雛形を作る")
    s.add_argument("name", help="一意の名前 (半角英数字。例: SalesAutomation)")
    s.add_argument("--display", default="", help="表示名 (日本語可)")
    s.add_argument("--description", default="")
    s.add_argument("--prefix", default="", help="発行者の接頭辞 (既定: lab)")
    s.add_argument("--publisher", default="", help="発行者の一意の名前 (既定: labpublisher)")
    s.set_defaults(func=cmd_new_solution)

    s = sub.add_parser("new-flow", help="テンプレートからフローを追加する")
    s.add_argument("solution")
    s.add_argument("name", help="フロー名 (半角英数字。例: DailyReport)")
    s.add_argument("--template", "-t", default="manual-hello")
    s.add_argument("--display", default="", help="表示名 (日本語可)")
    s.set_defaults(func=cmd_new_flow)

    def common(sp):
        sp.add_argument("solutions", nargs="*", help="対象 (省略時はすべて)")
        sp.add_argument("--param", "-p", action="append", metavar="NAME=値", help="パラメータを上書き")
        sp.add_argument("--verbose", "-v", action="store_true", help="「情報」レベルも表示")

    s = sub.add_parser("validate", help="ビルドせずに検証だけ行う")
    common(s)
    s.set_defaults(func=cmd_validate)

    s = sub.add_parser("build", help="検証して dist/ にインポート用 zip を作る")
    common(s)
    s.add_argument("--legacy", action="store_true", help="レガシー パッケージ (Dataverse 無し環境向け) も出力")
    s.add_argument("--use-examples", action="store_true", help="未設定のパラメータを example の値で埋める (動作確認・CI 用)")
    s.add_argument("--out", help="出力先 (既定: dist)")
    s.set_defaults(func=cmd_build)

    s = sub.add_parser("bump", help="ソリューションのバージョンを上げる")
    s.add_argument("solution")
    s.add_argument("--part", choices=["major", "minor", "build", "revision"], default="revision")
    s.set_defaults(func=cmd_bump)

    s = sub.add_parser("params", help="必要なパラメータと設定状況を表示する")
    s.add_argument("solution")
    s.add_argument("--init", action="store_true", help="params.local.json の雛形を書き出す")
    s.set_defaults(func=cmd_params)

    s = sub.add_parser("import", help="エクスポートしたソリューション zip をソースに取り込む")
    s.add_argument("zip")
    s.add_argument("--name", default="", help="solutions/ 以下のディレクトリ名 (既定: ソリューションの一意の名前)")
    s.add_argument("--force", action="store_true", help="既存のディレクトリに上書きする")
    s.set_defaults(func=cmd_import)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    root = Path(args.root).resolve() if args.root else _default_root()
    try:
        return args.func(args, root)
    except ProjectError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1
