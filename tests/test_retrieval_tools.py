from __future__ import annotations

import importlib.util
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO = Path(__file__).resolve().parents[1]
DISCOVERY_DIR = REPO / "skills" / "review-topic-paper-discovery" / "scripts"
DISCOVER = DISCOVERY_DIR / "discover.py"
CREATE_PROJECT = DISCOVERY_DIR / "create_project.py"
INGEST = DISCOVERY_DIR / "ingest_external_papers.py"
MINERU = (
    REPO
    / "skills"
    / "mineru-precise-parse-chemvellum"
    / "scripts"
    / "parse_chemvellum_pdfs.py"
)
FIGURES = (
    REPO
    / "skills"
    / "review-source-figure-tools"
    / "scripts"
    / "build_paper_figure_inventory.py"
)
METADATA = (
    REPO
    / "skills"
    / "review-metadata-prep"
    / "scripts"
    / "prepare_metadata.py"
)


def load_module(name: str, path: Path):
    sys.path.insert(0, str(path.parent))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


class RetrievalToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.projects = load_module("review_project_allocator", CREATE_PROJECT)
        cls.discovery = load_module("review_discovery_tools", DISCOVER)
        cls.ingest = load_module("review_ingest_tools", INGEST)
        cls.figures = load_module("review_figure_inventory_tools", FIGURES)
        cls.metadata = load_module("review_metadata_tools", METADATA)
        cls.mineru = load_module("review_mineru_tools", MINERU)

    def test_review_and_experiment_ids_are_allocated_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = self.projects.ensure_review_project(
                root,
                "Mechanistic selectivity in catalytic depolymerization",
            )
            second = self.projects.ensure_review_project(
                root,
                "Electrochemical polymer upcycling",
            )
            experiment_one = self.projects.ensure_experiment(
                root,
                "retrieval smoke test",
                date_key="20260801",
            )
            experiment_two = self.projects.ensure_experiment(
                root,
                "rendering smoke test",
                date_key="20260801",
            )

            self.assertTrue(first["project_id"].startswith("CVR-0001-"))
            self.assertTrue(second["project_id"].startswith("CVR-0002-"))
            self.assertTrue(experiment_one["experiment_id"].startswith("EXP-20260801-001-"))
            self.assertTrue(experiment_two["experiment_id"].startswith("EXP-20260801-002-"))
            project = root / "review-projects" / first["project_id"]
            self.assertTrue((project / "project.json").exists())
            self.assertTrue((project / "manuscript.md").exists())
            for directory in ("00_discovery", "assets", "deliverables", "notes", "runs"):
                self.assertTrue((project / directory).is_dir())

    def test_project_allocator_rejects_escaping_ids_and_releases_its_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError):
                self.projects.ensure_review_project(
                    root,
                    "unsafe project",
                    project_id="../outside",
                )
            lock_path = root / "review-projects" / ".project_registry.lock"
            with self.projects.file_lock(lock_path, timeout=0.1):
                with self.assertRaises(TimeoutError):
                    with self.projects.file_lock(lock_path, timeout=0.05):
                        pass
            with self.projects.file_lock(lock_path, timeout=0.1):
                pass

    def test_discovery_rerun_archives_the_previous_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = self.projects.ensure_review_project(root, "A review topic")
            project = root / "review-projects" / manifest["project_id"]
            run_id = "20260801T010203000000Z-123"
            self.projects.record_discovery_run(
                project,
                run_id,
                "A review topic",
                "completed",
            )
            (project / "00_discovery" / "combined_results_by_keyword.json").write_text(
                "{}\n",
                encoding="utf-8",
            )

            archive = self.projects.archive_current_discovery(project)

            self.assertEqual(archive, project / "runs" / run_id / "discovery")
            self.assertTrue((archive / "combined_results_by_keyword.json").exists())
            self.assertEqual(list((project / "00_discovery").iterdir()), [])

    def test_metadata_registry_lock_excludes_a_second_writer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "papers.jsonl.lock"
            first = self.metadata.RegistryFileLock(lock_path, timeout=0.1)
            second = self.metadata.RegistryFileLock(lock_path, timeout=0.05)
            first.acquire()
            try:
                with self.assertRaises(TimeoutError):
                    second.acquire()
            finally:
                first.release()
            second.acquire()
            second.release()

    def test_metadata_registry_identity_matches_relative_and_absolute_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            absolute_pdf = root / "chem_papers" / "paper.pdf"
            relative = self.metadata.registry_key(
                {"source_pdf": "chem_papers/paper.pdf"},
                root,
            )
            absolute = self.metadata.registry_key(
                {"source_pdf": str(absolute_pdf)},
                root,
            )
            self.assertEqual(relative, absolute)

    def test_discovery_progress_is_immediate_and_machine_output_safe(self) -> None:
        stream = io.StringIO()
        with contextlib.redirect_stderr(stream):
            self.discovery.progress("Crossref request started")
        rendered = stream.getvalue()
        self.assertIn("[discover ", rendered)
        self.assertIn("Crossref request started", rendered)

    def test_metadata_cleans_jats_inline_spacing_and_footnote_markers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "paper.jats.xml"
            path.write_text(
                "<?xml version='1.0' encoding='UTF-8'?>"
                "<article><front><article-meta><title-group><article-title>"
                "Minimal <italic>N</italic>-hydroxy bond exchange"
                "<xref ref-type='fn'>†</xref></article-title></title-group>"
                "<contrib-group><contrib contrib-type='author'><name>"
                "<surname>Lovelace</surname><given-names>Ada</given-names>"
                "</name></contrib></contrib-group>"
                "<abstract><p>First paragraph.</p><p>Second paragraph.</p></abstract>"
                "<pub-date><year>2025</year></pub-date>"
                "</article-meta></front></article>",
                encoding="utf-8",
            )
            result = self.metadata.extract_jats_metadata(path)
            written = Path(tmp) / "metadata.json"
            self.metadata.write_json(written, result)
            round_trip = json.loads(written.read_text(encoding="utf-8"))

        self.assertEqual(result["title"], "Minimal N-hydroxy bond exchange")
        self.assertEqual(result["authors"], ["Ada Lovelace"])
        self.assertEqual(result["abstract"], "First paragraph. Second paragraph.")
        self.assertEqual(round_trip, result)

    def test_metadata_author_extraction_ignores_copyright_statements(self) -> None:
        blocks = [
            {"type": "text", "text": "A useful chemistry paper", "text_level": 1},
            {"type": "text", "text": "Ada Lovelace, Grace Hopper"},
            {"type": "text", "text": "DOI: 10.1000/example"},
            {"type": "text", "text": "© The Author(s) 2025"},
        ]
        authors = self.metadata.extract_authors(blocks, "A useful chemistry paper")
        self.assertEqual(authors["value"], ["Ada Lovelace", "Grace Hopper"])

    def test_ingest_jats_text_preserves_inline_markup_adjacency(self) -> None:
        element = self.ingest.ET.fromstring(
            "<article-title>Minimal <italic>N</italic>-hydroxy exchange"
            "<xref ref-type='fn'>†</xref></article-title>"
        )
        rendered = self.ingest._xml_text(
            element,
            skip_xref_types={"fn"},
        )
        self.assertEqual(rendered, "Minimal N-hydroxy exchange")

    def test_mineru_writes_latest_and_per_run_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "chem_papers"
            output_dir = root / "mineru-outputs"
            input_dir.mkdir()
            source = input_dir / "paper.pdf"
            source.write_bytes(b"%PDF-1.4\n")
            args = SimpleNamespace(
                input_dir=input_dir,
                output_dir=output_dir,
                pdf=[],
                force=False,
                limit=0,
                language="en",
                model_version="vlm",
                disable_formula=False,
                disable_table=False,
                ocr=False,
                batch_size=10,
                poll_interval=1,
                timeout_minutes=1,
            )
            original_parse_args = self.mineru.parse_args
            original_resolve_token = self.mineru.resolve_token
            original_discover_jobs = self.mineru.discover_jobs
            original_run_batch = self.mineru.run_batch
            self.mineru.parse_args = lambda: args
            self.mineru.resolve_token = lambda _args: "token"
            self.mineru.discover_jobs = lambda *_args: [
                self.mineru.ParseJob(1, source, input_dir, "paper", "paper-1")
            ]

            def fake_run_batch(_session, _token, jobs, _args, _output, manifest):
                manifest["completed"].append({"slug": jobs[0].slug})

            self.mineru.run_batch = fake_run_batch
            try:
                self.assertEqual(self.mineru.main(), 0)
            finally:
                self.mineru.parse_args = original_parse_args
                self.mineru.resolve_token = original_resolve_token
                self.mineru.discover_jobs = original_discover_jobs
                self.mineru.run_batch = original_run_batch

            latest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            run_manifests = list((output_dir / "runs").glob("*.json"))
            self.assertEqual(len(run_manifests), 1)
            archived = json.loads(run_manifests[0].read_text(encoding="utf-8"))
            self.assertEqual(archived["run_id"], latest["run_id"])

    def test_generic_query_plan_does_not_leak_allene_vocabulary(self) -> None:
        rows = self.discovery.infer_keywords(
            "enzymatic PET depolymerization",
            [],
            {
                "manuscript_title": "Enzymatic depolymerization of PET waste",
                "central_question": (
                    "How do feedstock crystallinity and enzyme engineering "
                    "change depolymerization performance?"
                ),
                "important_coverage": [
                    "feedstock crystallinity",
                    "enzyme engineering",
                    "reaction conditions",
                ],
            },
        )
        rendered = json.dumps(rows).lower()
        self.assertIn("feedstock crystallinity", rendered)
        self.assertIn("enzyme engineering", rendered)
        coverage_rows = [row for row in rows if row["category"] == "coverage"]
        self.assertTrue(
            all("enzymatic" in row["keyword"].lower() for row in coverage_rows)
        )
        self.assertTrue(all(len(row["keyword"].split()) <= 7 for row in coverage_rows))
        self.assertNotIn("allene", rendered)
        self.assertNotIn("propargyl", rendered)

    def test_dense_coverage_keeps_later_method_families_searchable(self) -> None:
        rows = self.discovery.infer_keywords(
            "polysubstituted allenes from propargylic alcohols",
            [],
            {
                "manuscript_title": (
                    "Synthesis of polysubstituted allenes from propargylic alcohols"
                ),
                "important_coverage": [
                    "reduction, substitution, rearrangement, cross-coupling, "
                    "tandem, photoredox, electrochemical, organocatalytic"
                ],
            },
        )
        coverage = " ".join(
            row["keyword"].lower()
            for row in rows
            if row["category"] == "coverage"
        )
        self.assertIn("reduction", coverage)
        self.assertIn("photoredox", coverage)
        self.assertIn("organocatalytic", coverage)

    def test_external_screen_keeps_sparse_but_topical_titles(self) -> None:
        relevance = self.discovery.external_relevance(
            "polysubstituted allenes propargylic reduction coupling",
            "synthesis of polysubstituted allenes from propargylic alcohol derivatives",
            "Nickel coupling of propargylic carbonates to allenes",
        )
        self.assertTrue(relevance["passed"])
        unrelated = self.discovery.external_relevance(
            "polysubstituted allenes propargylic reduction coupling",
            "synthesis of polysubstituted allenes from propargylic alcohol derivatives",
            "Nickel coupling of aryl carbonates",
        )
        self.assertFalse(unrelated["passed"])

    def test_external_query_cap_samples_each_coverage_facet_first(self) -> None:
        groups = [
            {"keyword": "core", "facet_group": "core_topic"},
            {"keyword": "route-a-1", "facet_group": "coverage_0"},
            {"keyword": "route-a-2", "facet_group": "coverage_0"},
            {"keyword": "route-b-1", "facet_group": "coverage_1"},
            {"keyword": "route-b-2", "facet_group": "coverage_1"},
        ]
        chosen = self.discovery.choose_external_groups(groups, 3)
        self.assertEqual(
            [row["keyword"] for row in chosen],
            ["core", "route-a-1", "route-b-1"],
        )

    def test_corpus_plan_exposes_set_level_coverage_before_ingestion(self) -> None:
        topic_contract = {
            "central_question": "How do catalyst state and substrate scope trade off?",
            "important_coverage": [
                "catalyst speciation and oxidation state",
                "substrate scope and selectivity boundaries",
            ],
        }
        combined = [
            {
                "keyword": "catalyst speciation",
                "category": "mechanism",
                "keep": True,
                "local_results": [],
                "web_results": [
                    {
                        "external_id": "review-paper",
                        "title": "Review of catalyst speciation and oxidation state",
                        "abstract": "A mechanistic overview of catalyst speciation and oxidation state.",
                        "year": 2025,
                        "score": 1.2,
                        "source": "europe_pmc",
                        "repository_full_text_url": "https://example.org/review.xml",
                        "repository_format": "jats_xml",
                        "keep": True,
                    }
                ],
            },
            {
                "keyword": "substrate scope",
                "category": "document_scope",
                "keep": True,
                "local_results": [],
                "web_results": [
                    {
                        "external_id": "primary-paper",
                        "title": "Substrate scope and selectivity boundaries in catalysis",
                        "abstract": "Primary experiments compare substrate scope and selectivity boundaries.",
                        "year": 2024,
                        "score": 1.0,
                        "source": "openalex",
                        "open_access_pdf_url": "https://example.org/primary.pdf",
                        "keep": True,
                    }
                ],
            },
        ]

        selected = self.discovery.selected_from_combined(
            combined,
            topic_contract=topic_contract,
        )
        ingest_plan = self.discovery.build_external_ingest_plan(
            "CVR-TEST",
            selected["web_papers"],
            True,
        )
        corpus_plan = self.discovery.build_corpus_plan_draft(
            "CVR-TEST",
            "RUN-TEST",
            topic_contract,
            ingest_plan,
        )

        self.assertEqual(corpus_plan["importable_candidate_count"], 2)
        self.assertEqual(corpus_plan["selection"]["selected_paper_keys"], [])
        self.assertIn("review-paper", corpus_plan["orientation_candidate_paper_keys"])
        self.assertTrue(
            all(axis["candidate_paper_keys"] for axis in corpus_plan["coverage_axes"])
        )
        self.assertTrue(
            all("ranking_score" in item for item in ingest_plan["items"])
        )
        self.assertTrue(
            all("important_coverage_hits" in item for item in ingest_plan["items"])
        )

    def test_corpus_plan_selection_imports_the_whole_explicit_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "corpus_plan.json"
            path.write_text(
                json.dumps(
                    {
                        "project_id": "CVR-TEST",
                        "discovery_run_id": "RUN-TEST",
                        "selection": {
                            "selected_paper_keys": ["paper-a", "paper-b", "paper-a"]
                        },
                    }
                ),
                encoding="utf-8",
            )
            keys = self.ingest.corpus_plan_paper_keys(
                path,
                "CVR-TEST",
                "RUN-TEST",
            )
        plan = {
            "items": [
                {"paper_key": "paper-a", "action": "download_then_mineru"},
                {"paper_key": "paper-b", "action": "ingest_repository_full_text"},
                {"paper_key": "paper-c", "action": "download_then_mineru"},
            ]
        }
        chosen = self.ingest.select_items(plan, keys, limit=1)
        self.assertEqual(keys, ["paper-a", "paper-b"])
        self.assertEqual([row["paper_key"] for row in chosen], keys)
        self.assertEqual(
            self.ingest.unresolved_requested_paper_keys(
                chosen,
                ["paper-a", "paper-b", "missing-paper"],
            ),
            ["missing-paper"],
        )

    def test_promoted_paper_ids_accumulate_across_ingest_batches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            discovery_dir = Path(tmp)
            selected_path = discovery_dir / "selected_discovery_results.json"
            selected_path.write_text(
                json.dumps({"local_papers": []}),
                encoding="utf-8",
            )
            self.ingest.add_promotions_to_candidates(
                discovery_dir,
                [({"paper_key": "external-a"}, "P900", {"title": "First"})],
            )
            self.ingest.add_promotions_to_candidates(
                discovery_dir,
                [({"paper_key": "external-b"}, "P901", {"title": "Second"})],
            )
            selected = json.loads(selected_path.read_text(encoding="utf-8"))

        self.assertEqual(selected["newly_ingested_paper_ids"], ["P900", "P901"])
        self.assertEqual(selected["latest_ingested_paper_ids"], ["P901"])
        self.assertEqual(selected["candidate_paper_ids"], ["P900", "P901"])

    def test_markdown_topic_input_accepts_common_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "topic.md"
            path.write_text(
                "# Enzymatic PET recycling\n\n"
                "## Central question\n"
                "How do substrate state and enzyme design interact?\n\n"
                "## Important coverage\n"
                "- substrate crystallinity\n"
                "- enzyme engineering\n\n"
                "## Inclusion criteria\n"
                "- full experimental studies\n"
                "  with reported reaction conditions\n\n"
                "## Exclusion criteria\n"
                "- abstracts without full text\n",
                encoding="utf-8",
            )
            payload = self.discovery.load_topic_contract_file(str(path))
        self.assertEqual(payload["topic"], "Enzymatic PET recycling")
        self.assertEqual(len(payload["important_coverage"]), 2)
        self.assertIn(
            "with reported reaction conditions",
            payload["inclusion_criteria"][0],
        )

    def test_crossref_filter_removes_editorial_and_supplement_records(self) -> None:
        bad = [
            {"type": "peer-review", "title": ["Review report"], "DOI": "10.1/x"},
            {
                "type": "journal-article",
                "title": ["Supporting information"],
                "DOI": "10.1/x.s1",
            },
            {
                "type": "journal-article",
                "title": ["Author response"],
                "DOI": "10.1/x/v2/response1",
            },
        ]
        self.assertTrue(
            all(self.discovery.is_excluded_crossref_record(row) for row in bad)
        )
        self.assertFalse(
            self.discovery.is_excluded_crossref_record(
                {
                    "type": "journal-article",
                    "title": ["A primary research article"],
                    "DOI": "10.1/article",
                }
            )
        )

    def test_provider_status_distinguishes_failure_from_no_results(self) -> None:
        self.assertEqual(
            self.discovery.provider_run_status(
                True,
                {"attempted_queries": 2, "successful_queries": 0},
                ["HTTP 429"],
            ),
            "error",
        )
        self.assertEqual(
            self.discovery.provider_run_status(
                True,
                {
                    "attempted_queries": 2,
                    "successful_queries": 2,
                    "returned_records": 0,
                },
                [],
            ),
            "no_results",
        )

    def test_crossref_request_retries_a_transient_read_failure(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"message": {"items": []}}'

        calls = 0
        original_urlopen = self.discovery.urllib.request.urlopen
        original_sleep = self.discovery.time.sleep

        def fake_urlopen(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise self.discovery.urllib.error.URLError("temporary EOF")
            return FakeResponse()

        try:
            self.discovery.urllib.request.urlopen = fake_urlopen
            self.discovery.time.sleep = lambda _seconds: None
            payload = self.discovery.crossref_request_json(
                "https://api.crossref.org/works"
            )
        finally:
            self.discovery.urllib.request.urlopen = original_urlopen
            self.discovery.time.sleep = original_sleep

        self.assertEqual(payload, {"message": {"items": []}})
        self.assertEqual(calls, 2)

    def test_reference_expansion_reuses_a_review_found_by_another_provider(
        self,
    ) -> None:
        grouped = [
            {
                "keyword": "nickel photoredox",
                "web_results": [
                    {
                        "doi": "10.1000/review",
                        "title": "Nickel photoredox catalysis for organic synthesis",
                        "publication_types": ["Review", "Journal Article"],
                        "keep": True,
                    },
                    {
                        "doi": "10.1000/primary",
                        "title": "A primary coupling study",
                        "publication_types": ["Journal Article"],
                        "keep": True,
                    },
                ],
            }
        ]
        hints = self.discovery.review_seed_hints_from_grouped(grouped)
        self.assertEqual([row["DOI"] for row in hints], ["10.1000/review"])

        original_search = self.discovery.crossref_search_items
        original_fetch = self.discovery.fetch_crossref_work
        original_fetch_many = self.discovery.fetch_crossref_works
        self.discovery.crossref_search_items = lambda *_args, **_kwargs: []
        self.discovery.fetch_crossref_work = lambda _doi: {
            "DOI": "10.1000/review",
            "title": ["Nickel photoredox catalysis for organic synthesis"],
            "type": "journal-article",
            "reference": [
                {
                    "DOI": "10.1000/foundation",
                    "article-title": (
                        "Foundational nickel photoredox cross-coupling"
                    ),
                }
            ],
        }
        self.discovery.fetch_crossref_works = lambda _dois: [
            {
                "DOI": "10.1000/foundation",
                "title": ["Foundational nickel photoredox cross-coupling"],
                "type": "journal-article",
                "issued": {"date-parts": [[2016]]},
                "is-referenced-by-count": 120,
            }
        ]
        try:
            report = self.discovery.crossref_reference_expansion(
                "nickel photoredox C(sp3)-C(sp2) cross-coupling",
                ["nickel photoredox coupling"],
                seed_hints=hints,
                seed_limit=2,
                result_limit=10,
            )
        finally:
            self.discovery.crossref_search_items = original_search
            self.discovery.fetch_crossref_work = original_fetch
            self.discovery.fetch_crossref_works = original_fetch_many

        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["seeds"][0]["doi"], "10.1000/review")
        self.assertTrue(
            any(
                row.get("reference_expansion_role") == "cited_candidate"
                and row.get("doi") == "10.1000/foundation"
                for row in report["results"]
            )
        )

    def test_unpaywall_requires_a_real_contact(self) -> None:
        self.assertFalse(
            self.discovery.valid_unpaywall_email("research@university.edu")
        )
        self.assertFalse(self.discovery.valid_unpaywall_email("not-an-email"))
        self.assertTrue(self.discovery.valid_unpaywall_email("lab@example.org"))

    def test_ingest_target_cannot_escape_managed_download_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            good = self.ingest.safe_target(
                root, "chem_papers/web-imports/paper.pdf"
            )
            self.assertEqual(good.suffix, ".pdf")
            with self.assertRaises(ValueError):
                self.ingest.safe_target(root, "../outside.pdf")
            with self.assertRaises(ValueError):
                self.ingest.safe_target(
                    root, "chem_papers/web-imports/not-a-pdf.txt"
                )

    def test_pdf_link_extraction_handles_metadata_and_relative_links(self) -> None:
        urls = self.ingest.extract_pdf_urls(
            "https://example.org/article/1",
            """
            <html><head>
              <meta name="citation_pdf_url" content="/files/paper.pdf">
            </head><body>
              <a href="/download/secondary">Download PDF</a>
            </body></html>
            """,
        )
        self.assertIn("https://example.org/files/paper.pdf", urls)
        self.assertIn("https://example.org/download/secondary", urls)

    def test_license_inventory_surfaces_hints_without_approving_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "paper.md"
            source.write_text(
                "This article is licensed under CC BY 4.0.\n"
                "https://creativecommons.org/licenses/by/4.0/\n",
                encoding="utf-8",
            )
            hints = self.figures.license_hints(source)
        self.assertEqual(hints["reuse_hint_class"], "open_reuse_candidate")
        self.assertIn("instructions", hints)
        self.assertNotIn("verified", json.dumps(hints).lower())
        self.assertNotIn("passed", json.dumps(hints).lower())

    def test_crop_spec_rejects_missing_or_inverted_geometry(self) -> None:
        self.assertIsNone(
            self.figures.crop_spec("paper.pdf", 3, [100, 100, 50, 150])
        )
        self.assertIsNone(self.figures.crop_spec("paper.pdf", "3", [0, 0, 5, 5]))
        self.assertEqual(
            self.figures.crop_spec("paper.pdf", 3, [0, 1, 100, 101]),
            {
                "source_pdf": "paper.pdf",
                "page_index": 3,
                "bbox": [0.0, 1.0, 100.0, 101.0],
            },
        )

    def test_visual_browser_keeps_source_order_without_keyword_ranking(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "browser.html"
            source_preview = Path(tmp) / "source-preview.png"
            source_preview.write_bytes(b"portable-preview")
            inventory = {
                "project_id": "generic-chemistry-review",
                "papers": [
                    {
                        "paper_id": "P001",
                        "title": "A materials characterization study",
                        "candidates": [
                            {
                                "inventory_candidate_id": "P001-V0001",
                                "source_label": "Figure 1",
                                "source_type": "image",
                                "source_page_hint": "page 2",
                                "source_caption_text": "First source visual.",
                                "source_image_path": str(source_preview),
                                "reuse_rights_hints": {"reuse_hint_class": "unknown"},
                            },
                            {
                                "source_label": "Figure 2",
                                "source_type": "chart",
                                "source_page_hint": "page 4",
                                "source_caption_text": "Second source visual.",
                                "reuse_rights_hints": {"reuse_hint_class": "unknown"},
                            },
                        ],
                    }
                ],
            }
            self.figures.render_browser_html(inventory, output)
            html = output.read_text(encoding="utf-8")
            preview_files = list((Path(tmp) / "browser_files").iterdir())
        self.assertLess(html.index("First source visual"), html.index("Second source visual"))
        self.assertIn("no keyword score or automatic recommendation", html)
        self.assertNotIn("inventory_score", html)
        self.assertEqual(len(preview_files), 1)
        self.assertIn("browser_files/P001-V0001-", html)

    def test_figure_inventory_recovers_promoted_web_papers_by_doi(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "review-projects" / "CVR-0001-topic"
            discovery = project / "00_discovery"
            discovery.mkdir(parents=True)
            (discovery / "selected_discovery_results.json").write_text(
                json.dumps(
                    {
                        "candidate_paper_ids": [],
                        "local_papers": [],
                        "web_papers": [
                            {
                                "doi": "https://doi.org/10.1000/example",
                                "title": "A repository paper promoted later",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            registry = root / "review-library" / "registry"
            registry.mkdir(parents=True)
            (registry / "papers.jsonl").write_text(
                json.dumps(
                    {
                        "paper_id": "P042",
                        "doi": "10.1000/example",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            self.assertEqual(
                self.figures.selected_paper_ids(root, project),
                ["P042"],
            )

    def test_generic_metadata_descriptors_do_not_require_allene_labels(self) -> None:
        prompt = self.metadata.classification_rules_prompt({})
        schema = self.metadata.structured_tags_schema({})
        rendered = json.dumps(schema).lower()
        self.assertNotIn("allene", prompt.lower())
        self.assertNotIn('"enum"', rendered)
        self.assertEqual(
            self.metadata.constrain_structured_tags(
                {
                    "product": "polyethylene terephthalate hydrolysate",
                    "substrate": "semi-crystalline PET",
                },
                {},
            )["substrate"],
            "semi-crystalline PET",
        )

    def test_metadata_paths_are_relative_inside_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            internal = root / "chem_papers" / "paper.pdf"
            internal.parent.mkdir(parents=True)
            internal.write_bytes(b"pdf")
            self.assertEqual(
                self.metadata.portable_path(root, internal),
                "chem_papers/paper.pdf",
            )

            external = root.parent / "external-paper.pdf"
            self.assertEqual(
                self.metadata.portable_path(root, external),
                str(external.resolve()),
            )


if __name__ == "__main__":
    unittest.main()
