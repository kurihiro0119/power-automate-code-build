# キャンバス アプリの書き方 (`apps/<Name>/Src/*.pa.yaml`)

Power Apps のキャンバス アプリも、フローと同じソリューション zip に入れてビルドできます。
画面とコントロールは Microsoft 公式のソースコード形式 **pa.yaml (スキーマ v3)** で書きます。

```
solutions/<ソリューション>/apps/<Name>/
  app.json              名前・表示名・使うベース
  Src/App.pa.yaml       アプリ全体 (StartScreen, Formulas, OnStart など)
  Src/<画面名>.pa.yaml   1 画面 = 1 ファイル
  Src/Components/*.pa.yaml  コンポーネント (任意)
  base/                 このアプリ専用のベース (import したアプリだけ)
```

## 仕組みと「ベース」

`.msapp` の中身 (コントロールの内部 JSON・テーマ・データ ソースの定義) はゼロからは作れません。
そこで、Power Apps Studio で **一度だけ** 作って保存したアプリを「ベース」として登録し、
ビルド時にその中の `Src/*.pa.yaml` だけを差し替えます。

```
ベース .msapp − 元の Src/*.pa.yaml + このリポジトリの Src/*.pa.yaml + packed.json (LoadFromYaml: true)
  = 新しい .msapp → ソリューション zip の CanvasApps/ に入る
```

これは Microsoft の `pac canvas pack --layout SourceCode` と同じ方式です
(`packed.json` の `LoadFromYaml` で、Studio / サーバーに pa.yaml から画面を組み立て直させる)。

**データ ソース (SharePoint リスト・Dataverse テーブル・フローなど) は pa.yaml に書けません。**
使いたいデータ ソースは、ベースを作るときに Studio の「データ」で追加しておきます。

## 最初に 1 回: ベースを登録する

1. https://make.powerapps.com →「ソリューション」→ 任意のソリューション (例: `PowerAppsBase`) を作る
2. そのソリューションで「新規」→「アプリ」→「キャンバス アプリ」→ 形式 (タブレット / 電話) を選んで作成
3. 使うデータ ソースがあれば「データ」→「データの追加」で追加する (後から増やすときはベースを取り直す)
4. 何も置かずに「保存」→ 戻って、ソリューションを「エクスポート」(アンマネージド) → zip をダウンロード
5. 登録する

```bash
python3 -m pabuild app-base ~/Downloads/PowerAppsBase_1_0_0_1.zip --id blank-tablet --title "空のタブレット アプリ"
```

`templates/apps/bases/blank-tablet/` に `base.msapp` と、ソリューション内のアプリ定義 (`canvasapp.xml`)・
接続参照の情報 (`base.json`) が保存されます。`python3 -m pabuild app-templates` で一覧できます。

> `.msapp` 単体 (Studio の「名前を付けて保存」→「このコンピューター」) も登録できますが、
> その場合ソリューション内のアプリ定義を推測で作るため `validate` が警告を出します。ソリューション zip を推奨します。
>
> ベースの `.msapp` には追加したデータ ソースの接続先 (サイト URL など) が含まれます。
> 公開リポジトリに置くときは注意してください。

## アプリを追加する

```bash
python3 -m pabuild app-templates                        # テンプレートとベースの一覧
python3 -m pabuild new-app MySolution Catalog -t local-list-detail --display "備品カタログ"
python3 -m pabuild validate MySolution -v
python3 -m pabuild build MySolution
```

ベースが 1 つだけなら `--base` は省略できます。`--base-file <zip>` でそのアプリ専用のベースを取り込むこともできます。

### `app.json`

```json
{
  "name": "Catalog",
  "displayName": "備品カタログ",
  "description": "一覧と詳細",
  "base": "blank-tablet",
  "postImport": ["インポート後に手でやることがあれば 1 行ずつ"]
}
```

| キー | 内容 |
| --- | --- |
| `name` | 半角英数字。アプリのスキーマ名 (`<prefix>_<name>_xxxxx`) の元。**変えると別アプリ扱い** |
| `displayName` | Power Apps に表示される名前 (日本語可) |
| `base` | `templates/apps/bases/<id>` の ID。省略すると `apps/<Name>/base/` を使う |
| `schemaName` | 省略可。`import` で取り込んだアプリに付く元のスキーマ名 (消さない) |
| `backgroundColor` | 省略可。アプリ一覧のタイルの色 (`RGBA(0,176,240,1)`) |

## pa.yaml の書き方

```yaml
Screens:
  HomeScreen:
    Properties:
      Fill: =RGBA(245, 246, 250, 1)
    Children:
      - TitleLabel:
          Control: Label
          Properties:
            Text: ="こんにちは"
            X: =40
            Y: =40
            Width: =Parent.Width - 80
            Height: =60
      - GoButton:
          Control: Classic/Button
          Properties:
            Text: ="次へ"
            OnSelect: =Navigate(DetailScreen, ScreenTransition.Cover)
```

- **プロパティの値はすべて `=` で始まる Power Fx の数式**。文字列は `="..."`、数値は `=100`、真偽値は `=true`
- 複数行の数式は `|-` のブロックにして、1 行目を `=` で始める
- コントロール名はアプリ全体で一意 (画面名とも重ならない)。`Children` は `- 名前:` の並び (後ろほど手前に表示)
- インデントは半角スペース 2 つ。タブ・アンカー (`&`/`*`)・複数行の `[ ... ]` は使わない (このツールの読み込み対象外)
- 環境ごとに変わる値は `{{UPPER_SNAKE}}` にして `solution.json` の `params` に宣言できる (フローと同じ)
- `_EditorState.pa.yaml` (画面の並び順) は省略可。無ければ StartScreen を先頭にファイル名順で補う

### よく使うコントロール

| `Control:` | 用途 | 主なプロパティ |
| --- | --- | --- |
| `Label` | 文字表示 (クラシック) | `Text`, `Size`, `FontWeight`, `Color`, `Align`, `VerticalAlign` |
| `Classic/Button` | ボタン | `Text`, `OnSelect`, `DisplayMode` |
| `Classic/TextInput` | 入力欄 | `Default`, `HintText`, `Mode` (`TextMode.MultiLine`) → 値は `名前.Text` |
| `Classic/DropDown` | ドロップダウン | `Items`, `Default` → 値は `名前.Selected.Value` |
| `Gallery` | 一覧 (`Variant: BrowseLayout_Vertical_OneTextVariant_ver5.0` など) | `Items`, `TemplateSize`, `OnSelect`、子は `ThisItem.列` / 選択は `名前.Selected` |
| `Image` | 画像 | `Image` |
| `Rectangle` | 図形 | `Fill` |
| `GroupContainer` | コンテナー (`Variant: AutoLayout` / `ManualLayout`) | `LayoutDirection`, `PaddingTop` など |
| `Text` / `Button` / `TextInput` | モダン コントロール (Fluent) | アプリ設定でモダン コントロールが有効な場合 |

`Label@2.5.1` のようにバージョンを付けることもできます (省略すると Studio の既定)。
`App.pa.yaml` の `App.Properties` には `StartScreen`・`OnStart`・`Formulas` (名前付き数式) などを書きます。

公式スキーマ: https://raw.githubusercontent.com/microsoft/PowerApps-Tooling/master/schemas/pa-yaml/v3.0/pa.schema.yaml

### フローを呼ぶ

1. 呼ばれるフローを先にこのリポジトリで作って (例: テンプレート `powerapps-local-lookup`) インポートする
2. Studio でベース用アプリを開き「データ」(または「Power Automate」) からそのフローを追加 → 保存 → エクスポート → `app-base` で登録
3. pa.yaml では `Set(varResult, LocalLookup.Run(CodeInput.Text))` のように呼ぶ (テンプレート `flow-lookup`)

フローの GUID はソリューション名 + フロー名から決まるので、ビルドし直してもベースからの参照は切れません。

## 既存アプリを取り込む

Studio で作ったアプリをコード管理に移すときは、アプリ入りのソリューションをエクスポートして:

```bash
python3 -m pabuild import ~/Downloads/MySolution_1_0_0_1.zip
```

`apps/<Name>/Src/*.pa.yaml` (ソース) と `apps/<Name>/base/` (そのアプリ自身のベース) ができます。
スキーマ名を保持するので、ビルドして再インポートすると **元のアプリが更新** されます。

## 検証でわかること / わからないこと

`validate` が見るのは、YAML の構造・`=` 始まり・コントロール名の重複・`Control` の書式・StartScreen の存在などです。
**数式の中身 (関数名・列名・データ ソース名の誤り) はチェックしません**。インポート後に Studio で開き、
右上の ⚠ (アプリ チェッカー) でエラーが無いことを確かめてから「公開」してください。

> この仕組み (pa.yaml を差し替えた .msapp をソリューションでインポート) は、2026-11 時点で実環境での確認が
> まだです。初めて動かせたら `templates/apps/*/template.json` と `templates/apps/bases/*/base.json` の
> `verified` を更新してください。
