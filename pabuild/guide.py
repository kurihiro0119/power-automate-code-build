"""ビルドのたびに dist/ に出す「インポート手順書」。"""
from __future__ import annotations

import json
from typing import List

from . import catalog
from .build import Prepared, solution_zip_name


def render(prep: Prepared, legacy_names: List[str]) -> str:
    s = prep.solution
    zip_name = solution_zip_name(s)
    lines: List[str] = [
        f"# {s.display_name} インポート手順",
        "",
        f"- ソリューション: `{s.unique_name}` / バージョン `{s.version}`",
        f"- ファイル: `{zip_name}`",
        f"- フロー数: {len(prep.flows)}",
        "",
        "## 1. インポートする",
        "",
        "1. https://make.powerautomate.com を開き、右上の「環境」でインポート先を選ぶ",
        "2. 左メニュー「ソリューション」→「ソリューションをインポート」",
        f"3. 「参照」で `{zip_name}` を選び「次へ」→「次へ」",
    ]
    if prep.connrefs:
        lines += [
            "4. 「接続」の画面で、下の表のコネクタごとに接続を選ぶ。無ければ「新しい接続」→ サインインして戻り「最新の情報に更新」",
            "5. 「インポート」を押し、完了の通知を待つ (1〜3 分)",
            "",
            "## 2. サインインが必要なコネクタ",
            "",
            "| コネクタ | サインインするアカウント | 区分 |",
            "| --- | --- | --- |",
        ]
        for c in prep.connrefs.values():
            info = catalog.info(c.api_name)
            tier = {"premium": "プレミアム", "standard": "標準"}.get(info.get("tier", ""), "不明")
            lines.append(f"| {catalog.display_name(c.api_name)} (`{c.api_name}`) | {info.get('signIn', '—')} | {tier} |")
    else:
        lines += [
            "4. 「インポート」を押し、完了の通知を待つ (1〜3 分)",
            "",
            "## 2. サインインが必要なコネクタ",
            "",
            "なし (このソリューションはコネクタを使いません)。",
        ]
    lines += ["", "## 3. フローごとの確認", ""]
    for pf in prep.flows:
        f = pf.flow
        state = "オン" if f.state == "on" else "オフ (インポート後に手動でオンにする)"
        lines.append(f"### {f.display_name}")
        lines.append("")
        if f.description:
            lines.append(f.description)
            lines.append("")
        lines.append(f"- 状態: {state}")
        conns = ", ".join(catalog.display_name(a) for a in pf.connections.values()) or "なし"
        lines.append(f"- 使うコネクタ: {conns}")
        if f.post_import:
            lines.append("- インポート後に手でやること:")
            lines += [f"  - [ ] {step}" for step in f.post_import]
        lines.append("- [ ] 「ソリューション」→ このソリューション → フローを開き、テスト実行して成功を確認する")
        lines.append("")
    if prep.used_tokens:
        lines += ["## 4. このビルドに埋め込んだ値", "", "| パラメータ | 値 | 備考 |", "| --- | --- | --- |"]
        for key in sorted(prep.used_tokens):
            val = prep.values.get(key, "(未設定)")
            note = "**例の値のまま。本番では params.local.json で上書きして再ビルド**" if key in prep.example_tokens else ""
            lines.append(f"| `{key}` | `{json.dumps(val, ensure_ascii=False)}` | {note} |")
        lines.append("")
    if legacy_names:
        lines += [
            "## Dataverse の無い環境の場合 (レガシー パッケージ)",
            "",
            "「ソリューション」が使えない環境では、`legacy/` の zip を 1 フローずつ取り込みます。",
            "「マイ フロー」→「インポート」→「パッケージのインポート (レガシー)」→ zip を選ぶ →",
            "フローは「新規作成」、各接続は「インポート時に選択する」で既存の接続を選ぶ →「インポート」。",
            "",
        ]
        lines += [f"- `legacy/{n}`" for n in legacy_names]
        lines.append("")
    lines += [
        "## うまくいかないとき",
        "",
        "リポジトリの `docs/troubleshooting.md` を参照してください。",
        "",
    ]
    return "\n".join(lines)
