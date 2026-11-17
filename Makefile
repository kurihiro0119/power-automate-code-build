PY ?= python3

.PHONY: build legacy validate test templates list

build:        ## 検証して dist/ にソリューション zip を作る
	$(PY) -m pabuild build

legacy:       ## レガシー パッケージ (Dataverse 無し環境向け) も作る
	$(PY) -m pabuild build --legacy

validate:     ## 検証だけ
	$(PY) -m pabuild validate -v

test:         ## ツール自体のテスト
	$(PY) -m unittest -q

templates:    ## フロー テンプレートの一覧
	$(PY) -m pabuild templates

list:         ## ソリューションとフローの一覧
	$(PY) -m pabuild list
