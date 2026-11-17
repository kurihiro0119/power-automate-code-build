# うまくいかないとき

## ビルド / 検証で止まる

| メッセージ | 対処 |
| --- | --- |
| `{{NAME}} の値がありません` | `python3 -m pabuild params <名前> --init` で `params.local.json` を作って値を入れる |
| `{{NAME}} が solution.json の params に宣言されていません` | `solution.json` の `params` に追加する |
| `runAfter の 'X' が見つかりません` | 名前の綴りを確認。別の階層 (スコープやループの内外) のアクションは指定できない |
| `outputs('X') の 'X' というアクションはありません` | 式の中のアクション名を実際のキーと一致させる (空白は `_`) |
| `アクション名 'X' が重複しています` | 入れ子の中も含めてフロー全体で一意にする |
| `JSON の文法エラー` | 行・列が表示されるので、カンマや引用符を確認する |

`[情報] operationId 'X' はカタログに未掲載` はエラーではありません。綴りに自信があれば無視できます。

## インポートで失敗する

| 症状 | 原因と対処 |
| --- | --- |
| 「ソリューション」メニューでインポートできない / Dataverse が必要と出る | 環境に Dataverse が無い。追加するか `build --legacy` を使う ([import-guide.md](import-guide.md)) |
| 「接続」の画面で接続を作れない | そのコネクタを使えるライセンス/アカウントが無い (例: メールボックスの無いテナントで Office 365 Outlook)。個人アカウント用のコネクタ (Outlook.com など) に差し替える |
| インポートは成功したがフローがオフ | 接続が未設定のときに起きる。フローを開いて接続を選び、オンにする |
| `InvalidOpenApiFlow` / `...operation 'X' ... not found` | `operationId` の誤り。デザイナーで同じアクションを作ってコード ビューで正しい ID を確認する |
| `...parameter 'X' is missing` / `is not valid` | `parameters` のキー名の誤りか必須項目の不足。同上の方法で実物と見比べる |
| `WorkflowRunActionInputsInvalidProperty` / `The template validation failed` | 式の文法エラーが多い。エラー文にアクション名が出るので、その `inputs` の式 (引用符・括弧) を確認する |
| 発行者 (publisher) の競合エラー | 同じ `uniqueName` で `prefix` の違う発行者が環境に既にある。`solution.json` の `publisher` を環境側に合わせる |
| デザイナーでサイトやリストが「カスタム値」と表示される | 動作に支障はない。気になるならドロップダウンから選び直して保存する |

エラー文は「ソリューション」→「履歴」→ 該当の行 →「詳細」で全文を確認できます。
AI に直させるときは、その全文と該当の `*.flow.json` を渡してください。

## 実行すると失敗する

- 401 / 403: 接続したアカウントに、対象 (サイト・チーム・メールボックス) への権限が無い
- 404: `params.local.json` の URL・リスト名・ID が違う
- プレミアムのエラー: HTTP や Dataverse コネクタは Power Automate Free では実行できない (`validate` が警告を出します)

## キャンバス アプリ (Power Apps)

- `new-app` で「ベースの .msapp が登録されていません」: [app-authoring.md](app-authoring.md) の手順で `app-base` を先に実行する
- `app-base` で「古い形式の .msapp」: Studio で開いて保存し直し、もう一度エクスポートする (`Src/*.pa.yaml` を含む形式が必要)
- `validate` の「値は Power Fx の数式として = で始めてください」: `Text: こんにちは` ではなく `Text: ="こんにちは"`
- インポートは通ったが画面がベースのまま / 空: Studio で開き直す。直らなければ `.msapp` を Studio の
  「開く」→「参照」で直接開いて表示を確かめ、結果を `templates/apps/bases/<id>/base.json` の `verified` に書く
- Studio で開くと ⚠ (数式エラー): 関数名・列名・データ ソース名を確認する。データ ソースが無いエラーなら、
  ベースにそのデータ ソースを追加してから取り直す (pa.yaml だけでは追加できない)

## 動いたら

- テンプレートの `template.verified` を「YYYY-MM インポート・実行確認済み」に更新する
- 新しく確認できた `operationId` を `pabuild/data/connectors.json` に追記する

動いた JSON を見本として増やしていくほど、次の自動生成の精度が上がります。
