"""ソース → (検証) → ソリューション zip / レガシー パッケージ zip。"""
from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Set, Union
from xml.sax.saxutils import escape, quoteattr

from . import canvas, catalog, flowdef, naming, params, validate
from .project import ProjectError, Solution, read_json
from .validate import ERROR, INFO, WARNING, Issue

CONTENT_TYPES_XML = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="xml" ContentType="text/xml" />'
    '<Default Extension="json" ContentType="application/octet-stream" />'
    "</Types>"
)


def content_types_xml(extra_parts: List[str]) -> str:
    """キャンバス アプリがあるときは .msapp と拡張子なしの背景画像を登録する (エクスポート実物と同じ)。"""
    if not extra_parts:
        return CONTENT_TYPES_XML
    overrides = "".join(
        f'<Override PartName={quoteattr("/" + p)} ContentType="application/octet-stream" />' for p in extra_parts
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="text/xml" />'
        '<Default Extension="json" ContentType="application/octet-stream" />'
        '<Default Extension="msapp" ContentType="application/octet-stream" />'
        f"{overrides}</Types>"
    )
# zip 内の更新日時を固定し、同じソースから同じバイト列の zip が出るようにする
FIXED_ZIP_TIME = (2020, 1, 1, 0, 0, 0)


@dataclass
class PreparedFlow:
    flow: Any
    guid: str
    definition: Dict[str, Any]
    connections: Dict[str, str]  # connectionName -> API 名

    @property
    def file_name(self) -> str:
        return naming.workflow_filename(self.flow.name, self.guid)


@dataclass
class ConnRef:
    connection_name: str
    api_name: str
    logical_name: str
    display_name: str


@dataclass
class Prepared:
    solution: Solution
    flows: List[PreparedFlow]
    connrefs: Dict[str, ConnRef]
    values: Dict[str, Any]
    used_tokens: Set[str]
    example_tokens: Set[str]
    issues: List[Issue] = field(default_factory=list)
    apps: List[canvas.PreparedApp] = field(default_factory=list)

    @property
    def errors(self) -> List[Issue]:
        return [i for i in self.issues if i.level == ERROR]


def load_local_params(solution: Solution) -> Dict[str, Any]:
    path = solution.dir / "params.local.json"
    if not path.is_file():
        return {}
    data = read_json(path)
    if not isinstance(data, dict):
        raise ProjectError(f"{path}: オブジェクト ({{\"NAME\": \"値\"}}) で書いてください")
    return data


def prepare(
    solution: Solution,
    overrides: Optional[Dict[str, Any]] = None,
    strict_params: bool = True,
    use_examples: bool = False,
) -> Prepared:
    """パラメータ置換・正規化・検証まで行う (ファイルは書かない)。"""
    issues = validate.validate_solution_meta(solution)
    used: Set[str] = set()
    for flow in solution.flows:
        used |= params.find_tokens(flow.definition)
    for app in solution.apps:
        used |= canvas.find_tokens(app)
    values, _missing, from_example = params.resolve(
        solution.params_spec, load_local_params(solution), overrides or {}, used, use_examples
    )

    flows: List[PreparedFlow] = []
    connrefs: Dict[str, ConnRef] = {}
    for flow in solution.flows:
        definition = flowdef.normalize(params.substitute(flow.definition, values))
        issues += validate.unresolved_token_issues(
            flow, definition, set(solution.params_spec) | set(values), strict_params
        )
        issues += validate.validate_flow(flow, definition)
        conns = flowdef.connections_used(definition)
        for conn, api in conns.items():
            existing = connrefs.get(conn)
            if existing and existing.api_name != api:
                issues.append(Issue(ERROR, flow.name, "", f"接続名 '{conn}' が別々のコネクタ ({existing.api_name} と {api}) に使われています"))
                continue
            if existing:
                continue
            override = solution.connref_overrides.get(conn, {})
            connrefs[conn] = ConnRef(
                connection_name=conn,
                api_name=api,
                logical_name=override.get("logicalName")
                or naming.connref_logical_name(solution.prefix, conn, solution.unique_name),
                display_name=override.get("displayName")
                or f"{catalog.display_name(api)} ({solution.display_name})",
            )
        flows.append(PreparedFlow(flow, solution.flow_id(flow), definition, conns))
    apps = _prepare_apps(solution, values, connrefs, issues, strict_params)
    return Prepared(solution, flows, connrefs, values, used, from_example, issues, apps)


def _prepare_apps(solution: Solution, values: Dict[str, Any], connrefs: Dict[str, ConnRef],
                  issues: List[Issue], strict_params: bool) -> List[canvas.PreparedApp]:
    root = solution.dir.parent.parent
    declared = set(solution.params_spec) | set(values)
    out: List[canvas.PreparedApp] = []
    for app in solution.apps:
        label = f"app:{app.name}"
        try:
            pa = canvas.prepare_app(root, solution.prefix, solution.unique_name, app, values)
        except ProjectError as e:
            issues.append(Issue(ERROR, label, "", str(e)))
            continue
        issues += validate.unresolved_token_issues(SimpleNamespace(name=label), pa.src, declared, strict_params)
        for yi in pa.analysis.issues:
            where = f"Src/{yi.file}" + (f":{yi.line}" if yi.line else "")
            issues.append(Issue(yi.level, label, where, yi.message))
        if not pa.base.canvasapp_xml:
            issues.append(Issue(WARNING, label, "base", (
                f"ベース '{pa.base.id}' は .msapp 単体から登録されたため、ソリューション内のアプリ定義 (CanvasApp) を推測で生成します (未検証)。"
                "ソリューション zip から `app-base` したベースを使うと確実です")))
        sources = pa.base.data_sources
        issues.append(Issue(INFO, label, "base", (
            f"ベース '{pa.base.id}' で使えるデータ ソース: {', '.join(sources) if sources else 'なし'}"
            " (pa.yaml にはデータ ソースを追加できません)")))
        for ref in pa.base.connection_references:
            logical = ref["logicalName"]
            if any(c.logical_name == logical for c in connrefs.values()):
                continue
            connrefs[f"app:{logical}"] = ConnRef(
                connection_name=f"app:{logical}",
                api_name=ref["apiName"],
                logical_name=logical,
                display_name=ref.get("displayName") or f"{catalog.display_name(ref['apiName'])} ({solution.display_name})",
            )
        out.append(pa)
    return out


# --------------------------------------------------------------------------- ソリューション


def workflow_json(pf: PreparedFlow, connrefs: Dict[str, ConnRef]) -> str:
    refs = {}
    for conn, api in pf.connections.items():
        refs[conn] = {
            "runtimeSource": "embedded",
            "connection": {"connectionReferenceLogicalName": connrefs[conn].logical_name},
            "api": {"name": api},
        }
    doc = {
        "properties": {"connectionReferences": refs, "definition": pf.definition},
        "schemaVersion": "1.0.0.0",
    }
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


def solution_xml(prep: Prepared) -> str:
    s = prep.solution
    pub = s.publisher
    desc = (
        f"<Descriptions>\n      <Description description={quoteattr(s.description)} languagecode=\"1033\" />\n    </Descriptions>"
        if s.description
        else "<Descriptions />"
    )
    roots = [f'      <RootComponent type="29" id="{{{pf.guid}}}" behavior="0" />' for pf in prep.flows]
    roots += [
        f'      <RootComponent type="300" schemaName={quoteattr(pa.schema_name)} behavior="0" />' for pa in prep.apps
    ]
    roots += [
        f'      <RootComponent type="connectionreference" schemaName={quoteattr(c.logical_name)} behavior="0" />'
        for c in prep.connrefs.values()
    ]
    return f"""<?xml version="1.0" encoding="utf-8"?>
<ImportExportXml version="9.2.24.4" SolutionPackageVersion="9.2" languagecode="1033" generatedBy="CrmLive" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <SolutionManifest>
    <UniqueName>{escape(s.unique_name)}</UniqueName>
    <LocalizedNames>
      <LocalizedName description={quoteattr(s.display_name)} languagecode="1033" />
    </LocalizedNames>
    {desc}
    <Version>{escape(s.version)}</Version>
    <Managed>0</Managed>
    <Publisher>
      <UniqueName>{escape(str(pub["uniqueName"]))}</UniqueName>
      <LocalizedNames>
        <LocalizedName description={quoteattr(str(pub["displayName"]))} languagecode="1033" />
      </LocalizedNames>
      <Descriptions />
      <EMailAddress xsi:nil="true"></EMailAddress>
      <SupportingWebsiteUrl xsi:nil="true"></SupportingWebsiteUrl>
      <CustomizationPrefix>{escape(str(pub["prefix"]))}</CustomizationPrefix>
      <CustomizationOptionValuePrefix>{escape(str(pub["optionValuePrefix"]))}</CustomizationOptionValuePrefix>
      <Addresses />
    </Publisher>
    <RootComponents>
{chr(10).join(roots)}
    </RootComponents>
    <MissingDependencies />
  </SolutionManifest>
</ImportExportXml>
"""


def _workflow_xml(pf: PreparedFlow, version: str) -> str:
    f = pf.flow
    on = f.state == "on"
    desc_attr = f" Description={quoteattr(f.description)}" if f.description else ""
    return f"""    <Workflow WorkflowId="{{{pf.guid}}}" Name={quoteattr(f.display_name)}{desc_attr}>
      <JsonFileName>/Workflows/{escape(pf.file_name)}</JsonFileName>
      <Type>1</Type>
      <Subprocess>0</Subprocess>
      <Category>5</Category>
      <Mode>0</Mode>
      <Scope>4</Scope>
      <OnDemand>0</OnDemand>
      <TriggerOnCreate>0</TriggerOnCreate>
      <TriggerOnDelete>0</TriggerOnDelete>
      <AsyncAutodelete>0</AsyncAutodelete>
      <SyncWorkflowLogOnFailure>0</SyncWorkflowLogOnFailure>
      <StateCode>{1 if on else 0}</StateCode>
      <StatusCode>{2 if on else 1}</StatusCode>
      <RunAs>1</RunAs>
      <IsTransacted>1</IsTransacted>
      <IntroducedVersion>{escape(version)}</IntroducedVersion>
      <IsCustomizable>1</IsCustomizable>
      <BusinessProcessType>0</BusinessProcessType>
      <IsCustomProcessingStepAllowedForOtherPublishers>1</IsCustomProcessingStepAllowedForOtherPublishers>
      <PrimaryEntity>none</PrimaryEntity>
      <LocalizedNames>
        <LocalizedName languagecode="1033" description={quoteattr(f.display_name)} />
      </LocalizedNames>
    </Workflow>"""


def customizations_xml(prep: Prepared) -> str:
    workflows = "\n".join(_workflow_xml(pf, prep.solution.version) for pf in prep.flows)
    workflows_el = f"  <Workflows>\n{workflows}\n  </Workflows>" if prep.flows else "  <Workflows />"
    if prep.apps:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        app_items = "\n".join(canvas.canvasapp_xml(pa, prep.solution.version, stamp) for pa in prep.apps)
        canvas_el = f"  <CanvasApps>\n{app_items}\n  </CanvasApps>\n"
    else:
        canvas_el = ""
    if prep.connrefs:
        items = "\n".join(
            f"""    <connectionreference connectionreferencelogicalname={quoteattr(c.logical_name)}>
      <connectionreferencedisplayname>{escape(c.display_name)}</connectionreferencedisplayname>
      <connectorid>{escape(flowdef.API_ID_PREFIX + c.api_name)}</connectorid>
      <iscustomizable>1</iscustomizable>
      <statecode>0</statecode>
      <statuscode>1</statuscode>
    </connectionreference>"""
            for c in prep.connrefs.values()
        )
        connrefs = f"  <connectionreferences>\n{items}\n  </connectionreferences>\n"
    else:
        connrefs = ""
    return f"""<?xml version="1.0" encoding="utf-8"?>
<ImportExportXml xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <Entities />
  <Roles />
{workflows_el}
  <FieldSecurityProfiles />
  <Templates />
  <EntityMaps />
  <EntityRelationships />
  <OrganizationSettings />
  <optionsets />
  <CustomControls />
  <EntityDataProviders />
{canvas_el}{connrefs}  <Languages>
    <Language>1033</Language>
  </Languages>
</ImportExportXml>
"""


def solution_files(prep: Prepared) -> Dict[str, Union[str, bytes]]:
    files: Dict[str, Union[str, bytes]] = {
        "solution.xml": solution_xml(prep),
        "customizations.xml": customizations_xml(prep),
        "[Content_Types].xml": content_types_xml([pa.background_path for pa in prep.apps]),
    }
    for pf in prep.flows:
        files[f"Workflows/{pf.file_name}"] = workflow_json(pf, prep.connrefs)
    for pa in prep.apps:
        files[pa.msapp_path] = canvas.build_msapp(pa, prep.solution.unique_name, FIXED_ZIP_TIME)
        files[pa.background_path] = canvas.background_png(pa)
    return files


def write_zip(path: Path, files: Dict[str, Union[str, bytes]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in files.items():
            info = zipfile.ZipInfo(name, date_time=FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, text if isinstance(text, bytes) else text.encode("utf-8"))
    path.write_bytes(buf.getvalue())


def solution_zip_name(solution: Solution) -> str:
    return f"{solution.unique_name}_{naming.version_slug(solution.version)}.zip"


# --------------------------------------------------------------------------- レガシー パッケージ


def legacy_package_files(prep: Prepared, pf: PreparedFlow) -> Dict[str, str]:
    """「パッケージのインポート (レガシー)」用。Dataverse の無い環境向け。1 フロー = 1 zip。"""
    sol = prep.solution.unique_name
    asset_id = naming.stable_guid("legacy-asset", sol, pf.flow.name)
    resources: Dict[str, Any] = {}
    depends: List[str] = []
    apis_map: Dict[str, str] = {}
    conns_map: Dict[str, str] = {}
    conn_refs: Dict[str, Any] = {}
    for conn, api in pf.connections.items():
        api_res = naming.stable_guid("legacy-api", sol, pf.flow.name, conn)
        conn_res = naming.stable_guid("legacy-conn", sol, pf.flow.name, conn)
        api_id = flowdef.API_ID_PREFIX + api
        title = catalog.display_name(api)
        apis_map[conn] = api_res
        conns_map[conn] = conn_res
        depends += [api_res, conn_res]
        resources[api_res] = {
            "id": api_id, "name": api, "type": "Microsoft.PowerApps/apis",
            "suggestedCreationType": "Existing", "details": {"displayName": title},
            "configurableBy": "System", "hierarchy": "Child", "dependsOn": [],
        }
        resources[conn_res] = {
            "type": "Microsoft.PowerApps/apis/connections",
            "suggestedCreationType": "Existing", "creationType": "Existing",
            "details": {"displayName": title},
            "configurableBy": "User", "hierarchy": "Child", "dependsOn": [api_res],
        }
        conn_refs[conn] = {"connectionName": "", "source": "Embedded", "id": api_id, "tier": "NotSpecified"}
    flow_res = {
        "type": "Microsoft.Flow/flows", "suggestedCreationType": "New",
        "creationType": "Existing, New, Update",
        "details": {"displayName": pf.flow.display_name},
        "configurableBy": "User", "hierarchy": "Root", "dependsOn": depends,
    }
    manifest = {
        "schema": "1.0",
        "details": {
            "displayName": pf.flow.display_name, "description": pf.flow.description,
            "createdTime": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "packageTelemetryId": naming.stable_guid("legacy-telemetry", sol, pf.flow.name),
            "creator": "N/A", "sourceEnvironment": "",
        },
        "resources": {asset_id: flow_res, **resources},
    }
    definition = {
        "name": pf.guid,
        "id": f"/providers/Microsoft.Flow/flows/{pf.guid}",
        "type": "Microsoft.Flow/flows",
        "properties": {
            "apiId": "/providers/Microsoft.PowerApps/apis/shared_logicflows",
            "displayName": pf.flow.display_name,
            "definition": pf.definition,
            "connectionReferences": conn_refs,
            "flowFailureAlertSubscribed": False,
            "isManaged": False,
        },
    }
    dump = lambda o: json.dumps(o, ensure_ascii=False, separators=(",", ":"))  # noqa: E731
    base = f"Microsoft.Flow/flows/{asset_id}"
    return {
        f"{base}/apisMap.json": dump(apis_map),
        f"{base}/connectionsMap.json": dump(conns_map),
        f"{base}/definition.json": dump(definition),
        "Microsoft.Flow/flows/manifest.json": dump(
            {"packageSchemaVersion": "1.0", "flowAssets": {"assetPaths": [asset_id]}}
        ),
        "manifest.json": dump(manifest),
    }
