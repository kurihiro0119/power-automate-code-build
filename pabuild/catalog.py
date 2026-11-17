"""コネクタのカタログ (表示名・ライセンス区分・よく使う operationId)。

網羅はしていません。未掲載のコネクタ/操作も使えます (検証で「情報」として知らせるだけ)。
追記は pabuild/data/connectors.json へ。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

DATA = Path(__file__).parent / "data" / "connectors.json"


@lru_cache(maxsize=1)
def load() -> Dict[str, Dict[str, Any]]:
    with DATA.open(encoding="utf-8") as f:
        return json.load(f)


def display_name(api_name: str) -> str:
    entry = load().get(api_name)
    if entry:
        return entry["displayName"]
    base = api_name[len("shared_"):] if api_name.startswith("shared_") else api_name
    return base


def info(api_name: str) -> Dict[str, Any]:
    return load().get(api_name, {})
