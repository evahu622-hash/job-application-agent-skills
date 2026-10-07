"""Tests for the resume variant builder; the integration test needs LibreOffice and poppler."""

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock
from xml.etree import ElementTree
from xml.sax.saxutils import escape

from assistant import GuardError, sha256_file
import render_resume
from render_resume import (
    apply_edit, apply_edits, approve, build, check_invariant, docx_plain_text, font_check,
    font_profile_xcu, image_check, load_config, load_manifest, make_variant_docx,
    paragraph_text, relative, render_pdf, scan_paragraphs, settle_status, soffice_version, status, text_checks,
)


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
BOLD = "<w:rPr><w:b/></w:rPr>"


def run(text: str, props: str = "", attrs: str = "") -> str:
    return f"<w:r>{props}<w:t{attrs}>{escape(text)}</w:t></w:r>"


def para(*runs: str) -> str:
    return f'<w:p w:rsidR="00A1">{"".join(runs)}</w:p>'


def document(*paragraphs: str) -> str:
    body = "".join(paragraphs)
    return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<w:document xmlns:w="{W_NS}"><w:body>{body}<w:sectPr/></w:body></w:document>')


def edit(anchor: str, old: str, new: str) -> dict:
    return {"paragraph_contains": anchor, "old": old, "new": new,
            "fact_ids": ["F-001"], "reason": "test"}


def texts(xml: str) -> list[str]:
    return [paragraph_text(p) for p in scan_paragraphs(xml)]


CORE = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title></dc:title>'
        '<dc:creator>Template Author</dc:creator></cp:coreProperties>')
CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '<Override PartName="/docProps/core.xml" '
    'ContentType="application/vnd.openxmlformats-package.core-properties+xml"/></Types>')
RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/>'
    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" '
    'Target="docProps/core.xml"/></Relationships>')


def write_docx(path: Path, xml: str) -> None:
    with zipfile.ZipFile(path, "w") as package:
        package.writestr("[Content_Types].xml", CONTENT_TYPES, compress_type=zipfile.ZIP_DEFLATED)
        package.writestr("_rels/", b"")
        package.writestr("_rels/.rels", RELS, compress_type=zipfile.ZIP_STORED)
        package.writestr("word/document.xml", xml, compress_type=zipfile.ZIP_DEFLATED)
        package.writestr("docProps/core.xml", CORE, compress_type=zipfile.ZIP_DEFLATED)


class EditEngineTest(unittest.TestCase):
    def test_single_run_edit_changes_only_that_text(self):
        master = document(para(run("Jane Example")), para(run("Product Lead | Berlin")), para(run("Lead the team")))
        result, index = apply_edit(master, edit("Product Lead | Berlin", "Product Lead", "Partner Manager"))
        self.assertEqual(1, index)
        self.assertEqual(master.replace(">Product Lead | Berlin<", ">Partner Manager | Berlin<"), result)
        check_invariant(master, result, [edit("Product Lead | Berlin", "Product Lead", "Partner Manager")], [index])

    def test_edit_spanning_runs_keeps_first_run_formatting_and_trims_others(self):
        master = document(para(run("Senior Pro", BOLD), run("duct Le"), run("ad"), run(" | Berlin")))
        change = edit("Senior Product Lead", "Product Lead", "Partner Manager")
        result, _ = apply_edits(master, [change])
        self.assertEqual(["Senior Partner Manager | Berlin"], texts(result))
        self.assertIn(f"{BOLD}<w:t>Senior Partner Manager</w:t>", result)
        self.assertIn("<w:r><w:t></w:t></w:r><w:r><w:t></w:t></w:r><w:r><w:t> | Berlin</w:t>", result)
        check_invariant(master, result, [change], [0])

    def test_preserve_is_added_when_text_gains_edge_spaces(self):
        master = document(para(run("Senior Product ", attrs=' xml:space="preserve"'), run("Lead Berlin")))
        result, _ = apply_edit(master, edit("Senior", "Product Lead", "Partner Manager"))
        self.assertEqual(["Senior Partner Manager Berlin"], texts(result))
        self.assertIn('<w:t xml:space="preserve">Senior Partner Manager</w:t>', result)
        self.assertIn('<w:t xml:space="preserve"> Berlin</w:t>', result)
        kept = document(para(run("Head", attrs=' xml:space="preserve"')))
        self.assertIn('<w:t xml:space="preserve">Head </w:t>', apply_edit(kept, edit("Head", "Head", "Head "))[0])

    def test_xml_special_characters_are_unescaped_for_matching_and_escaped_on_write(self):
        master = document(para(run("R&D <Ops> & Sales")))
        self.assertIn("R&amp;D &lt;Ops&gt;", master)
        change = edit("R&D <Ops>", "R&D", "Partners & <Channels>")
        result, _ = apply_edit(master, change)
        self.assertIn("<w:t>Partners &amp; &lt;Channels&gt; &lt;Ops&gt; &amp; Sales</w:t>", result)
        self.assertEqual(["Partners & <Channels> <Ops> & Sales"], texts(result))
        check_invariant(master, result, [change], [0])

    def test_refuses_zero_or_many_paragraphs_and_occurrences(self):
        master = document(para(run("Team lead")), para(run("Team lead again")), para(run("aaa bb bb")))
        with self.assertRaisesRegex(GuardError, "matched 0"):
            apply_edit(master, edit("Missing text", "x", "y"))
        with self.assertRaisesRegex(GuardError, "matched 2"):
            apply_edit(master, edit("Team lead", "lead", "manager"))
        with self.assertRaisesRegex(GuardError, "found 0"):
            apply_edit(master, edit("again", "absent", "y"))
        with self.assertRaisesRegex(GuardError, "found 2"):
            apply_edit(master, edit("aaa", "bb", "cc"))
        with self.assertRaisesRegex(GuardError, "found 2"):  # overlapping matches count too
            apply_edit(master, edit("aaa", "aa", "x"))

    def test_text_box_paragraph_and_its_anchor_paragraph_are_refused(self):
        box = f"<w:r><w:pict><w:txbxContent>{para(run('Sidebar text'))}</w:txbxContent></w:pict></w:r>"
        master = document(f'<w:p>{run("Headline ")}{box}{run("Title")}</w:p>', para(run("Body")))
        self.assertEqual(["Headline Title", "Sidebar text", "Body"], texts(master))
        with self.assertRaisesRegex(GuardError, "inside a text box"):
            apply_edit(master, edit("Sidebar", "Sidebar", "Side"))
        with self.assertRaisesRegex(GuardError, "contains a text box"):
            apply_edit(master, edit("Headline Title", "Headline Title", "New Title"))
        change = edit("Body", "Body", "Text")
        result, index = apply_edit(master, change)
        self.assertEqual(["Headline Title", "Sidebar text", "Text"], texts(result))
        check_invariant(master, result, [change], [index])

    def test_invariant_rejects_other_text_or_byte_changes(self):
        master = document(para(run("Alpha role", BOLD)), para(run("Beta role")))
        change = edit("Alpha", "Alpha", "Gamma")
        result, index = apply_edit(master, change)
        check_invariant(master, result, [change], [index])
        with self.assertRaisesRegex(GuardError, "unexpected text"):
            check_invariant(master, result.replace("Beta", "Delta"), [change], [index])
        with self.assertRaisesRegex(GuardError, "bytes outside"):
            check_invariant(master, result.replace("<w:b/>", "<w:i/>"), [change], [index])
        with self.assertRaisesRegex(GuardError, "paragraph count"):
            check_invariant(master, result.replace("<w:sectPr/>", "<w:p/><w:sectPr/>"), [change], [index])

    def test_variant_docx_copies_other_entries_and_sets_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            master, target = Path(folder) / "master.docx", Path(folder) / "out" / "variant.docx"
            write_docx(master, document(para(run("Jane Example")), para(run("Product Lead\tBerlin"))))
            text = make_variant_docx(master, target, [edit("Product Lead", "Product Lead", "Partner Manager")],
                                     {"title": "Example CV", "author": "Jane & Co"})
            self.assertIn("Partner Manager", text)
            with zipfile.ZipFile(master) as before, zipfile.ZipFile(target) as after:
                self.assertEqual(before.namelist(), after.namelist())
                for old, new in zip(before.infolist(), after.infolist()):
                    self.assertEqual(old.compress_type, new.compress_type)
                    if old.filename not in {"word/document.xml", "docProps/core.xml"}:
                        self.assertEqual(before.read(old), after.read(new))
                core = after.read("docProps/core.xml").decode()
                self.assertIn("<dc:title>Example CV</dc:title>", core)
                self.assertIn("<dc:creator>Jane &amp; Co</dc:creator>", core)
            with self.assertRaisesRegex(GuardError, "matched 0"):
                make_variant_docx(master, target, [edit("Nope", "x", "y")], {})

    def test_plain_text_separates_tabs_and_paragraphs(self):
        xml = document(para(run("Berlin"), "<w:r><w:tab/></w:r>", run("2020")), para(run("Next")))
        self.assertEqual({"Berlin", "2020", "Next"}, set(docx_plain_text(xml).split()))


class RenderSupportTest(unittest.TestCase):
    def test_font_profile_xcu_enables_replacement_table(self):
        xml = font_profile_xcu({"Arial Regular": "Arial", "Calibri": "Carlito & Co"})
        root = ElementTree.fromstring(xml)
        oor = "{http://openoffice.org/2001/registry}"
        items = root.findall("item")
        self.assertEqual("/org.openoffice.Office.Common/Font/Substitution", items[0].get(f"{oor}path"))
        self.assertEqual("true", items[0].find("prop/value").text)
        pairs = [{p.get(f"{oor}name"): p.find("value").text for p in item.iter("prop")} for item in items[1:]]
        self.assertEqual([
            {"Always": "true", "OnScreenOnly": "false", "ReplaceFont": "Arial Regular", "SubstituteFont": "Arial"},
            {"Always": "true", "OnScreenOnly": "false", "ReplaceFont": "Calibri", "SubstituteFont": "Carlito & Co"},
        ], pairs)
        self.assertEqual(["_0", "_1"], [item.find("node").get(f"{oor}name") for item in items[1:]])
        self.assertEqual("false", ElementTree.fromstring(font_profile_xcu({})).find("item/prop/value").text)

    @unittest.skipUnless(shutil.which("false"), "needs the false command")
    def test_render_failure_explains_sandbox_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            renderer = {"soffice": shutil.which("false"), "font_replacements": {}, "timeout_seconds": 30}
            with self.assertRaisesRegex(GuardError, "cannot run inside the Codex sandbox; run this build in a normal terminal"):
                render_pdf(Path(folder) / "cv.docx", renderer, Path(folder))
            with self.assertRaisesRegex(GuardError, "Codex sandbox"):
                soffice_version(renderer["soffice"])
        with self.assertRaisesRegex(GuardError, "brew install --cask libreoffice"):
            soffice_version("/nonexistent/soffice")

    CHECKS = {"min_word_coverage": 0.995, "required_phrases": ["Partner   Manager", "jane@example.com"]}

    def test_coverage_ligature_and_phrase_checks(self):
        docx = "Jane Example\nPartner Manager\nQualification certificate jane@example.com 2024"
        good = text_checks(docx, "JANE EXAMPLE\npartner manager\nQuali-\nfication certificate\njane@example.com 2024", self.CHECKS)
        self.assertTrue(all(check["ok"] for check in good.values()), good)
        garbled = text_checks(docx, "Jane Example Partner Manager Quaalifiecation certiﬁcate jane@example.com 2024", self.CHECKS)
        self.assertFalse(garbled["ligatures"]["ok"])
        self.assertIn("U+FB01", garbled["ligatures"]["detail"])
        self.assertFalse(garbled["word_coverage"]["ok"])
        self.assertIn("qualification", garbled["word_coverage"]["detail"])
        cid = text_checks(docx, docx + " (cid:12)", self.CHECKS)
        self.assertFalse(cid["ligatures"]["ok"])
        missing = text_checks(docx, "Jane Example", self.CHECKS)
        self.assertFalse(missing["required_phrases"]["ok"])
        self.assertIn("jane@example.com", missing["required_phrases"]["detail"])

    def test_missing_words_list_is_capped_at_twenty(self):
        docx = " ".join(f"word{n:02d}" for n in range(30))
        result = text_checks(docx, "", {"min_word_coverage": 0.5, "required_phrases": []})
        listed = result["word_coverage"]["detail"].split("missing (30): ")[1].split(", ")
        self.assertEqual(20, len(listed))

    def test_cjk_line_wraps_do_not_count_as_missing_words(self):
        docx = "负责渠道合作伙伴管理"
        result = text_checks(docx, "负责渠道合作\n伙伴管理\n", {"min_word_coverage": 1.0, "required_phrases": []})
        self.assertTrue(result["word_coverage"]["ok"], result)

    def test_poppler_table_parsing(self):
        fonts = (
            "name                                 type              encoding         emb sub uni object ID\n"
            "------------------------------------ ----------------- ---------------- --- --- --- ---------\n"
            "BAAAAA+ArialMT                       CID TrueType      Identity-H       yes yes yes      9  0\n"
            "Wingdings                            Type 3            Custom           no  no  no      12  0\n")
        result = font_check(fonts)
        self.assertFalse(result["ok"])
        self.assertIn("not embedded: Wingdings", result["detail"])
        self.assertIn("Type 3: Wingdings", result["detail"])
        self.assertTrue(font_check("".join(fonts.splitlines(keepends=True)[:3]))["ok"])
        carlito = fonts.splitlines(keepends=True)[:2] + [
            "CAAAAA+Carlito-Bold                  TrueType          WinAnsi          yes yes yes      9  0\n"]
        named = font_check("".join(carlito), {"Calibri": "Carlito", "Cambria": "Helvetica"})
        self.assertTrue(named["ok"])
        self.assertIn("1 font(s): Carlito-Bold;", named["detail"])
        self.assertIn("WARNING replacement font(s) not in the PDF: Helvetica", named["detail"])
        self.assertNotIn("WARNING", font_check("".join(carlito), {"Calibri": "Carlito"})["detail"])
        images = (
            "page   num  type   width height color comp bpc  enc interp  object ID x-ppi y-ppi size ratio\n"
            "--------------------------------------------------------------------------------------------\n"
            "   1     0 image     300   400  rgb     3   8  jpeg   no        10  0   150   150 41.2K 11%\n"
            "   1     1 smask     300   400  gray    1   8  image  no        10  0   150   150  1K  1%\n")
        self.assertEqual({"ok": True, "detail": "1 image(s); minimum 1"}, image_check(images, 1))
        self.assertFalse(image_check(images, 2)["ok"])


class ManifestTest(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.root = Path(self.workspace.name)
        self.config = self.root / "variants.json"
        self.config.write_text(json.dumps({
            "schema_version": 1, "master": "master.docx", "upload_filename": "Jane_Example_CV.pdf",
            "output_dir": "build", "variants": [
                {"id": "base", "role_family": "general", "description": "Master as is", "edits": []},
                {"id": "broken", "role_family": "general", "description": "Failed", "edits": []},
            ]}), encoding="utf-8")
        (self.root / "build" / "base").mkdir(parents=True)
        self.pdf = self.root / "build" / "base" / "Jane_Example_CV.pdf"
        self.pdf.write_bytes(b"%PDF-1.7\nfixture")
        self.manifest = self.root / "build" / "manifest.json"
        self.write_manifest()

    def entry(self, variant_id, status, **changes):
        value = {"id": variant_id, "status": status, "pdf": "build/base/Jane_Example_CV.pdf",
                 "pdf_sha256": sha256_file(self.pdf), "checks": {"pages": {"ok": True, "detail": "1 page(s)"}},
                 "approved_by": None, "approved_at": None}
        value.update(changes)
        return value

    def write_manifest(self, **changes):
        variants = [self.entry("base", "draft"),
                    self.entry("broken", "failed", checks={"word_coverage": {"ok": False, "detail": "90%"}})]
        payload = {"schema_version": 1, "variants": variants}
        payload.update(changes)
        self.manifest.write_text(json.dumps(payload), encoding="utf-8")

    def test_rebuild_with_new_pdf_hash_resets_approval(self):
        approved = self.entry("base", "approved", pdf_sha256="a" * 64, approved_by="Jane", approved_at="2026-10-01T00:00:00Z")
        same = settle_status(self.entry("base", "failed", pdf_sha256="a" * 64), approved)
        self.assertEqual(("approved", "Jane"), (same["status"], same["approved_by"]))
        changed = settle_status(self.entry("base", "failed", pdf_sha256="b" * 64), approved)
        self.assertEqual(("draft", None, None), (changed["status"], changed["approved_by"], changed["approved_at"]))
        failing = self.entry("base", "draft", pdf_sha256="a" * 64, checks={"images": {"ok": False, "detail": "0"}})
        self.assertEqual("failed", settle_status(failing, approved)["status"])

    def test_approve_records_reviewer_and_refuses_failed_or_changed_pdf(self):
        result = approve(self.config, self.root, "base", "Jane", at="2026-10-08T09:00:00Z")
        self.assertEqual(("approved", "Jane", "2026-10-08T09:00:00Z"),
                         (result["status"], result["approved_by"], result["approved_at"]))
        self.assertEqual("approved", load_manifest(self.manifest)["variants"][0]["status"])
        with self.assertRaisesRegex(GuardError, "failed checks"):
            approve(self.config, self.root, "broken", "Jane")
        with self.assertRaisesRegex(GuardError, "--by"):
            approve(self.config, self.root, "base", "  ")
        self.pdf.write_bytes(b"%PDF-1.7\nreplaced after build")
        with self.assertRaisesRegex(GuardError, "changed since build"):
            approve(self.config, self.root, "base", "Jane")
        rows = {row["id"]: row for row in status(self.config, self.root)["variants"]}
        self.assertFalse(rows["base"]["pdf_matches_manifest"])
        self.assertEqual(["word_coverage"], rows["broken"]["failed_checks"])

    def test_malformed_manifest_is_rejected(self):
        self.write_manifest(variants=[{"id": "base", "status": "ok"}])
        with self.assertRaisesRegex(GuardError, "Malformed manifest"):
            approve(self.config, self.root, "base", "Jane")

    def test_config_validation(self):
        base = json.loads(self.config.read_text(encoding="utf-8"))
        cases = {
            "id must match": {"variants": [{"id": "Bad_ID", "role_family": "", "description": "", "edits": []}]},
            "unique": {"variants": [base["variants"][0], base["variants"][0]]},
            "fact_ids": {"variants": [{"id": "x", "role_family": "", "description": "", "edits": [
                {"paragraph_contains": "a", "old": "a", "new": "b", "fact_ids": [], "reason": "r"}]}]},
            "upload_filename": {"upload_filename": "../cv.pdf"},
            "schema_version": {"schema_version": 2},
        }
        for message, change in cases.items():
            with self.subTest(message):
                self.config.write_text(json.dumps({**base, **change}), encoding="utf-8")
                with self.assertRaisesRegex(GuardError, message):
                    load_config(self.config)
        self.config.write_text(json.dumps(base), encoding="utf-8")
        loaded = load_config(self.config)
        self.assertEqual(0.995, loaded["checks"]["min_word_coverage"])
        self.assertEqual(180, loaded["renderer"]["timeout_seconds"])


class BuildFlowTest(unittest.TestCase):
    """build() bookkeeping with rendering replaced by a fake; no LibreOffice needed."""

    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.root = Path(self.workspace.name) / "repo"
        self.root.mkdir()
        (self.root / "master.docx").write_bytes(b"docx bytes")
        self.config = self.root / "variants.json"
        self.variants = [{"id": "csm", "role_family": "customer-success", "description": "CSM", "edits": []},
                         {"id": "ops", "role_family": "operations", "description": "Ops", "edits": []}]
        self.write_config()
        self.renders = 0
        for name, value in (("require_poppler", lambda: None), ("soffice_version", lambda soffice: "LO 1"),
                            ("build_variant", self.fake_build_variant)):
            patcher = mock.patch.object(render_resume, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def write_config(self, **changes):
        config = {"schema_version": 1, "master": "master.docx", "upload_filename": "Jane_Example_CV.pdf",
                  "output_dir": "private/build", "variants": self.variants}
        config.update(changes)
        self.config.write_text(json.dumps(config), encoding="utf-8")

    def fake_build_variant(self, root, config, variant, master, digest, previous):
        self.renders += 1
        pdf = root / config["output_dir"] / variant["id"] / config["upload_filename"]
        pdf.parent.mkdir(parents=True, exist_ok=True)
        pdf.write_bytes(b"%%PDF-1.7 render %d" % self.renders)  # LibreOffice output differs per build
        entry = {"id": variant["id"], "role_family": variant["role_family"], "description": variant["description"],
                 "input_sha256": digest, "pdf": relative(root, pdf), "pdf_sha256": sha256_file(pdf),
                 "checks": {"pages": {"ok": True, "detail": "1 page(s)"}}, "edits": variant["edits"]}
        return settle_status(entry, previous)

    def build(self, **options):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = build(self.config, self.root, **options)
        return {v["id"]: v for v in result["variants"]}, stderr.getvalue()

    def manifest(self):
        return {v["id"]: v for v in load_manifest(self.root / "private" / "build" / "manifest.json")["variants"]}

    def test_display_metadata_follows_config_without_rebuild_and_removed_variants_go(self):
        self.build()
        approve(self.config, self.root, "csm", "Jane")
        self.variants[0]["role_family"] = "account-management"
        self.variants = self.variants[:1]
        self.write_config()
        results, _ = self.build()
        self.assertEqual((True, "approved"), (results["csm"]["skipped"], results["csm"]["status"]))
        self.assertEqual(["csm"], list(self.manifest()))
        self.assertEqual(("account-management", "approved"),
                         (self.manifest()["csm"]["role_family"], self.manifest()["csm"]["status"]))
        self.assertEqual(2, self.renders)

    def test_forced_rebuild_says_that_approval_was_cleared(self):
        self.build()
        approve(self.config, self.root, "csm", "Jane")
        results, stderr = self.build(only="csm", force=True)
        self.assertEqual("draft", results["csm"]["status"])
        self.assertIn("Approval cleared for csm (the PDF changed", stderr)
        self.assertEqual("", self.build(only="ops", force=True)[1])  # ops was never approved

    def test_output_inside_repository_and_no_absolute_paths_in_manifest(self):
        self.write_config(output_dir="../outside")
        with self.assertRaisesRegex(GuardError, "output_dir must be inside the repository"):
            self.build()
        elsewhere = Path(self.workspace.name) / "cv-master.docx"
        elsewhere.write_bytes(b"docx bytes")
        self.write_config(master=str(elsewhere))
        self.build()
        manifest = load_manifest(self.root / "private" / "build" / "manifest.json")
        self.assertEqual("cv-master.docx", manifest["master"])
        self.assertNotIn(self.workspace.name, json.dumps(manifest))


TOOLS = ("soffice", "pdftotext", "pdfinfo", "pdffonts", "pdfimages", "pdftoppm")


@unittest.skipUnless(all(shutil.which(tool) for tool in TOOLS), "needs LibreOffice and poppler")
@unittest.skipIf(os.environ.get("CODEX_SANDBOX"),
                 "LibreOffice cannot run inside the Codex sandbox; run this test in a normal terminal")
class RenderIntegrationTest(unittest.TestCase):
    def test_build_approve_and_skip_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write_docx(root / "master.docx", document(
                para(run("Jane Example | Product Lead", BOLD)),
                para(run("Office workflow certification for financial filings")),
                para(run("Contact: jane@example.com")),
            ))
            config = root / "variants.json"
            config.write_text(json.dumps({
                "schema_version": 1, "master": "master.docx", "upload_filename": "Jane_Example_CV.pdf",
                "output_dir": "build", "metadata": {"title": "Jane Example CV", "author": "Jane Example"},
                "checks": {"expected_pages": 1, "required_phrases": ["jane@example.com"]},
                "variants": [
                    {"id": "base", "role_family": "general", "description": "Master", "edits": []},
                    {"id": "partner", "role_family": "partnerships", "description": "Partner headline",
                     "edits": [edit("Jane Example |", "Product Lead", "Partner Manager")]},
                ]}), encoding="utf-8")
            result = build(config, root)
            for variant in result["variants"]:
                self.assertEqual("draft", variant["status"], variant["checks"])
            manifest = load_manifest(root / "build" / "manifest.json")
            partner = manifest["variants"][1]
            self.assertEqual("build/partner/Jane_Example_CV.pdf", partner["pdf"])
            self.assertTrue(partner["previews"] and (root / partner["previews"][0]).is_file())
            self.assertEqual(1, partner["pages"])
            approve(config, root, "partner", "Jane")
            again = build(config, root, only="partner")
            self.assertEqual([("partner", "approved", True)],
                             [(v["id"], v["status"], v["skipped"]) for v in again["variants"]])


if __name__ == "__main__":
    unittest.main()
