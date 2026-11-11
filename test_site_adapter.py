"""离线验证新版页面解析、分类目标和网站保存响应。"""

from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from site_adapter import Course, Requirements, Site, candidates, seconds, STUDY_URL, TASK_ID
from project_config import parse_study_url


class TaskConfigTests(unittest.TestCase):
    def test_new_account_task_is_not_fixed_to_old_id(self):
        self.assertEqual(parse_study_url('https://sysaq.ustc.edu.cn/lab-study-front/examTask/36'),
                         ('https://sysaq.ustc.edu.cn/lab-study-front/examTask/36', '36'))

    def test_course_detail_is_normalized_to_task_home(self):
        self.assertEqual(parse_study_url('https://sysaq.ustc.edu.cn/lab-study-front/examTask/36/5/1/3?nocache=1'),
                         ('https://sysaq.ustc.edu.cn/lab-study-front/examTask/36', '36'))

    def test_login_and_unrelated_urls_are_rejected(self):
        for url in ['https://sysaq.ustc.edu.cn/login', 'about:blank',
                    'https://example.com/lab-study-front/examTask/36',
                    'https://sysaq.ustc.edu.cn/lab-study-front/examTask/not-a-task']:
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    parse_study_url(url)


class DurationTests(unittest.TestCase):
    def test_clock_formats(self):
        for text, value in [("01:28:00", 5280), ("04:32:00", 16320), ("15:00", 900), ("65:01", 3901)]:
            with self.subTest(text=text):
                self.assertEqual(seconds(text), value)

    def test_missing_and_invalid_are_not_zero(self):
        for text in [None, "", "loading", "01:60", "01:60:00"]:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    seconds(text)


class RequirementTests(unittest.TestCase):
    def test_read_categories_even_when_cards_are_reordered(self):
        requirements = Requirements.from_cards([
            {"label": "选学时长", "done": "00:00:00", "required": "/ 01:28:00"},
            {"label": "必学时长", "done": "01:40:01", "required": "/ 04:32:00"},
        ])
        self.assertEqual(requirements, Requirements(6001, 16320, 0, 5280))

    def test_one_category_cannot_substitute_for_the_other(self):
        self.assertFalse(Requirements(600, 1200, 1800, 600).complete)
        self.assertTrue(Requirements(1200, 1200, 600, 600).complete)

    def test_missing_requirement_stops(self):
        with self.assertRaises(RuntimeError):
            Requirements.from_cards([])


class CourseTests(unittest.TestCase):
    def setUp(self):
        self.must = Course("课程 A", "安全知识", True, 1, 900, 1, 0, "去学习", False)

    def test_parse_table_progress(self):
        row = {"title": "课程", "module": "安全视频", "must": False,
               "time_text": "已学习：00:00:21 / 00:02:00", "index": 2, "action": "去学习", "disabled": False}
        course = Course.from_row(row, 3)
        self.assertEqual((course.done, course.required, course.remaining, course.page_number), (21, 120, 99, 3))

    def test_candidates_respect_category_and_completed_courses(self):
        elective = replace(self.must, title="课程 B", must=False)
        complete = replace(self.must, title="课程 C", done=900)
        disabled = replace(self.must, title="课程 D", disabled=True)
        needs_must_only = Requirements(0, 900, 600, 600)
        self.assertEqual(candidates([elective, complete, disabled, self.must], needs_must_only), [self.must])
        self.assertEqual(candidates([self.must], needs_must_only, [self.must.key]), [])

    def test_scan_checks_total_across_pages(self):
        site = Site(MagicMock())
        cards = [{"label": "必学时长", "done": "00:00:00", "required": "/ 00:30:00"},
                 {"label": "选学时长", "done": "00:00:00", "required": "/ 00:00:00"}]
        def state(number, title, has_next, total=2):
            return {"cards": cards, "page_number": number, "has_next": has_next, "total_text": f"共 {total} 条",
                    "rows": [{"title": title, "module": "安全知识", "must": True, "time_text": "00:00:00 / 00:15:00",
                              "index": 0, "action": "去学习", "disabled": False}]}
        site.next_page = MagicMock(return_value=state(2, "课程 B", False))
        _, courses = site.collect_courses(state(1, "课程 A", True))
        self.assertEqual([c.title for c in courses], ["课程 A", "课程 B"])
        site.next_page.return_value = state(2, "课程 B", False, total=3)
        with self.assertRaises(RuntimeError):
            site.collect_courses(state(1, "课程 A", True))


class UploadTests(unittest.TestCase):
    def packet(self, data, status=200, code=200):
        return SimpleNamespace(url="https://sysaq.ustc.edu.cn/cg-exam-server/api/study_upload/test",
                               response=SimpleNamespace(status=status, body={"code": code, "data": data}))

    def test_saved_requires_server_confirmation(self):
        page = MagicMock()
        page.listen.wait.side_effect = [self.packet(True), False]
        site = Site(page)
        self.assertEqual(site.packets(), ["saved"])
        self.assertTrue(site.saved_during_course)

    def test_rejected_or_failed_upload_is_not_success(self):
        for packet in [self.packet(False), self.packet(True, status=500), self.packet(True, code=400)]:
            with self.subTest(packet=packet):
                page = MagicMock()
                page.listen.wait.return_value = packet
                with self.assertRaises(RuntimeError):
                    Site(page).packets()


class ReturnTests(unittest.TestCase):
    def setUp(self):
        self.page = MagicMock()
        self.page.url = STUDY_URL
        self.page.run_js.return_value = True
        self.site = Site(self.page)
        self.site.in_course = True
        self.site.wait_for = MagicMock(return_value=True)
        self.site.wait_task = MagicMock(return_value={})

    def test_return_uses_explicit_task_route_and_checks_save_before_table(self):
        events = []
        self.site.packets = MagicMock(side_effect=lambda: events.append('saved') or ['saved'])
        self.site.wait_task.side_effect = lambda: events.append('table') or {}
        self.assertTrue(self.site.leave_course())
        self.assertEqual(events, ['saved', 'table'])
        script, task = self.page.run_js.call_args.args
        self.assertIn("name: 'examTask'", script)
        self.assertIn('$router.push', script)
        self.assertNotIn('$router.back', script)
        self.assertEqual(task, TASK_ID)
        self.page.listen.stop.assert_called_once()

    def test_saved_course_can_reload_stuck_table_once(self):
        self.site.packets = MagicMock(return_value=['saved'])
        self.site.wait_task.side_effect = [RuntimeError('课程表格加载超时：加载遮罩尚未消失。'), {}]
        self.assertTrue(self.site.leave_course())
        self.page.get.assert_called_once_with(STUDY_URL)
        self.assertEqual(self.site.wait_task.call_count, 2)

    def test_no_reload_without_confirmed_save(self):
        self.site.packets = MagicMock(return_value=[])
        with patch('site_adapter.time.monotonic', side_effect=[0, 11]):
            self.assertFalse(self.site.leave_course())
        self.page.get.assert_not_called()
        self.site.wait_task.assert_not_called()
        self.page.listen.stop.assert_called_once()

    def test_exhausted_list_retry_preserves_save_status_in_error(self):
        self.site.packets = MagicMock(return_value=['saved'])
        self.site.wait_task.side_effect = RuntimeError('课程表格加载超时：加载遮罩尚未消失。')
        with self.assertRaisesRegex(RuntimeError, '学习进度已保存，但返回列表失败'):
            self.site.leave_course()
        self.page.get.assert_called_once_with(STUDY_URL)

    def test_timeout_names_actual_page_instead_of_guessing_network(self):
        site = Site(self.page)
        site.snapshot = MagicMock(return_value={'path': '/lab-study-front/examTask/22/5/1/3'})
        def expired(callback, message):
            self.assertFalse(callback())
            raise RuntimeError(message)
        site.wait_for = expired
        with self.assertRaisesRegex(RuntimeError, '尚未回到任务首页.*22/5/1/3'):
            site.wait_task()


if __name__ == "__main__":
    unittest.main()
