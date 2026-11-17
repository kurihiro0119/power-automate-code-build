# アプリのベース (.msapp)

キャンバス アプリをビルドするための「本物の .msapp」を置く場所です。1 ディレクトリ = 1 ベース。

```
<id>/base.msapp       Power Apps Studio で保存したアプリ
<id>/base.json        形 (Phone/Tablet)・使えるデータ ソース・接続参照・検証状況
<id>/canvasapp.xml    ソリューション内のアプリ定義 (ソリューション zip から登録したときだけ)
<id>/background.png   アプリのタイル画像 (同上)
```

登録は手で置かずに `python3 -m pabuild app-base <zip|msapp> --id <id>` で行います。手順は `docs/app-authoring.md`。
