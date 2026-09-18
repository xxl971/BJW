import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from main import (
    build_push_markdown,
    connect_history,
    find_next_url,
    infer_china_location,
    is_internship_text,
    job_from_mapping,
    load_config,
    parse_jobs,
    portal_report,
    render_html_report,
    score_text,
    send_daily_push,
    sync_push_pool,
)


class JobWatcherTests(unittest.TestCase):
    def test_score(self):
        score, matches = score_text("Senior Biostatistician, Oncology, Remote")
        self.assertGreaterEqual(score, 40)
        self.assertIn("biostatistics", matches)

    def test_parser_filters_non_job_links_and_deduplicates(self):
        page = """
        <a href="/job/boston/biostatistician/1/123"><span>Biostatistician</span> — Boston</a>
        <a href="/job/boston/biostatistician/1/123">Biostatistician — Boston</a>
        <a href="/about">About</a>
        <a href="/job/boston/accountant/1/999">Accountant</a>
        """
        jobs = parse_jobs(page, "Test Pharma", "https://careers.example.com/search", 20)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].company, "Test Pharma")
        self.assertEqual(jobs[0].url, "https://careers.example.com/job/boston/biostatistician/1/123")

    def test_china_location(self):
        self.assertEqual(infer_china_location("Biostatistician — Chengdu", "https://example.com/job/1"), "Chengdu, China")
        self.assertEqual(infer_china_location("RWE Lead", "https://example.com/job/beijing/rwe/1"), "Beijing, China")
        self.assertEqual(infer_china_location("Biostatistician — Boston", "https://example.com/job/2"), "")

    def test_next_page(self):
        page = '<a href="/search/jobs&p=2" rel="nofollow">Next</a>'
        self.assertEqual(find_next_url(page, "https://example.com/search/jobs"), "https://example.com/search/jobs?p=2")

    def test_internship_detection(self):
        self.assertTrue(is_internship_text("Biostatistics Intern – Summer 2027"))
        self.assertTrue(is_internship_text("临床数据实习生"))
        self.assertFalse(is_internship_text("Senior Biostatistician"))

    def test_company_pool_has_30_and_five_cros(self):
        sources = load_config(Path(__file__).with_name("companies.json"))
        self.assertEqual(len(sources), 30)
        self.assertEqual(sum(item.get("category") == "CRO" for item in sources), 5)

    def test_portal_source_is_explicit(self):
        result = portal_report({"company":"Test", "category":"国内药企", "url":"https://example.com"})
        self.assertEqual(result["status"], "portal")
        self.assertEqual(result["pages_fetched"], 0)

    def test_china_internship_section_is_populated(self):
        payload = json.loads(Path(__file__).with_name("internships.json").read_text(encoding="utf-8"))
        china = [item for item in payload["current"] if item.get("region") == "中国"]
        self.assertGreaterEqual(len(china), 10)
        self.assertTrue(any(item.get("deadline_type") == "official_explicit" for item in china))

    def test_push_pool_only_marks_first_sighting_new(self):
        jobs = parse_jobs(
            '<a href="/job/chengdu/biostatistician/1/123">Biostatistician — Chengdu</a>',
            "Test Pharma",
            "https://careers.example.com/jobs",
            20,
        )
        with tempfile.TemporaryDirectory() as tmp:
            connection = connect_history(Path(tmp) / "history.sqlite3")
            first_jobs, first_internships = sync_push_pool(connection, jobs, {"current": []})
            second_jobs, second_internships = sync_push_pool(connection, jobs, {"current": []})
            self.assertEqual(first_jobs, {jobs[0].url})
            self.assertFalse(first_internships)
            self.assertFalse(second_jobs)
            self.assertFalse(second_internships)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM push_queue").fetchone()[0], 1
            )

    def test_daily_push_sends_when_queue_is_empty_and_links_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            connection = connect_history(Path(tmp) / "history.sqlite3")
            with patch("main.send_wxpusher_spt") as send:
                sent = send_daily_push(
                    connection,
                    "SPT_testtoken",
                    "https://example.github.io/job-report/",
                )
            self.assertEqual(sent, 0)
            send.assert_called_once()
            summary, content = send.call_args.args[1:]
            self.assertIn("今日新增 0 个", summary)
            self.assertIn("今天没有发现新岗位", content)
            self.assertIn("https://example.github.io/job-report/", content)

    def test_push_markdown_includes_full_report_link(self):
        content = build_push_markdown([], report_url="https://example.com/report/")
        self.assertIn("完整报告", content)
        self.assertIn("[点击查看今日完整岗位报告](https://example.com/report/)", content)

    def test_csv_mapping_restores_numeric_score(self):
        job = job_from_mapping(
            {
                "job_id": "1",
                "company": "Test",
                "title": "Biostatistician — Chengdu",
                "location": "Chengdu, China",
                "posted_date": "",
                "url": "https://example.com/job/1",
                "source_url": "https://example.com/jobs",
                "score": "42",
                "matched_rules": "biostatistics",
                "collected_at_utc": "2026-09-17T00:00:00+00:00",
            }
        )
        self.assertEqual(job.score, 42)
        self.assertIsInstance(job.score, int)

    def test_report_shows_update_count_and_red_new_row(self):
        jobs = parse_jobs(
            '<a href="/job/chengdu/biostatistician/1/123">Biostatistician — Chengdu</a>',
            "Test Pharma",
            "https://careers.example.com/jobs",
            20,
        )
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "report.html"
            render_html_report(
                jobs,
                [{"company":"Test Pharma", "category":"国内药企", "source_url":"https://example.com", "status":"ok", "jobs_found":1}],
                {"current": [], "historical": []},
                report,
                new_job_urls={jobs[0].url},
            )
            content = report.read_text(encoding="utf-8")
            self.assertIn("今天更新了 <strong>1</strong> 个岗位机会", content)
            self.assertIn('class="new-row"', content)
            self.assertIn("NEW", content)

    def test_github_workflow_uses_beijing_schedule_and_secret(self):
        workflow = (
            Path(__file__).with_name(".github")
            / "workflows"
            / "daily-job-push.yml"
        ).read_text(encoding="utf-8")
        self.assertIn('cron: "7 17 * * *"', workflow)
        self.assertIn('timezone: "Asia/Shanghai"', workflow)
        self.assertIn("secrets.WXPUSHER_SPT", workflow)
        self.assertIn("actions/deploy-pages@v4", workflow)
        self.assertIn("python main.py --send-daily", workflow)
        self.assertNotRegex(workflow, r"SPT_[A-Za-z0-9]{10,}")


if __name__ == "__main__":
    unittest.main()
