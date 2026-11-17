# フローの書き方 (`*.flow.json`)

1 ファイル = 1 フローです。`solutions/<ソリューション>/flows/<Name>.flow.json` に置きます。

```json
{
  "name": "DailyMail",
  "displayName": "毎朝のメール通知",
  "description": "日本時間の毎朝 9 時にメールを送る。",
  "state": "on",
  "definition": {
    "triggers": { "...": {} },
    "actions": { "...": {} }
  },
  "postImport": ["インポート後に手でやることがあれば 1 行ずつ"]
}
```

| キー | 内容 |
| --- | --- |
| `name` | 半角英数字。フローの ID (GUID) とファイル名の元になる。**変えると別フロー扱い** |
| `displayName` | Power Automate に表示される名前 (日本語可) |
| `state` | `on` / `off`。`off` ならオフの状態でインポートされる |
| `id` | 省略可。`import` で取り込んだフローに付く、元のフローの GUID |
| `definition` | トリガーとアクション。Power Automate のコード ビューと同じ形式 |
| `postImport` | 手作業が残るときの手順。`dist/*_IMPORT.md` に載る |

## 書かなくてよいもの (ビルドが補う)

- `definition.$schema` / `contentVersion` / `parameters` (`$connections`, `$authentication`)
- コネクタ アクションの `inputs.host.apiId` と `inputs.authentication`
- 先頭アクションの `"runAfter": {}`
- 接続参照 (`connectionReferences`)、`solution.xml`、`customizations.xml`、GUID

逆に、デザイナーのコード ビューからコピーした JSON はそのまま貼れます (上の項目が付いていても問題ありません)。

## パラメータ `{{NAME}}`

環境ごとに変わる値は `{{SP_SITE_URL}}` のように書き、`solution.json` に宣言します。

```json
"params": {
  "SP_SITE_URL": { "description": "SharePoint サイトの URL", "example": "https://contoso.sharepoint.com/sites/demo" },
  "ONEDRIVE_FOLDER": { "description": "保存先", "default": "/MailAttachments" }
}
```

値の優先順位は `default` < `params.local.json` < `--param NAME=値` です。
`example` は説明用で、`build --use-examples` のときだけ使われます (CI・動作確認用)。
値全体が `"{{N}}"` のときは、数値や真偽値の型がそのまま入ります。

## トリガー (1 フローに 1 つ)

手動 (ボタン)。`kind` を `PowerAppV2` にすると Power Apps / Copilot から呼べます。

```json
"manual": {
  "type": "Request",
  "kind": "Button",
  "inputs": { "schema": { "type": "object", "properties": {
    "text": { "title": "メッセージ", "type": "string", "x-ms-dynamically-added": true, "x-ms-content-hint": "TEXT" }
  }, "required": ["text"] } }
}
```

入力は `text`, `text_1`, `text_2` … (数値は `number`, 真偽は `boolean`) というキーにし、`triggerBody()?['text']` で読みます。

スケジュール:

```json
"Recurrence": {
  "type": "Recurrence",
  "recurrence": { "frequency": "Day", "interval": 1, "timeZone": "Tokyo Standard Time",
                  "schedule": { "hours": ["9"], "minutes": [0] } }
}
```

`frequency` は `Minute` / `Hour` / `Day` / `Week` / `Month`。平日だけなら `schedule` に `"weekDays": ["Monday", ...]` (`frequency` は `Week`)。

コネクタのトリガー (ポーリング型の例: SharePoint):

```json
"When_an_item_is_created": {
  "type": "OpenApiConnection",
  "recurrence": { "frequency": "Minute", "interval": 5 },
  "splitOn": "@triggerOutputs()?['body/value']",
  "inputs": {
    "host": { "connectionName": "shared_sharepointonline", "operationId": "GetOnNewItems" },
    "parameters": { "dataset": "{{SP_SITE_URL}}", "table": "{{SP_LIST}}" }
  }
}
```

通知型 (Forms は `OpenApiConnectionWebhook`、Outlook の新着メールは `OpenApiConnectionNotification`) は `recurrence` が不要です。`templates/flows/` に実例があります。

## アクション

実行順は `runAfter` で決まります (JSON 内の並び順ではありません)。

```json
"Step_B": {
  "runAfter": { "Step_A": ["Succeeded"] },
  "type": "Compose",
  "inputs": "@outputs('Step_A')"
}
```

- `runAfter` を省略する (または `{}`) と、その階層の先頭
- 状態は `Succeeded` / `Failed` / `Skipped` / `TimedOut`。`["Failed", "TimedOut"]` で「失敗したら実行」
- 指定できるのは **同じ階層** のアクションだけ
- アクション名はフロー全体で一意。空白は `_`

### コネクタ

```json
"Send_an_email": {
  "type": "OpenApiConnection",
  "inputs": {
    "host": { "connectionName": "shared_office365", "operationId": "SendEmailV2" },
    "parameters": {
      "emailMessage/To": "{{MAIL_TO}}",
      "emailMessage/Subject": "件名",
      "emailMessage/Body": "<p>本文</p>"
    }
  }
}
```

`connectionName` がコネクタの API 名です (一覧は `pabuild/data/connectors.json`)。同じ `connectionName` を使うアクションは
ソリューション内で 1 つの接続参照にまとまるので、**利用者のサインインはコネクタごとに 1 回** で済みます。
同じコネクタに別アカウントで 2 つ接続したいときは、`"connectionName": "shared_office365_2"` と
`"apiId": "/providers/Microsoft.PowerApps/apis/shared_office365"` を両方書きます。

### データ操作と変数

```json
"Init_count":  { "type": "InitializeVariable", "inputs": { "variables": [ { "name": "count", "type": "integer", "value": 0 } ] } },
"Add_one":     { "type": "IncrementVariable", "inputs": { "name": "count", "value": 1 } },
"Set_text":    { "type": "SetVariable", "inputs": { "name": "msg", "value": "@{variables('count')} 件" } },
"Filter":      { "type": "Query",  "inputs": { "from": "@body('Get_items')?['value']", "where": "@equals(item()?['Status'], '未対応')" } },
"Pick_titles": { "type": "Select", "inputs": { "from": "@body('Filter')", "select": { "title": "@item()?['Title']" } } },
"Join_lines":  { "type": "Join",   "inputs": { "from": "@body('Pick_titles')", "joinWith": "\n" } },
"Parse":       { "type": "ParseJson", "inputs": { "content": "@outputs('X')", "schema": { "type": "object" } } }
```

(上は 1 行に詰めた抜粋です。実際は各アクションに `runAfter` を付けます。)
変数の `type` は `string` / `integer` / `float` / `boolean` / `array` / `object`。「変数を初期化する」は最上位にだけ置けます。

### 条件・ループ・スコープ

```json
"Condition": {
  "type": "If",
  "expression": { "and": [ { "greater": ["@length(body('Filter'))", 0] } ] },
  "actions": { "...はいの場合": {} },
  "else": { "actions": { "...いいえの場合": {} } }
},
"Apply_to_each": {
  "type": "Foreach",
  "foreach": "@body('Filter')",
  "actions": { "Use_item": { "type": "Compose", "inputs": "@items('Apply_to_each')?['Title']" } }
},
"Switch": {
  "type": "Switch",
  "expression": "@triggerBody()?['text']",
  "cases": { "Case_A": { "case": "A", "actions": {} } },
  "default": { "actions": {} }
},
"Try": { "type": "Scope", "actions": {} }
```

条件の演算子は `equals` / `greater` / `greaterOrEquals` / `less` / `lessOrEquals` / `contains` / `startsWith` / `endsWith`、
否定は `{ "not": { "equals": [...] } }`、組み合わせは `and` / `or`。
エラー処理の型は `templates/flows/try-catch-notify.flow.json` を見てください。

## 式の早見表

| やりたいこと | 式 |
| --- | --- |
| 手動トリガーの入力 | `triggerBody()?['text']` |
| コネクタ トリガーの列 | `triggerOutputs()?['body/Title']` |
| アクションの出力 | `outputs('Name')?['body/value']` / `body('Name')?['value']` |
| ループ中の要素 | `items('Apply_to_each')?['Title']` |
| 変数 | `variables('count')` |
| 日本時間の現在 | `convertFromUtc(utcNow(), 'Tokyo Standard Time', 'yyyy-MM-dd HH:mm')` |
| 空なら既定値 | `coalesce(値, '既定値')` |
| 件数 | `length(body('Filter'))` |

- 文字列に埋め込むときは `"件名: @{式}"`、値全体が式なら `"@式"` (型が保たれる)
- 式の中の文字列はシングルクォート。`'` 自体は `''`
- 先頭が `@` の普通の文字列を書きたいときは `@@`

## 知らないコネクタ・操作を使いたいとき

`operationId` や `parameters` のキーは推測せず、実物から取ります。

1. デザイナーでそのアクションを 1 つ置いたフローを作り、ソリューションに入れてエクスポートする
2. `python3 -m pabuild import <エクスポートした zip> --name Scratch` で取り込む
3. `solutions/Scratch/flows/*.flow.json` から該当アクションをコピーする
4. 確認できた `operationId` を `pabuild/data/connectors.json` に追記する

1 アクションだけなら、デザイナーでアクションの「…」→「コード ビュー」を開いてコピーしても同じ JSON が取れます。
