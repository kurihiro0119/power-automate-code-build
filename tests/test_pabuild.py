"""python3 -m unittest で実行 (標準ライブラリのみ)。"""
import copy
import io
import json
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from pabuild import build as builder
from pabuild import flowdef, importer, naming, params, scaffold
from pabuild.cli import main
from pabuild.project import Flow, find_solutions, load_solution
from pabuild.validate import ERROR, validate_flow

ROOT = Path(__file__).resolve().parent.parent


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue() + err.getvalue()


def make_flow(definition, name="T"):
    return Flow(Path("T.flow.json"), name, name, "", "on", None, definition)


def errors_of(definition):
    flow = make_flow(definition)
    return [i.message for i in validate_flow(flow, flowdef.normalize(definition)) if i.level == ERROR]


BASE = {
    "triggers": {"manual": {"type": "Request", "kind": "Button", "inputs": {"schema": {"type": "object"}}}},
    "actions": {
        "A": {"type": "Compose", "inputs": "x"},
        "B": {"type": "Compose", "inputs": "@outputs('A')", "runAfter": {"A": ["Succeeded"]}},
    },
}


class TempRepo(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copytree(ROOT / "templates", self.tmp / "templates")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)


class RepoSolutions(unittest.TestCase):
    def test_all_solutions_build_cleanly(self):
        dirs = find_solutions(ROOT)
        self.assertTrue(dirs)
        for d in dirs:
            prep = builder.prepare(load_solution(d), use_examples=True)
            self.assertEqual([i.format() for i in prep.errors], [], d.name)

    def test_zip_structure_is_consistent(self):
        for d in find_solutions(ROOT):
            prep = builder.prepare(load_solution(d), use_examples=True)
            files = builder.solution_files(prep)
            sol = ET.fromstring(files["solution.xml"])
            cust = ET.fromstring(files["customizations.xml"])
            ET.fromstring(files["[Content_Types].xml"])
            root_ids = {r.get("id") for r in sol.iter("RootComponent") if r.get("type") == "29"}
            root_refs = {r.get("schemaName") for r in sol.iter("RootComponent") if r.get("type") == "connectionreference"}
            wf_ids = set()
            for wf in cust.iter("Workflow"):
                wf_ids.add(wf.get("WorkflowId"))
                member = wf.findtext("JsonFileName").lstrip("/")
                self.assertIn(member, files)
                doc = json.loads(files[member])
                for ref in doc["properties"]["connectionReferences"].values():
                    self.assertIn(ref["connection"]["connectionReferenceLogicalName"], root_refs)
                self.assertIn("$authentication", doc["properties"]["definition"]["parameters"])
            self.assertEqual(root_ids, wf_ids)
            declared = {c.get("connectionreferencelogicalname") for c in cust.iter("connectionreference")}
            self.assertEqual(declared, root_refs)


class Templates(TempRepo):
    def test_every_template_builds(self):
        templates = scaffold.list_templates(self.tmp)
        self.assertGreaterEqual(len(templates), 5)
        sol_dir = scaffold.new_solution(self.tmp, "AllTemplates")
        for i, t in enumerate(templates):
            scaffold.new_flow(self.tmp, sol_dir, f"Flow{i}", t["id"])
        prep = builder.prepare(load_solution(sol_dir), use_examples=True)
        self.assertEqual([i.format() for i in prep.errors], [])
        self.assertEqual(len(prep.flows), len(templates))
        for pf in prep.flows:
            self.assertEqual(params.find_tokens(pf.definition), set())

    def test_cli_end_to_end_and_reproducible(self):
        r = ["--root", str(self.tmp)]
        self.assertEqual(run_cli(*r, "new-solution", "Demo", "--display", "デモ")[0], 0)
        self.assertEqual(run_cli(*r, "new-flow", "Demo", "Mail", "-t", "scheduled-mail-office365")[0], 0)
        code, out = run_cli(*r, "build", "Demo")
        self.assertEqual(code, 1)  # MAIL_TO が未設定
        self.assertIn("MAIL_TO", out)
        self.assertEqual(run_cli(*r, "build", "Demo", "-p", "MAIL_TO=a@example.com", "--legacy")[0], 0)
        zip_path = self.tmp / "dist" / "Demo_1_0_0_0.zip"
        first = zip_path.read_bytes()
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            self.assertEqual(names[:3], ["solution.xml", "customizations.xml", "[Content_Types].xml"])
            wf = json.loads(zf.read([n for n in names if n.startswith("Workflows/")][0]))
        action = wf["properties"]["definition"]["actions"]["Send_an_email"]
        self.assertEqual(action["inputs"]["parameters"]["emailMessage/To"], "a@example.com")
        self.assertEqual(action["inputs"]["host"]["apiId"], "/providers/Microsoft.PowerApps/apis/shared_office365")
        self.assertEqual(action["inputs"]["authentication"], "@parameters('$authentication')")
        self.assertTrue((self.tmp / "dist" / "Demo_1_0_0_0_IMPORT.md").is_file())
        with zipfile.ZipFile(self.tmp / "dist" / "legacy" / "Demo_Mail_legacy.zip") as zf:
            self.assertIn("manifest.json", zf.namelist())
        self.assertEqual(run_cli(*r, "build", "Demo", "-p", "MAIL_TO=a@example.com")[0], 0)
        self.assertEqual(zip_path.read_bytes(), first)
        self.assertEqual(run_cli(*r, "bump", "Demo")[0], 0)
        self.assertEqual(load_solution(self.tmp / "solutions" / "Demo").version, "1.0.0.1")

    def test_roundtrip_import(self):
        sol_dir = scaffold.new_solution(self.tmp, "Round")
        scaffold.new_flow(self.tmp, sol_dir, "Notify", "sharepoint-new-item-to-teams")
        scaffold.new_flow(self.tmp, sol_dir, "Approve", "approval-basic")
        prep = builder.prepare(load_solution(sol_dir), use_examples=True)
        files = builder.solution_files(prep)
        zip_path = self.tmp / "round.zip"
        builder.write_zip(zip_path, files)
        other = self.tmp / "other"
        target = importer.import_solution_zip(zip_path, other)
        prep2 = builder.prepare(load_solution(target))
        self.assertEqual([i.format() for i in prep2.errors], [])
        self.assertEqual(builder.solution_files(prep2), files)


class Validation(unittest.TestCase):
    def test_valid_base(self):
        self.assertEqual(errors_of(BASE), [])

    def test_unknown_run_after(self):
        d = copy.deepcopy(BASE)
        d["actions"]["B"]["runAfter"] = {"Nope": ["Succeeded"]}
        self.assertTrue(any("Nope" in m for m in errors_of(d)))

    def test_bad_status_and_cycle(self):
        d = copy.deepcopy(BASE)
        d["actions"]["A"]["runAfter"] = {"B": ["Done"]}
        msgs = errors_of(d)
        self.assertTrue(any("Done" in m for m in msgs))
        self.assertTrue(any("循環" in m for m in msgs))

    def test_missing_reference_in_expression(self):
        d = copy.deepcopy(BASE)
        d["actions"]["B"]["inputs"] = "@{body('Ghost')?['x']} @{variables('v')} @{items('Loop')}"
        msgs = " ".join(errors_of(d))
        for word in ("Ghost", "'v'", "Loop"):
            self.assertIn(word, msgs)

    def test_duplicate_name_across_scopes(self):
        d = copy.deepcopy(BASE)
        d["actions"]["S"] = {"type": "Scope", "actions": {"A": {"type": "Compose", "inputs": 1}},
                             "runAfter": {"B": ["Succeeded"]}}
        self.assertTrue(any("重複" in m for m in errors_of(d)))

    def test_nested_initialize_variable_and_names(self):
        d = copy.deepcopy(BASE)
        d["actions"]["S"] = {"type": "Scope", "runAfter": {"B": ["Succeeded"]}, "actions": {
            "Init": {"type": "InitializeVariable", "inputs": {"variables": [{"name": "v", "type": "string"}]}}}}
        d["actions"]["Bad name"] = {"type": "Compose", "inputs": 1, "runAfter": {"S": ["Succeeded"]}}
        msgs = errors_of(d)
        self.assertTrue(any("最上位" in m for m in msgs))
        self.assertTrue(any("空白" in m for m in msgs))

    def test_connector_requires_operation(self):
        d = copy.deepcopy(BASE)
        d["actions"]["C"] = {"type": "OpenApiConnection", "runAfter": {"B": ["Succeeded"]},
                             "inputs": {"host": {"connectionName": "shared_teams"}}}
        self.assertTrue(any("operationId" in m for m in errors_of(d)))

    def test_trigger_count(self):
        d = copy.deepcopy(BASE)
        d["triggers"] = {}
        self.assertTrue(any("トリガー" in m for m in errors_of(d)))


class Units(unittest.TestCase):
    def test_ids_are_stable(self):
        self.assertEqual(naming.flow_guid("S", "F"), naming.flow_guid("S", "F"))
        self.assertNotEqual(naming.flow_guid("S", "F"), naming.flow_guid("S", "G"))
        self.assertRegex(naming.connref_logical_name("lab", "shared_teams", "S"), r"^lab_sharedteams_[0-9a-f]{5}$")
        self.assertEqual(naming.bump_version("1.2.3.4", "minor"), "1.3.0.0")

    def test_param_substitution(self):
        node = {"a": "{{N}}", "b": "x{{N}}y{{S}}", "c": ["{{MISSING}}"], "d": "@{json('{\"k\":{\"z\":1}}')}"}
        out = params.substitute(node, {"N": 5, "S": "s"})
        self.assertEqual(out, {"a": 5, "b": "x5ys", "c": ["{{MISSING}}"], "d": node["d"]})
        self.assertEqual(params.find_tokens(out), {"MISSING"})


if __name__ == "__main__":
    unittest.main()
