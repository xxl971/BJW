import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from main import (
    build_push_markdown,
    collect_one,
    company_type,
    connect_history,
    find_next_url,
    infer_china_location,
    is_chengdu_job,
    is_china_job,
    is_internship_text,
    job_from_mapping,
    load_config,
    needs_city_review,
    parse_jobs,
    platform_search_table,
    job_source,
    portal_report,
    pending_report,
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

    def test_explicit_city_overrides_job_title_and_search_url(self):
        page = '<a href="/job/123" data-location="成都">Statistical Scientist</a>'
        jobs = parse_jobs(page, "Roche", "https://example.com/china/jobs", 20)
        self.assertEqual(jobs[0].location, "Chengdu, China")
        self.assertTrue(is_china_job(jobs[0]))
        self.assertTrue(is_chengdu_job(jobs[0]))
        foreign = parse_jobs('<a href="/job/456" data-location="Boston, USA">Statistical Scientist — 上海</a>',
                             "Roche", "https://example.com/china/jobs", 20)
        self.assertFalse(is_china_job(foreign[0]))

    def test_jobposting_structured_location_and_date(self):
        page = '''<script type="application/ld+json">
        {"@context":"https://schema.org","@type":"JobPosting","title":"生物统计师",
         "url":"https://careers.example.com/jobs/42","datePosted":"2026-09-20",
         "jobLocation":{"@type":"Place","address":{"@type":"PostalAddress",
         "addressLocality":"苏州","addressCountry":"CN"}}}
        </script>'''
        job, = parse_jobs(page, "Test", "https://careers.example.com/china", 20)
        self.assertEqual(job.location, "Suzhou, China")
        self.assertEqual(job.posted_date, "2026-09-20")
        self.assertTrue(is_china_job(job))

    def test_sibling_location_in_job_card(self):
        page = '''<ul><li><a href="/job/42">Statistical Scientist</a>
          <span class="job-location"><span>上海</span></span></li>
          <li><a href="/job/43">Biostatistician</a>
          <div class="location">Boston, USA</div></li></ul>'''
        jobs = parse_jobs(page, "Test", "https://example.com/search", 20)
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0].location, "Shanghai, China")
        self.assertTrue(is_china_job(jobs[0]))
        self.assertFalse(is_china_job(jobs[1]))

    def test_unknown_chinese_city_is_reviewed_instead_of_marked_china(self):
        job, = parse_jobs('<a href="/job/77" data-location="鄂州">生物统计师</a>',
                          "Test", "https://example.com/jobs", 20)
        self.assertFalse(is_china_job(job))
        self.assertTrue(needs_city_review(job))

    def test_platform_search_links_are_separate_from_auto_results(self):
        table = platform_search_table()
        self.assertIn("猎聘", table)
        self.assertIn("智联招聘", table)
        self.assertIn("LinkedIn", table)
        self.assertIn("实习僧", table)
        self.assertIn("松鼠实习", table)
        self.assertIn("site%3Aagechild.com%2F", table)
        self.assertIn("Statistical Scientist", table)
        self.assertIn("site%3Aliepin.com%2Fjob%2F", table)

    def test_next_page(self):
        page = '<a href="/search/jobs&p=2" rel="nofollow">Next</a>'
        self.assertEqual(find_next_url(page, "https://example.com/search/jobs"), "https://example.com/search/jobs?p=2")

    def test_internship_detection(self):
        self.assertTrue(is_internship_text("Biostatistics Intern – Summer 2027"))
        self.assertTrue(is_internship_text("临床数据实习生"))
        self.assertFalse(is_internship_text("Senior Biostatistician"))

    def test_company_pool_preserves_originals_and_adds_ranked_companies(self):
        sources = load_config(Path(__file__).with_name("companies.json"))
        self.assertEqual(len(sources), 94)
        self.assertEqual(sum(item.get("category") == "跨国CRO" for item in sources), 5)
        self.assertEqual(sum(item.get("category") == "跨国药企" for item in sources), 14)
        self.assertEqual(sum(item.get("category") == "国内药企" for item in sources), 55)
        self.assertEqual(sum(item.get("category") == "国内CRO" for item in sources), 20)
        self.assertEqual(len({item["company"] for item in sources}), len(sources))
        self.assertEqual(sorted(item["rank"] for item in sources if item.get("category") == "国内CRO"), list(range(1, 21)))
        self.assertEqual(sum(item.get("rank_source") == "2025医药工业营收百强" for item in sources), 50)
        self.assertEqual(sum(item.get("monitor_mode") == "pending" for item in sources), 56)

    def test_official_social_and_campus_channels_are_both_retained(self):
        sources = load_config(Path(__file__).with_name("companies.json"))
        for company, kinds in (("科伦药业 Kelun", ["社会招聘", "校园招聘"]),
                               ("康弘药业 Kanghong", ["社招", "校招"])):
            source = next(item for item in sources if item["company"] == company)
            report = portal_report(source)
            self.assertEqual([channel["kind"] for channel in report["channels"]], kinds)
            self.assertEqual(len({channel["url"] for channel in report["channels"]}), 2)

    def test_company_type_and_job_provenance(self):
        self.assertEqual(company_type("Eli Lilly China / 礼来中国", {"Eli Lilly": "跨国药企"}), "跨国药企")
        self.assertEqual(company_type("德达医药 Deltamed", {}), "国内CRO")
        self.assertEqual(company_type("未知雇主", {}), "其他")
        linked = job_from_mapping({"company": "Roche / Genentech", "title": "Statistical Scientist",
                                   "url": "https://cn.linkedin.com/jobs/view/123", "location": "北京"})
        official = job_from_mapping({**linked.__dict__, "url": "https://careers.roche.com/job/123"})
        self.assertEqual(job_source(linked), "LinkedIn")
        self.assertEqual(job_source(official), "公司官网")
        with tempfile.TemporaryDirectory() as tmp:
            connection = connect_history(Path(tmp) / "history.sqlite3")
            new_urls, _ = sync_push_pool(connection, [linked, official], {"current": []})
            self.assertEqual(new_urls, {linked.url, official.url})

    def test_portal_source_is_explicit(self):
        result = portal_report({"company":"Test", "category":"国内药企", "url":"https://example.com"})
        self.assertEqual(result["status"], "portal")
        self.assertEqual(result["pages_fetched"], 0)

    def test_pending_source_has_no_misleading_official_link(self):
        result = pending_report({"company": "待核实", "category": "国内CRO", "rank": 1})
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["source_url"], "")
        self.assertEqual(result["channels"], [])

    def test_second_batch_keeps_search_engine_separate_from_company_jobs(self):
        by_name = {x["company"]: x for x in load_config(Path(__file__).with_name("companies.json"))}
        xiuzheng = by_name["修正药业"]
        self.assertEqual(xiuzheng["monitor_mode"], "pending")
        self.assertEqual(xiuzheng["url"], "")
        self.assertEqual(pending_report(xiuzheng)["channels"][0]["kind"], "Bing 人工查找招聘官网（非官网）")
        for company in ("中国生物制药 / 正大天晴", "上海医药", "三生制药"):
            self.assertEqual(by_name[company]["monitor_mode"], "portal")
            self.assertEqual(len(by_name[company]["channels"]), 3)
        self.assertEqual(by_name["恒瑞医药 Hengrui"]["channels"][2]["kind"], "松鼠实习（第三方，需核实）")
        self.assertEqual(job_source(job_from_mapping({"company": "恒瑞医药", "title": "统计实习", "url": "https://agechild.com/company/hengrui", "location": "上海"})), "松鼠实习")

    def test_user_submitted_multi_channel_links_are_preserved(self):
        sources = load_config(Path(__file__).with_name("companies.json"))
        by_name = {item["company"]: item for item in sources}
        self.assertEqual(by_name["Fortrea"]["url"], "https://careers.fortrea.com/en/jobs/")
        self.assertEqual([c["kind"] for c in by_name["复星医药"]["channels"]], ["社会招聘", "校园招聘", "实习生招聘"])
        self.assertEqual([c["kind"] for c in by_name["齐鲁制药"]["channels"]], ["实习生招聘", "校园招聘", "海外人才招聘"])
        self.assertEqual(by_name["国药集团"]["monitor_mode"], "portal")
        self.assertEqual(by_name["AstraZeneca"]["monitor_mode"], "html")
        self.assertIn("https://job-search.astrazeneca.cn/%E6%B1%82%E8%81%8C", by_name["AstraZeneca"]["target_urls"])

    def test_html_source_report_exposes_additional_recruiting_channels(self):
        source = next(x for x in load_config(Path(__file__).with_name("companies.json")) if x["company"] == "ICON")
        with patch("main.fetch_html", return_value=("<html></html>", {"http_status": 200, "bytes": 13, "elapsed_seconds": 0.1})):
            _, report = collect_one(source, timeout=1)
        self.assertEqual([c["kind"] for c in report["channels"]], ["全部职位", "生物统计自动采集入口"])

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
            self.assertIn("公司性质", content)
            self.assertIn("国内药企", content)
            self.assertIn("区域二：五个招聘平台", content)

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
