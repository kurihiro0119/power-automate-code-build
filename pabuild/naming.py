"""GUID・論理名・ファイル名の決定論的な生成。

同じソース (ソリューション名 + フロー名) からは必ず同じ ID が出るので、
再ビルドして再インポートすると「新規作成」ではなく「更新」になります。
"""
from __future__ import annotations

import hashlib
import re
import uuid

# このリポジトリ固有の名前空間。変更すると全フローの GUID が変わるので触らないこと。
NAMESPACE = uuid.UUID("6f1c2a0e-5d3b-4c58-9a7e-0b1d2c3e4f50")

FLOW_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,79}$")
SOLUTION_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
PREFIX_RE = re.compile(r"^[a-z][a-z0-9]{1,7}$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+\.\d+$")
GUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def flow_guid(solution_unique: str, flow_name: str) -> str:
    return str(uuid.uuid5(NAMESPACE, f"flow:{solution_unique}/{flow_name}"))


def stable_guid(*parts: str) -> str:
    return str(uuid.uuid5(NAMESPACE, "/".join(parts)))


def connref_logical_name(prefix: str, connection_name: str, solution_unique: str) -> str:
    digest = hashlib.sha1(f"{solution_unique}/{connection_name}".encode()).hexdigest()[:5]
    compact = re.sub(r"[^a-z0-9]", "", connection_name.lower())
    return f"{prefix}_{compact}_{digest}"


def workflow_filename(flow_name: str, guid: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9]", "", flow_name) or "Flow"
    return f"{safe}-{guid.upper()}.json"


def version_slug(version: str) -> str:
    return version.replace(".", "_")


def bump_version(version: str, part: str = "revision") -> str:
    nums = [int(x) for x in version.split(".")]
    idx = {"major": 0, "minor": 1, "build": 2, "revision": 3}[part]
    nums[idx] += 1
    for i in range(idx + 1, 4):
        nums[i] = 0
    return ".".join(str(n) for n in nums)
