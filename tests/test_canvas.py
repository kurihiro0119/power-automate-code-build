"""キャンバス アプリ (pa.yaml → .msapp → ソリューション) のテスト。"""
import io
import json
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from pabuild import build as builder
from pabuild import canvas, importer, payaml, scaffold
from pabuild.project import ProjectError, load_solution
from tests.test_pabuild import ROOT, run_cli

OLD_SCREEN = """Screens:
  Screen1:
    Properties:
      Fill: =White
"""


def fake_msapp(structure="2.4.0", doc="1.348", width=640, height=1136, data_sources=()) -> bytes:
    """Studio が保存する .msapp の形 (Windows 区切りのパス) を最小限まねたもの。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Header.json", json.dumps({"DocVersion": doc, "MinVersionToLoad": doc, "MSAppStructureVersion": structure}))
        zf.writestr("Properties.json", json.dumps({
            "Author": "", "Name": "base.msapp", "Id": "11111111-1111-1111-1111-111111111111",
            "FileID": "22222222-2222-2222-2222-222222222222", "AppDescription": "",
            "DocumentLayoutWidth": width, "DocumentLayoutHeight": height}))
        zf.writestr("Controls\\1.json", "{}")
        zf.writestr("References\\DataSources.json", json.dumps({"DataSources": [
            {"Name": n, "Type": "ServiceInfo"} for n in data_sources] + [{"Name": "x_opt", "Type": "OptionSetInfo"}]}))
        zf.writestr("Resources\\PublishInfo.json", json.dumps({"AppName": "Base"}))
        zf.writestr("Src\\App.pa.yaml", "App:\n  Properties:\n    Theme: =PowerAppsTheme\n")
        zf.writestr("Src\\Screen1.pa.yaml", OLD_SCREEN)
        zf.writestr("Src\\_EditorState.pa.yaml", "EditorState:\n  ScreensOrder:\n    - Screen1\n")
    return buf.getvalue()


CANVAS_XML = """<CanvasApp>
      <Name>orig_myapp_abcde</Name>
      <AppVersion>2025-12-23T02:41:29Z</AppVersion>
      <Status>Ready</Status>
      <DisplayName>My App</DisplayName>
      <Description xsi:nil="true"></Description>
      <ConnectionReferences>{"c1":{"id":"/providers/microsoft.powerapps/apis/shared_sharepointonline","displayName":"SharePoint","connectionReferenceLogicalName":"orig_sharedsharepointonline_x1"}}</ConnectionReferences>
      <IsCustomizable>1</IsCustomizable>
      <BackgroundImageUri>/CanvasApps/orig_myapp_abcde_BackgroundImageUri</BackgroundImageUri>
      <DocumentUri>/CanvasApps/orig_myapp_abcde_DocumentUri.msapp</DocumentUri>
      <AdditionalUris>
        <AdditionalUri>/CanvasApps/orig_myapp_abcde_AdditionalUris0_identity.json</AdditionalUri>
      </AdditionalUris>
    </CanvasApp>"""


def fake_solution_zip(path: Path) -> None:
    cust = f"""<?xml version="1.0" encoding="utf-8"?>
<ImportExportXml xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <Workflows />
  <CanvasApps>
    {CANVAS_XML}
  </CanvasApps>
  <connectionreferences>
    <connectionreference connectionreferencelogicalname="orig_sharedsharepointonline_x1">
      <connectionreferencedisplayname>SharePoint (Orig)</connectionreferencedisplayname>
    </connectionreference>
  </connectionreferences>
</ImportExportXml>"""
    sol = """<?xml version="1.0" encoding="utf-8"?>
<ImportExportXml><SolutionManifest><UniqueName>Orig</UniqueName><Version>1.0.0.0</Version><Managed>0</Managed>
<Publisher><UniqueName>origpub</UniqueName><CustomizationPrefix>orig</CustomizationPrefix><CustomizationOptionValuePrefix>12345</CustomizationOptionValuePrefix></Publisher>
</SolutionManifest></ImportExportXml>"""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("solution.xml", sol)
        zf.writestr("customizations.xml", cust)
        zf.writestr("CanvasApps/orig_myapp_abcde_DocumentUri.msapp",
                    fake_msapp(width=1366, height=768, data_sources=["Tasks"]))
        zf.writestr("CanvasApps/orig_myapp_abcde_BackgroundImageUri", b"PNGDATA")


class PaYaml(unittest.TestCase):
    def issues(self, files):
        return [(i.level, i.message) for i in payaml.analyze(files).issues]

    def test_parse_subset(self):
        doc = payaml.parse("A:\n  B: |-\n    x\n    y\n  C: =If(a, \"b：c\")\n  D:\n    - E:\n        F: =1\n    - 'G'\n")
        self.assertEqual(doc, {"A": {"B": "x\ny", "C": '=If(a, "b：c")', "D": [{"E": {"F": "=1"}}, "G"]}})

    def test_rejects_colon_space_in_plain_formula(self):
        with self.assertRaisesRegex(payaml.PaYamlError, "プレーン値に ': '"):
            payaml.parse('A:\n  Text: ="例: A001"\n')

    def test_templates_are_clean(self):
        for t in scaffold.list_app_templates(ROOT):
            files = {p.relative_to(t["path"] / "Src").as_posix(): p.read_text(encoding="utf-8")
                     for p in (t["path"] / "Src").rglob("*.pa.yaml")}
            self.assertEqual(self.issues(files), [], t["id"])

    def test_common_mistakes(self):
        msgs = " ".join(m for _l, m in self.issues({
            "S.pa.yaml": "Screens:\n  S1:\n    Children:\n      - L1:\n          Control: Label\n          Properties:\n            Text: Hello\n"
                         "      - L1:\n          Control: Label\n      - B:\n          Properties:\n            X: =1\n",
            "App.pa.yaml": "App:\n  Properties:\n    StartScreen: =Nowhere\nS2:\n  Properties: {}\n",
        }))
        self.assertIn("= で始め", msgs)
        self.assertIn("'L1' が重複", msgs)
        self.assertIn("Control", msgs)
        self.assertIn("最上位に 'S2'", msgs)
        self.assertIn("Nowhere", msgs)

    def test_tabs_and_duplicates_are_errors(self):
        self.assertTrue(self.issues({"a.pa.yaml": "Screens:\n\tS: \n"}))
        self.assertTrue(any("重複" in m for _l, m in self.issues({"a.pa.yaml": "Screens:\n  S:\n  S:\n"})))


class CanvasBuild(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copytree(ROOT / "templates", self.tmp / "templates")
        shutil.rmtree(self.tmp / "templates" / "apps" / "bases", ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.r = ["--root", str(self.tmp)]

    def test_new_app_requires_base(self):
        sol = scaffold.new_solution(self.tmp, "NoBase")
        with self.assertRaises(ProjectError) as cm:
            scaffold.new_app(self.tmp, sol, "Hello", "hello-app")
        self.assertIn("app-base", str(cm.exception))

    def test_old_msapp_is_rejected(self):
        p = self.tmp / "old.msapp"
        p.write_bytes(fake_msapp(structure="2.0", doc="1.294"))
        code, out = run_cli(*self.r, "app-base", str(p), "--id", "old")
        self.assertEqual(code, 1)
        self.assertIn("古い形式", out)

    def test_msapp_base_end_to_end(self):
        p = self.tmp / "blank.msapp"
        p.write_bytes(fake_msapp())
        self.assertEqual(run_cli(*self.r, "app-base", str(p), "--id", "blank-phone")[0], 0)
        self.assertEqual(run_cli(*self.r, "new-solution", "AppDemo")[0], 0)
        self.assertEqual(run_cli(*self.r, "new-app", "AppDemo", "Catalog", "-t", "local-list-detail",
                                 "--display", "備品カタログ")[0], 0)
        code, out = run_cli(*self.r, "build", "AppDemo")
        self.assertEqual(code, 0, out)
        zip_path = self.tmp / "dist" / "AppDemo_1_0_0_0.zip"
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            sol = ET.fromstring(zf.read("solution.xml"))
            cust = ET.fromstring(zf.read("customizations.xml"))
            ET.fromstring(zf.read("[Content_Types].xml"))
            schema = [r.get("schemaName") for r in sol.iter("RootComponent") if r.get("type") == "300"]
            self.assertEqual(len(schema), 1)
            self.assertRegex(schema[0], r"^lab_catalog_[0-9a-f]{5}$")
            app_el = cust.find("CanvasApps/CanvasApp")
            self.assertEqual(app_el.findtext("Name"), schema[0])
            self.assertEqual(app_el.findtext("DisplayName"), "備品カタログ")
            self.assertIn('"primaryFormFactor":"Phone"', app_el.findtext("Tags"))
            doc_uri = app_el.findtext("DocumentUri").lstrip("/")
            self.assertIn(doc_uri, names)
            self.assertIn(app_el.findtext("BackgroundImageUri").lstrip("/"), names)
            self.assertTrue(zf.read(app_el.findtext("BackgroundImageUri").lstrip("/")).startswith(b"\x89PNG"))
            msapp = zipfile.ZipFile(io.BytesIO(zf.read(doc_uri)))
        m_names = msapp.namelist()
        self.assertIn("Src\\ListScreen.pa.yaml", m_names)
        self.assertIn("Src\\DetailScreen.pa.yaml", m_names)
        self.assertNotIn("Src\\Screen1.pa.yaml", m_names)  # ベースの画面は捨てる
        self.assertIn("Controls\\1.json", m_names)  # ベースのその他はそのまま
        self.assertEqual(json.loads(msapp.read("packed.json"))["LoadConfiguration"], {"LoadFromYaml": True})
        state = msapp.read("Src\\_EditorState.pa.yaml").decode()
        self.assertLess(state.index("ListScreen"), state.index("DetailScreen"))
        props = json.loads(msapp.read("Properties.json"))
        self.assertNotEqual(props["Id"], "11111111-1111-1111-1111-111111111111")
        self.assertEqual(json.loads(msapp.read("Resources\\PublishInfo.json"))["AppName"], "備品カタログ")
        guide = (self.tmp / "dist" / "AppDemo_1_0_0_0_IMPORT.md").read_text(encoding="utf-8")
        self.assertIn("アプリ: 備品カタログ", guide)

    def test_yaml_error_stops_build(self):
        p = self.tmp / "blank.msapp"
        p.write_bytes(fake_msapp())
        run_cli(*self.r, "app-base", str(p), "--id", "blank")
        sol = scaffold.new_solution(self.tmp, "Broken")
        app_dir = scaffold.new_app(self.tmp, sol, "Hello", "hello-app")
        screen = app_dir / "Src" / "HomeScreen.pa.yaml"
        screen.write_text(screen.read_text(encoding="utf-8").replace('Text: ="あいさつする"', "Text: あいさつする"),
                          encoding="utf-8")
        code, out = run_cli(*self.r, "build", "Broken")
        self.assertEqual(code, 1)
        self.assertIn("HomeScreen.pa.yaml:", out)
        self.assertIn("= で始め", out)

    def test_solution_base_keeps_real_definition_and_connrefs(self):
        zip_path = self.tmp / "exported.zip"
        fake_solution_zip(zip_path)
        self.assertEqual(run_cli(*self.r, "app-base", str(zip_path), "--id", "tasks-tablet")[0], 0)
        base = canvas.load_base(canvas.bases_dir(self.tmp) / "tasks-tablet")
        self.assertEqual(base.data_sources, ["Tasks"])
        self.assertEqual(base.connection_references[0]["apiName"], "shared_sharepointonline")
        sol = scaffold.new_solution(self.tmp, "TaskApp")
        scaffold.new_app(self.tmp, sol, "Board", "hello-app", base_id="tasks-tablet")
        prep = builder.prepare(load_solution(sol))
        self.assertEqual([i.format() for i in prep.errors], [])
        files = builder.solution_files(prep)
        cust = ET.fromstring(files["customizations.xml"])
        app_el = cust.find("CanvasApps/CanvasApp")
        self.assertEqual(app_el.findtext("Name"), prep.apps[0].schema_name)
        self.assertIsNone(app_el.find("AdditionalUris"))
        self.assertNotEqual(app_el.findtext("AppVersion"), "2025-12-23T02:41:29Z")
        refs = {c.get("connectionreferencelogicalname") for c in cust.iter("connectionreference")}
        self.assertEqual(refs, {"orig_sharedsharepointonline_x1"})
        self.assertEqual(files[prep.apps[0].background_path], b"PNGDATA")

    def test_import_extracts_app_source(self):
        zip_path = self.tmp / "exported.zip"
        fake_solution_zip(zip_path)
        target = importer.import_solution_zip(zip_path, self.tmp)
        app_dir = target / "apps" / "Myapp"
        self.assertEqual(json.loads((app_dir / "app.json").read_text())["schemaName"], "orig_myapp_abcde")
        self.assertEqual((app_dir / "Src" / "Screen1.pa.yaml").read_text(), OLD_SCREEN)
        prep = builder.prepare(load_solution(target))
        self.assertEqual([i.format() for i in prep.errors], [])
        self.assertEqual(prep.apps[0].schema_name, "orig_myapp_abcde")
        files = builder.solution_files(prep)
        msapp = zipfile.ZipFile(io.BytesIO(files[prep.apps[0].msapp_path]))
        # 自前のベースでは文書 ID を変えない (元のアプリを更新するため)
        self.assertEqual(json.loads(msapp.read("Properties.json"))["Id"], "11111111-1111-1111-1111-111111111111")


if __name__ == "__main__":
    unittest.main()
