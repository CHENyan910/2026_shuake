"""模拟考试自动入口的恢复、保存失败与成绩隔离检查。"""

import io
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import mock_exam as exam
import start


def task_state(dialogs=(), owner=False, confirmation=False):
    return {'path':f'/lab-study-front/examTask/{exam.TASK_ID}',
            'paper_ready':False,'mock_owner':owner,'dialogs':list(dialogs),
            'mock_confirmation':confirmation,'home_button':True,'messages':[]}


def paper_state(ready=True):
    return dict(task_state(),path=f'/lab-study-front/routine-exam/{exam.TASK_ID}',paper_ready=ready)


class LaunchTests(unittest.TestCase):
    def test_queue_then_paper_starts_each_step_once(self):
        states = [task_state(),task_state(['模拟考试'],True,True),
                  task_state(['模拟考试'],True,True),task_state(['需要排队等待'],True),
                  task_state(['需要排队等待'],True),task_state(['您已完成排队'],True),
                  task_state(['试卷生成中'],True),task_state(['试卷生成中'],True),
                  paper_state(False),paper_state()]
        page = SimpleNamespace(url=exam.STUDY_URL,run_js=lambda *args:states.pop(0))
        with patch.object(exam,'start_mock') as start_mock, patch.object(exam,'begin_mock') as begin, \
             patch.object(exam,'enter_mock') as enter, patch.object(exam,'verify_mock') as verify, \
             patch.object(exam.time,'sleep'), patch('sys.stdout',new=io.StringIO()):
            exam.launch_mock(page,None)
        start_mock.assert_called_once_with(page)
        begin.assert_called_once_with(page)
        enter.assert_called_once_with(page)
        verify.assert_called_once_with(page)

    def test_existing_paper_resumes_without_creating_an_attempt(self):
        page = SimpleNamespace(url='https://sysaq.ustc.edu.cn/lab-study-front/person',
                               run_js=lambda *args:paper_state())
        visited = []
        page.get = visited.append
        active = {'is_mock':1,'exam_id':int(exam.TASK_ID),'request_id':'existing-request','next_step':5}
        with patch.object(exam,'start_mock') as start_mock, patch.object(exam,'verify_mock'), \
             patch('sys.stdout',new=io.StringIO()):
            exam.launch_mock(page,active)
        start_mock.assert_not_called()
        self.assertEqual(len(visited),1)
        self.assertIn('examRequestId=existing-request',visited[0])

    def test_formal_attempt_prevents_navigation_and_answering(self):
        active = {'is_mock':0,'exam_id':int(exam.TASK_ID)}
        with patch.object(exam,'require_complete_bank'), patch.object(exam,'current_exam',return_value=active), \
             patch.object(exam,'launch_mock') as launch, patch.object(exam,'fill_paper') as fill:
            with self.assertRaisesRegex(RuntimeError,'正式考试'):
                exam.run_mock(object())
        launch.assert_not_called()
        fill.assert_not_called()

    def test_unknown_modal_is_not_automatically_confirmed(self):
        page = SimpleNamespace(url=exam.STUDY_URL,
                               run_js=lambda *args:task_state(['其他考试确认'],False))
        with patch.object(exam,'start_mock') as start_mock:
            with self.assertRaisesRegex(RuntimeError,'未确认'):
                exam.launch_mock(page,None)
        start_mock.assert_not_called()

    def test_failed_answer_save_prevents_submission(self):
        with patch.object(exam,'require_complete_bank'), patch.object(exam,'current_exam',return_value=None), \
             patch.object(exam,'launch_mock'), patch.object(exam,'fill_paper',side_effect=RuntimeError('保存失败')), \
             patch.object(exam,'submit_when_ready') as submit:
            with self.assertRaisesRegex(RuntimeError,'保存失败'):
                exam.run_mock(object())
        submit.assert_not_called()


class ResultAndMenuTests(unittest.TestCase):
    def test_old_high_score_and_formal_results_cannot_replace_new_mock(self):
        rows = [{'examId':int(exam.TASK_ID),'isMockExam':1,'sessionId':10,'endDate':100,'score':'100'},
                {'examId':int(exam.TASK_ID),'isMockExam':0,'sessionId':12,'endDate':300,'score':'100'},
                {'examId':int(exam.TASK_ID),'isMockExam':1,'sessionId':11,'endDate':200,'score':None}]
        self.assertEqual(exam.result_record(rows,exclude_session=10)['sessionId'],11)
        self.assertIsNone(exam.result_record(rows[:2],exclude_session=10))

    def test_menu_runs_mock_and_reads_results_as_separate_actions(self):
        with patch('builtins.input',side_effect=['10','11','12','0']), \
             patch.object(start.subprocess,'run',return_value=SimpleNamespace(returncode=0)) as run, \
             patch('sys.stdout',new=io.StringIO()):
            start.main()
        self.assertEqual([call.args[0][1:] for call in run.call_args_list],
                         [['mock_exam.py','run'],['mock_exam.py','recent'],['formal_exam.py','run','--confirm-promise']])


if __name__ == '__main__':
    unittest.main()
