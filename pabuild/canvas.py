"""キャンバス アプリ (Power Apps): ソース (app.json + Src/*.pa.yaml) → .msapp → ソリューションの部品。

仕組み (Microsoft 公式の `pac canvas pack --layout SourceCode` と同じ考え方):

  ベース .msapp (Power Apps Studio で一度保存した本物)
    − 元の Src/*.pa.yaml
    + このリポジトリの Src/*.pa.yaml
    + packed.json {"LoadConfiguration": {"LoadFromYaml": true}}
  = 新しい .msapp  (Studio / サーバーは Src の pa.yaml から画面を組み立て直す)

コントロールの内部 JSON (Controls/*.json) はゼロからは作れないので、ベースは必ず実物を使います。
データ ソース (SharePoint リスト・フローなど) も pa.yaml には書けず、ベースに入っているものだけが使えます。
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import struct
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from xml.sax.saxutils import escape

from . import naming, params, payaml
from .project import ProjectError, read_json, write_json

PACKING_CLIENT = "pabuild"
APP_NAME_RE = naming.FLOW_NAME_RE
# pac (MsappPackingService) が受け付ける最小のバージョン。これより古い msapp には Src が無い
MIN_STRUCTURE_VERSION = (2, 4, 0)
MIN_DOC_VERSION = (1, 348)
DROP_FROM_BASE = re.compile(r"^(src[\\/].*|packed\.json|checksum\.json)$", re.IGNORECASE)
API_PREFIX = "/providers/microsoft.powerapps/apis/"


# --------------------------------------------------------------------------- ベース


@dataclass
class Base:
    """ベース .msapp と、ソリューションから取り込んだ場合の付属情報。"""
    dir: Path
    id: str
    meta: Dict[str, Any]
    msapp: bytes
    canvasapp_xml: Optional[str]  # エクスポートした customizations.xml の <CanvasApp> 要素
    background: Optional[bytes]

    @property
    def data_sources(self) -> List[str]:
        return list(self.meta.get("dataSources") or [])

    @property
    def connection_references(self) -> List[Dict[str, str]]:
        return list(self.meta.get("connectionReferences") or [])


def bases_dir(root: Path) -> Path:
    return root / "templates" / "apps" / "bases"


def load_base(base_dir: Path, base_id: str = "") -> Base:
    msapp = base_dir / "base.msapp"
    if not msapp.is_file():
        raise ProjectError(f"{base_dir}: base.msapp がありません")
    meta_path = base_dir / "base.json"
    meta = read_json(meta_path) if meta_path.is_file() else {}
    xml_path = base_dir / "canvasapp.xml"
    bg = base_dir / "background.png"
    return Base(
        dir=base_dir,
        id=base_id or base_dir.name,
        meta=meta,
        msapp=msapp.read_bytes(),
        canvasapp_xml=xml_path.read_text(encoding="utf-8") if xml_path.is_file() else None,
        background=bg.read_bytes() if bg.is_file() else None,
    )


def list_bases(root: Path) -> List[Base]:
    d = bases_dir(root)
    if not d.is_dir():
        return []
    return [load_base(p) for p in sorted(d.iterdir()) if (p / "base.msapp").is_file()]


def _version_tuple(text: str) -> Tuple[int, ...]:
    try:
        return tuple(int(x) for x in str(text).split("."))
    except ValueError:
        return (0,)


def inspect_msapp(data: bytes, where: str) -> Dict[str, Any]:
    """ベースとして使えるか確かめ、base.json に書く情報を返す。"""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise ProjectError(f"{where}: .msapp (zip) として開けません") from e
    names = {n.replace("\\", "/"): n for n in zf.namelist()}
    if "Header.json" not in names or "Properties.json" not in names:
        raise ProjectError(f"{where}: Header.json / Properties.json がありません。Power Apps Studio で保存した .msapp を使ってください")
    header = json.loads(zf.read(names["Header.json"]).decode("utf-8-sig"))
    structure = _version_tuple(header.get("MSAppStructureVersion", "1.0"))
    doc = _version_tuple(header.get("DocVersion", "0"))
    if structure < MIN_STRUCTURE_VERSION or doc < MIN_DOC_VERSION:
        raise ProjectError(
            f"{where}: 古い形式の .msapp です (MSAppStructureVersion {header.get('MSAppStructureVersion')}, "
            f"DocVersion {header.get('DocVersion')})。Power Apps Studio で開いて保存し直したものを使ってください")
    props = json.loads(zf.read(names["Properties.json"]).decode("utf-8-sig"))
    sources: List[str] = []
    if "References/DataSources.json" in names:
        ds = json.loads(zf.read(names["References/DataSources.json"]).decode("utf-8-sig"))
        for d in ds.get("DataSources", []):
            if d.get("Type") in ("OptionSetInfo", "ViewInfo"):
                continue
            if d.get("Name"):
                sources.append(d["Name"])
    width = props.get("DocumentLayoutWidth")
    height = props.get("DocumentLayoutHeight")
    form = "Phone" if width and height and int(width) < int(height) else "Tablet"
    screens = sorted(n[4:-8] for n in names if n.startswith("Src/") and n.endswith(".pa.yaml")
                     and not n.startswith("Src/_") and "/" not in n[4:])
    return {
        "formFactor": form,
        "layout": {"width": width, "height": height},
        "docVersion": header.get("DocVersion"),
        "structureVersion": header.get("MSAppStructureVersion"),
        "dataSources": sorted(set(sources)),
        "screensInBase": screens,
    }


def read_msapp_src(data: bytes) -> Dict[str, str]:
    zf = zipfile.ZipFile(io.BytesIO(data))
    out: Dict[str, str] = {}
    for n in zf.namelist():
        norm = n.replace("\\", "/")
        if norm.startswith("Src/") and norm.endswith(".pa.yaml"):
            out[norm[4:]] = zf.read(n).decode("utf-8-sig")
    return out


# --------------------------------------------------------------------------- ソース


@dataclass
class App:
    dir: Path
    name: str
    display_name: str
    description: str
    base_ref: str  # "" ならアプリ直下の base/、それ以外は templates/apps/bases/<id>
    schema_name: Optional[str]
    src: Dict[str, str]  # Src からの相対パス -> 本文
    background_color: str
    post_import: List[str] = field(default_factory=list)

    @property
    def path(self) -> Path:
        return self.dir / "app.json"


def load_app(app_dir: Path) -> App:
    raw = read_json(app_dir / "app.json")
    if not isinstance(raw, dict):
        raise ProjectError(f"{app_dir / 'app.json'}: オブジェクトで書いてください")
    src_dir = app_dir / "Src"
    src: Dict[str, str] = {}
    if src_dir.is_dir():
        for p in sorted(src_dir.rglob("*.pa.yaml")):
            src[p.relative_to(src_dir).as_posix()] = p.read_text(encoding="utf-8-sig")
    post = raw.get("postImport", [])
    return App(
        dir=app_dir,
        name=raw.get("name", app_dir.name),
        display_name=raw.get("displayName", raw.get("name", app_dir.name)),
        description=raw.get("description", ""),
        base_ref=str(raw.get("base", "")),
        schema_name=raw.get("schemaName") or None,
        src=src,
        background_color=raw.get("backgroundColor", "RGBA(0,176,240,1)"),
        post_import=[post] if isinstance(post, str) else list(post),
    )


def load_apps(sol_dir: Path) -> List[App]:
    d = sol_dir / "apps"
    if not d.is_dir():
        return []
    return [load_app(p) for p in sorted(d.iterdir()) if (p / "app.json").is_file()]


def resolve_base(root: Path, app: App) -> Base:
    if not app.base_ref or app.base_ref in (".", "base"):
        if (app.dir / "base" / "base.msapp").is_file():
            return load_base(app.dir / "base", f"{app.name}/base")
        raise ProjectError(f"{app.path}: base が未指定で、{app.dir / 'base' / 'base.msapp'} もありません")
    d = bases_dir(root) / app.base_ref
    if not (d / "base.msapp").is_file():
        known = ", ".join(b.id for b in list_bases(root)) or "(なし。`app-base` で登録)"
        raise ProjectError(f"{app.path}: ベース '{app.base_ref}' がありません。使えるもの: {known}")
    return load_base(d)


def schema_name(prefix: str, solution_unique: str, app: App) -> str:
    if app.schema_name:
        return app.schema_name
    digest = hashlib.sha1(f"{solution_unique}/{app.name}".encode()).hexdigest()[:5]
    return f"{prefix}_{app.name.lower()}_{digest}"


# --------------------------------------------------------------------------- ビルド


@dataclass
class PreparedApp:
    app: App
    base: Base
    schema_name: str
    src: Dict[str, str]  # パラメータ置換済み + _EditorState を補ったもの
    analysis: payaml.AppSource

    @property
    def msapp_path(self) -> str:
        return f"CanvasApps/{self.schema_name}_DocumentUri.msapp"

    @property
    def background_path(self) -> str:
        return f"CanvasApps/{self.schema_name}_BackgroundImageUri"


def substitute_src(app: App, values: Dict[str, Any]) -> Dict[str, str]:
    return {k: params.substitute(v, values) for k, v in app.src.items()}


def find_tokens(app: App) -> set:
    found = set()
    for text in app.src.values():
        found |= params.find_tokens(text)
    return found


def prepare_app(root: Path, prefix: str, solution_unique: str, app: App, values: Dict[str, Any]) -> PreparedApp:
    base = resolve_base(root, app)
    src = substitute_src(app, values)
    analysis = payaml.analyze(src)
    if not analysis.has_editor_state:
        src["_EditorState.pa.yaml"] = payaml.editor_state_yaml(analysis.screens_order, sorted(analysis.components))
    return PreparedApp(app, base, schema_name(prefix, solution_unique, app), src, analysis)


def build_msapp(pa: PreparedApp, solution_unique: str, fixed_time: Tuple[int, ...]) -> bytes:
    zin = zipfile.ZipFile(io.BytesIO(pa.base.msapp))
    sep = "\\" if any("\\" in n for n in zin.namelist()) else "/"
    own_base = pa.base.dir.parent == pa.app.dir
    app_guid = naming.stable_guid("canvasapp", solution_unique, pa.app.name)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        def put(name: str, data: bytes) -> None:
            info = zipfile.ZipInfo(name, date_time=fixed_time)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zout.writestr(info, data)

        for info in zin.infolist():
            norm = info.filename.replace("\\", "/")
            if DROP_FROM_BASE.match(norm) or info.is_dir():
                continue
            data = zin.read(info.filename)
            if norm == "Properties.json":
                data = _patch_properties(data, pa, app_guid, own_base)
            elif norm == "Resources/PublishInfo.json":
                data = _patch_json(data, {"AppName": pa.app.display_name})
            put(info.filename, data)
        for rel, text in sorted(pa.src.items()):
            put("Src" + sep + rel.replace("/", sep), text.encode("utf-8"))
        packed = {
            "PackedStructureVersion": "0.1",
            "PackingClient": {"Name": PACKING_CLIENT, "Version": _pabuild_version()},
            "LoadConfiguration": {"LoadFromYaml": True},
        }
        put("packed.json", json.dumps(packed, indent=2).encode("utf-8"))
    return buf.getvalue()


def _pabuild_version() -> str:
    from . import __version__
    return __version__


def _patch_json(data: bytes, updates: Dict[str, Any]) -> bytes:
    doc = json.loads(data.decode("utf-8-sig"))
    doc.update(updates)
    return json.dumps(doc, ensure_ascii=False).encode("utf-8")


def _patch_properties(data: bytes, pa: PreparedApp, app_guid: str, own_base: bool) -> bytes:
    updates: Dict[str, Any] = {"AppDescription": pa.app.description}
    if not own_base:
        # 共有ベースから作るアプリ同士で文書 ID が重ならないようにする
        updates["Id"] = app_guid
        updates["FileID"] = naming.stable_guid("canvasapp-file", app_guid)
    doc = json.loads(data.decode("utf-8-sig"))
    if "AppDescription" not in doc:
        updates.pop("AppDescription")
    doc.update(updates)
    return json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8")


# --------------------------------------------------------------------------- customizations.xml


def _set_tag(xml: str, tag: str, value: str) -> str:
    pat = re.compile(rf"<{tag}(?:\s[^>]*)?(?:/>|>.*?</{tag}>)", re.S)
    repl = f"<{tag}>{escape(value)}</{tag}>"
    if pat.search(xml):
        return pat.sub(lambda _m: repl, xml, count=1)
    return xml


def canvasapp_xml(pa: PreparedApp, version: str, app_version: str) -> str:
    """app_version: AppVersion (UTC の ISO 時刻)。再インポートで確実に更新されるようビルド時刻を入れる。"""
    app = pa.app
    if pa.base.canvasapp_xml:
        xml = pa.base.canvasapp_xml.strip()
        xml = _set_tag(xml, "Name", pa.schema_name)
        xml = _set_tag(xml, "AppVersion", app_version)
        xml = _set_tag(xml, "DisplayName", app.display_name)
        if app.description:
            xml = _set_tag(xml, "Description", app.description)
        xml = _set_tag(xml, "BackgroundImageUri", "/" + pa.background_path)
        xml = _set_tag(xml, "DocumentUri", "/" + pa.msapp_path)
        xml = re.sub(r"\s*<AdditionalUris>.*?</AdditionalUris>", "", xml, flags=re.S)
        xml = re.sub(r"\s*<AdditionalUris\s*/>", "", xml)
        return "    " + xml
    # ベースが .msapp だけのとき。エクスポート実物 (2025-12) の要素を手本に組み立てる (未検証)
    layout = pa.base.meta.get("layout") or {}
    width, height = str(layout.get("width") or 1366), str(layout.get("height") or 768)
    form = pa.base.meta.get("formFactor") or "Tablet"
    tags = {
        "primaryDeviceWidth": width, "primaryDeviceHeight": height,
        "supportsPortrait": "true", "supportsLandscape": "true" if form == "Tablet" else "false",
        "primaryFormFactor": form, "showStatusBar": "false",
        "publisherVersion": "3.25121.7", "minimumRequiredApiVersion": "2.2.0",
        "hasComponent": "true" if pa.analysis.components else "false",
        "hasUnlockedComponent": "false", "isUnifiedRootApp": "false",
    }
    desc = f"<Description>{escape(app.description)}</Description>" if app.description else '<Description xsi:nil="true"></Description>'
    return f"""    <CanvasApp>
      <Name>{escape(pa.schema_name)}</Name>
      <AppVersion>{escape(app_version)}</AppVersion>
      <Status>Ready</Status>
      <CreatedByClientVersion>3.25121.7.0</CreatedByClientVersion>
      <MinClientVersion>3.25121.7.0</MinClientVersion>
      <Tags>{escape(json.dumps(tags, separators=(',', ':')))}</Tags>
      <IsCdsUpgraded>0</IsCdsUpgraded>
      <GalleryItemId xsi:nil="true"></GalleryItemId>
      <BackgroundColor>{escape(app.background_color)}</BackgroundColor>
      <DisplayName>{escape(app.display_name)}</DisplayName>
      {desc}
      <CommitMessage xsi:nil="true"></CommitMessage>
      <Publisher xsi:nil="true"></Publisher>
      <AuthorizationReferences>[]</AuthorizationReferences>
      <ConnectionReferences>{{}}</ConnectionReferences>
      <DatabaseReferences>{{}}</DatabaseReferences>
      <AppComponents>[]</AppComponents>
      <AppComponentDependencies>[]</AppComponentDependencies>
      <CanConsumeAppPass>1</CanConsumeAppPass>
      <CanvasAppType>0</CanvasAppType>
      <BypassConsent>0</BypassConsent>
      <AdminControlBypassConsent>0</AdminControlBypassConsent>
      <EmbeddedApp xsi:nil="true"></EmbeddedApp>
      <IntroducedVersion>{escape(version)}</IntroducedVersion>
      <CdsDependencies>{{"cdsdependencies":[]}}</CdsDependencies>
      <IsCustomizable>1</IsCustomizable>
      <BackgroundImageUri>/{escape(pa.background_path)}</BackgroundImageUri>
      <DocumentUri>/{escape(pa.msapp_path)}</DocumentUri>
    </CanvasApp>"""


def solid_png(rgba: Tuple[int, int, int, int] = (0, 176, 240, 255), size: int = 32) -> bytes:
    """背景画像の代わりに使う単色 PNG。"""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    row = b"\x00" + bytes(rgba) * size
    raw = zlib.compress(row * size, 9)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", raw) + chunk(b"IEND", b""))


def parse_rgba(text: str) -> Tuple[int, int, int, int]:
    m = re.fullmatch(r"\s*RGBA\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+)\s*\)\s*", text or "")
    if not m:
        return (0, 176, 240, 255)
    r, g, b = (min(255, int(m.group(i))) for i in (1, 2, 3))
    return (r, g, b, int(round(min(1.0, float(m.group(4))) * 255)))


def background_png(pa: PreparedApp) -> bytes:
    return pa.base.background or solid_png(parse_rgba(pa.app.background_color))


# --------------------------------------------------------------------------- ベースの取り込み


def _canvas_entries(zf: zipfile.ZipFile) -> List[Tuple[str, str, Dict[str, Any]]]:
    """ソリューション zip から (CanvasApp 要素の XML, 表示名, 情報) の一覧を返す。"""
    cust = zf.read("customizations.xml").decode("utf-8-sig")
    out = []
    for m in re.finditer(r"<CanvasApp>.*?</CanvasApp>", cust, re.S):
        xml = m.group(0)

        def tag(name: str) -> str:
            mm = re.search(rf"<{name}>(.*?)</{name}>", xml, re.S)
            return _unescape(mm.group(1)) if mm else ""
        out.append((xml, tag("DisplayName"), {
            "name": tag("Name"),
            "document": tag("DocumentUri").lstrip("/"),
            "background": tag("BackgroundImageUri").lstrip("/"),
            "connectionReferences": tag("ConnectionReferences"),
        }))
    return out


def _unescape(text: str) -> str:
    from xml.sax.saxutils import unescape
    return unescape(text, {"&quot;": '"', "&apos;": "'"})


def _connrefs_from(zf: zipfile.ZipFile, conn_json: str) -> List[Dict[str, str]]:
    cust = zf.read("customizations.xml").decode("utf-8-sig")
    displays = {
        m.group(1): _unescape(m.group(2))
        for m in re.finditer(
            r'<connectionreference connectionreferencelogicalname="([^"]+)">\s*<connectionreferencedisplayname>(.*?)</connectionreferencedisplayname>',
            cust, re.S)
    }
    try:
        refs = json.loads(conn_json or "{}")
    except json.JSONDecodeError:
        return []
    out = []
    for ref in refs.values():
        logical = ref.get("connectionReferenceLogicalName")
        api_id = str(ref.get("id", ""))
        if not logical or not api_id.lower().startswith(API_PREFIX):
            continue
        out.append({"logicalName": logical, "apiName": api_id[len(API_PREFIX):],
                    "displayName": displays.get(logical, "")})
    return out


def capture_from_solution(zf: zipfile.ZipFile, target: Path, app_name: str = "") -> Dict[str, Any]:
    """ソリューション zip の中のキャンバス アプリ 1 つをベースとして target/ に書き出す。"""
    entries = _canvas_entries(zf)
    if not entries:
        raise ProjectError("このソリューションにはキャンバス アプリが含まれていません")
    if app_name:
        picked = [e for e in entries if app_name in (e[1], e[2]["name"])]
        if not picked:
            raise ProjectError(f"アプリ '{app_name}' がありません。含まれるもの: {', '.join(e[1] for e in entries)}")
        entry = picked[0]
    else:
        entry = entries[0]
    xml, display, info = entry
    return _write_base(target, zf.read(info["document"]), xml,
                       zf.read(info["background"]) if info["background"] in zf.namelist() else None,
                       _connrefs_from(zf, info["connectionReferences"]),
                       {"capturedFrom": "solution", "originalName": info["name"], "originalDisplayName": display})


def capture_from_msapp(data: bytes, target: Path) -> Dict[str, Any]:
    return _write_base(target, data, None, None, [], {"capturedFrom": "msapp"})


def _write_base(target: Path, msapp: bytes, xml: Optional[str], background: Optional[bytes],
                connrefs: List[Dict[str, str]], extra: Dict[str, Any]) -> Dict[str, Any]:
    meta = inspect_msapp(msapp, str(target))
    meta.update(extra)
    if connrefs:
        meta["connectionReferences"] = connrefs
    target.mkdir(parents=True, exist_ok=True)
    (target / "base.msapp").write_bytes(msapp)
    if xml:
        (target / "canvasapp.xml").write_text(xml.strip() + "\n", encoding="utf-8")
    if background:
        (target / "background.png").write_bytes(background)
    old = read_json(target / "base.json") if (target / "base.json").is_file() else {}
    for keep in ("title", "verified"):
        if keep in old:
            meta.setdefault(keep, old[keep])
    write_json(target / "base.json", meta)
    return meta
