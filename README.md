# power-automate-code-build

Power Automate のクラウドフローを **JSON で書き → コマンド 1 つでインポート用 zip にし → インポートしてサインインするだけ** で動かすためのテンプレートリポジトリです。

```
solutions/<名前>/flows/*.flow.json   ← フローを書く (人でも AI でも)
        │  python3 -m pabuild build
        ▼
dist/<名前>_1_0_0_0.zip              ← Power Automate の「ソリューションをインポート」に渡す
dist/<名前>_1_0_0_0_IMPORT.md        ← サインインするコネクタと確認手順 (自動生成)
```

Python 3.9 以上だけで動きます (追加のインストールなし。3.10 で動作確認)。

## 3 分で試す

```bash
python3 -m pabuild build LabStarter
```

`dist/LabStarter_1_0_0_0.zip` ができます。これはコネクタを使わない動作確認用ソリューションです。

1. https://make.powerautomate.com →「ソリューション」→「ソリューションをインポート」
2. zip を選び「次へ」→「インポート」
3. ソリューションを開き、フロー「Hello (動作確認用)」をテスト実行

ここまで通れば、この環境で「コード → フロー」の経路が使えます。通らないときは [docs/troubleshooting.md](docs/troubleshooting.md) へ。

> ソリューションのインポートには **Dataverse のある環境** が必要です。無い環境では `build --legacy` で
> レガシー パッケージを作れます ([docs/import-guide.md](docs/import-guide.md))。

## 自分のフローを作る

```bash
# 1. ソリューション (フローの入れ物) を作る
python3 -m pabuild new-solution SalesAutomation --display "営業の自動化"

# 2. テンプレートからフローを追加する (一覧は `python3 -m pabuild templates`)
python3 -m pabuild new-flow SalesAutomation DailyMail -t scheduled-mail-office365 --display "毎朝の連絡"

# 3. 環境ごとの値 (宛先など) を設定する。params.local.json は Git に入りません
python3 -m pabuild params SalesAutomation --init
#    → solutions/SalesAutomation/params.local.json を編集

# 4. ビルド
python3 -m pabuild build SalesAutomation
```

あとは `solutions/SalesAutomation/flows/DailyMail.flow.json` を編集してビルドし直すだけです。
フローの ID はソリューション名とフロー名から決まるので、**再インポートすると同じフローが更新されます** (重複しません)。

### AI に作らせる

Claude などに、このリポジトリを開いた状態で次のように頼みます。ルールは [CLAUDE.md](CLAUDE.md) (= AGENTS.md) にまとめてあります。

> SalesAutomation に「SharePoint の『問い合わせ』リストに項目が追加されたら、担当者にメールして Teams にも投稿する」フローを追加して、ビルドまで通して。

AI は `templates/flows/` を見本にフローを書き、`validate` のエラーが無くなるまで直します。

## テンプレート

| ID | 内容 | コネクタ |
| --- | --- | --- |
| `manual-hello` | 手動実行して文字列を組み立てる (経路の動作確認) | なし |
| `powerapps-local-lookup` | Power Apps / Copilot から呼ぶ検索 (データ埋め込み) | なし |
| `powerapps-sharepoint-lookup` | Power Apps / Copilot から呼ぶ SharePoint リスト検索 | SharePoint |
| `scheduled-mail-office365` | 毎朝 9 時にメール | Office 365 Outlook |
| `scheduled-mail-outlookcom` | 毎朝 9 時にメール (個人アカウント) | Outlook.com |
| `sharepoint-new-item-to-teams` | リストの新規項目を Teams に投稿 | SharePoint, Teams |
| `forms-response-to-sharepoint` | Forms の回答をリストに記録 | Forms, SharePoint |
| `mail-attachments-to-onedrive` | メールの添付を OneDrive に保存 | Office 365 Outlook, OneDrive for Business |
| `approval-basic` | 承認依頼 → 結果をメール | Approvals, Office 365 Outlook |
| `try-catch-notify` | エラー処理の型 (Try / Catch スコープ) | Office 365 Outlook |
| `due-soon-to-teams` | 平日朝、期限が近い未完了タスクを Teams にまとめて投稿 | SharePoint, Teams |

各テンプレートの `template.verified` に検証状況を書いています。**実環境でインポートして動いたら、そこを更新してください** (動いた JSON が次の見本になります)。

## コマンド

| コマンド | 内容 |
| --- | --- |
| `build [名前...] [--legacy] [-p NAME=値]` | 検証して `dist/` に zip と手順書を出力 |
| `validate [名前...] [-v]` | 検証だけ |
| `new-solution <名前>` / `new-flow <ソリューション> <名前> -t <テンプレート>` | 雛形を作る |
| `templates` / `list` | テンプレート / ソリューションの一覧 |
| `params <名前> [--init]` | 必要なパラメータと設定状況 |
| `bump <名前>` | バージョンを上げる |
| `import <zip>` | エクスポートしたソリューション zip をソースに取り込む |

すべて `python3 -m pabuild <コマンド>` で実行します (`make build` などの短縮もあります)。

## ディレクトリ

```
solutions/            ソース。1 ディレクトリ = 1 ソリューション
  LabStarter/         サインイン不要の動作確認用
  M365Starter/        Outlook / SharePoint / Teams のサンプル
templates/flows/      フローのテンプレート (new-flow の元、AI の見本)
pabuild/              ビルドツール本体 (標準ライブラリのみ)
  data/connectors.json  コネクタのカタログ (表示名・operationId)
docs/                 書き方・インポート手順・トラブル対応
tests/                ツールのテスト (python3 -m unittest)
dist/                 ビルド成果物 (Git 管理外)
```

## ドキュメント

- [docs/flow-authoring.md](docs/flow-authoring.md) — `*.flow.json` の書き方と部品集
- [docs/import-guide.md](docs/import-guide.md) — インポートの手順 (ソリューション / レガシー)
- [docs/troubleshooting.md](docs/troubleshooting.md) — うまくいかないとき
- [CLAUDE.md](CLAUDE.md) — AI にフローを書かせるときのルール
# power-automate-code-build
