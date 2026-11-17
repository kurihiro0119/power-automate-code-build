# AI エージェント向けガイド

このリポジトリでは、Power Automate のクラウドフローを `solutions/<ソリューション>/flows/<Name>.flow.json` に書き、
`python3 -m pabuild build` でインポート用 zip を作る。利用者は zip をインポートして各コネクタにサインインするだけ、が目標。

## フローを追加・変更するときの手順

1. `python3 -m pabuild templates` で近いテンプレートを探し、`templates/flows/<id>.flow.json` を読む
2. `python3 -m pabuild new-flow <ソリューション> <FlowName> -t <id> --display "<日本語名>"` で雛形を作る
   (ソリューションが無ければ先に `new-solution`)
3. `*.flow.json` の `definition` を編集する。書式は `docs/flow-authoring.md`
4. 環境ごとに変わる値は `{{UPPER_SNAKE}}` にして、`solution.json` の `params` に
   `{"description": "...", "example": "..."}` を宣言する
5. 手作業が残る場合は `postImport` に 1 行ずつ書く (インポート手順書に載る)
6. `python3 -m pabuild validate <ソリューション> -v` がエラー 0 になるまで直す
7. `python3 -m pabuild build <ソリューション> --use-examples` が通ることを確認する
   (利用者の `params.local.json` があれば `--use-examples` は不要)
8. `python3 -m unittest -q` が通ることを確認する

## 守ること

- `dist/` と zip の中身を直接編集しない。必ずソースを直してビルドする
- `solution.xml` / `customizations.xml` / GUID / 接続参照は自動生成。手で書かない
- フローの `name` は半角英数字 (例: `DailyReport`)。日本語は `displayName` に書く。
  **`name` を変えるとフローの GUID が変わり、インポート時に別フローとして作られる**
- 既存フローに `id` が書かれていたら消さない (エクスポート元のフローと対応している)
- メールアドレス・サイト URL・チーム ID・フォーム ID などを `*.flow.json` に直書きしない (`{{...}}` にする)。
  `params.local.json` はコミットしない。
  `solutions/` は公開サンプル (LabStarter / ExpenseDesk / M365Starter / DueSoon) 以外 Git 管理外。
  実案件を `.gitignore` の例外に足さない
- コネクタを使うアクション/トリガーは `type: OpenApiConnection` 系で、`inputs.host` に `connectionName` と
  `operationId` を書く。`apiId` と `authentication` は省略する (ビルドが補う)
- `operationId` と `parameters` のキーを推測で作らない。優先順位:
  1. `templates/flows/` と `solutions/` にある実例
  2. `pabuild/data/connectors.json` にある operationId
  3. 利用者にデザイナーで 1 回作ってもらい、`python3 -m pabuild import <エクスポート zip>` で取り込んだ実物
  どれにも無い操作を使うときは、未検証であることを利用者に伝える
- アクション名はフロー全体で一意、空白は `_`。`runAfter` は同じ階層のアクションだけ指定できる
- 「変数を初期化する」(`InitializeVariable`) は最上位にだけ置く
- プレミアム (HTTP、Dataverse コネクタなど) は利用者が求めたときだけ使う。`validate` が警告を出す
- 新しいコネクタや操作を実環境で確認できたら `pabuild/data/connectors.json` に追記し、
  汎用的なフローは `templates/flows/` に追加する (`template.verified` に確認状況を書く)

## キャンバス アプリ (Power Apps) を追加・変更するとき

書式は `docs/app-authoring.md`。ソースは `solutions/<ソリューション>/apps/<Name>/app.json` と `Src/*.pa.yaml` (公式 pa.yaml v3)。

1. `python3 -m pabuild app-templates` でテンプレートと登録済みベースを確認する。
   **ベースが 0 件なら作れない**: 利用者に「空のキャンバス アプリ入りソリューションをエクスポートして
   `python3 -m pabuild app-base <zip> --id <id>` を実行」してもらう (ベースを推測で作らない)
2. `python3 -m pabuild new-app <ソリューション> <AppName> -t <テンプレート> --display "<日本語名>"` で雛形を作る
3. `Src/*.pa.yaml` を編集する。プロパティの値は必ず `=` で始まる Power Fx。コントロール名はアプリ全体で一意
4. データ ソース (SharePoint・Dataverse・フローなど) は pa.yaml に追加できない。ベースの `base.json` の
   `dataSources` にあるものだけを使い、無ければベースの取り直しを利用者に頼む
5. `validate -v` → `build` → `python3 -m unittest -q` (フローと同じ)

- `.msapp`・`base/`・`templates/apps/bases/` の中身は手で編集しない
- アプリの `name` を変えるとスキーマ名が変わり別アプリになる。`schemaName` が書かれていたら消さない
- `validate` は数式の中身 (関数名・列名) を検査しない。使う関数・プロパティは `templates/apps/` の実例か
  Microsoft の公式ドキュメントにあるものに限り、インポート後に Studio で ⚠ が無いか確認するよう利用者に伝える

## 式でよく間違える点

- 文字列に式を埋めるときは `@{...}`、値全体が式のときは `@...` (型が保たれる)
- 式の中の文字列はシングルクォート。シングルクォート自体は `''` と重ねる
- 手動トリガーの入力は `triggerBody()?['text']`、コネクタ トリガーの値は `triggerOutputs()?['body/列名']`
- アクションの出力は `outputs('Name')?['body/...']` または `body('Name')?['...']`、ループ内の現在の要素は `items('ループ名')`
- 日本時間は `convertFromUtc(utcNow(), 'Tokyo Standard Time', 'yyyy-MM-dd HH:mm')`
