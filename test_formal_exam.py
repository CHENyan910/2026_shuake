"""检查承诺授权、正式考试阶段与模式，以及成绩隔离。"""

import unittest
import io
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

import formal_exam as formal
import mock_exam as shared


class FormalGuardTests(unittest.TestCase):
    def test_pending_personal_promise_cannot_be_accepted_by_runner(self):
        page = SimpleNamespace(url=shared.STUDY_URL)
        active = {'is_mock':0,'exam_id':int(shared.TASK_ID),'next_step':1}
        with patch.object(formal,'current_exam',return_value=active), \
             patch.object(formal,'verify_paper') as verify:
            with self.assertRaisesRegex(RuntimeError,'本人'):
                formal.require_formal_paper(page)
        verify.assert_not_called()

    def test_mock_attempt_is_not_a_formal_attempt(self):
        page = SimpleNamespace(url=f'https://sysaq.ustc.edu.cn/lab-study-front/routine-exam/{shared.TASK_ID}')
        with patch.object(formal,'current_exam',return_value={'is_mock':1,'exam_id':int(shared.TASK_ID),'next_step':5}):
            with self.assertRaises(RuntimeError):
                formal.require_formal_paper(page)

    def test_ready_formal_paper_requires_server_confirmation(self):
        page = SimpleNamespace(url=f'https://sysaq.ustc.edu.cn/lab-study-front/routine-exam/{shared.TASK_ID}')
        active = {'is_mock':0,'exam_id':int(shared.TASK_ID),'next_step':5}
        with patch.object(formal,'current_exam',return_value=active), patch.object(formal,'verify_paper') as verify:
            self.assertEqual(formal.require_formal_paper(page),active)
        verify.assert_called_once_with(page,is_mock=False)

    def test_missing_personal_step_prevents_answering(self):
        with patch.object(formal,'require_complete_bank'), \
             patch.object(formal,'require_formal_paper',side_effect=RuntimeError('本人尚未确认')), \
             patch.object(formal,'fill_paper') as fill, patch.object(formal,'submit_when_ready') as submit, \
             patch.object(formal,'launch_formal') as launch:
            with self.assertRaises(RuntimeError):
                formal.run(object())
        fill.assert_not_called()
        submit.assert_not_called()
        launch.assert_not_called()

    def test_failed_formal_save_prevents_submission(self):
        with patch.object(formal,'require_complete_bank'), patch.object(formal,'require_formal_paper'), \
             patch.object(formal,'fill_paper',side_effect=RuntimeError('保存失败')), \
             patch.object(formal,'submit_when_ready') as submit:
            with self.assertRaises(RuntimeError):
                formal.run(object())
        submit.assert_not_called()

    def test_formal_result_does_not_reuse_mock_score(self):
        rows = [{'examId':int(shared.TASK_ID),'isMockExam':1,'sessionId':100,'score':'100','endDate':20},
                {'examId':int(shared.TASK_ID),'isMockExam':0,'sessionId':101,'score':None,'endDate':10}]
        self.assertEqual(shared.result_record(rows,is_mock=False)['sessionId'],101)

    def test_diagnosis_only_reads_and_matches_existing_paper(self):
        state = {'questions':[{'id':20983,'answer':None}],'remaining':7000}
        page = SimpleNamespace(run_js=Mock(return_value=state))
        plans = [{'subject_id':20983,'resolution':{'selected_bank_id':15517}}]
        with TemporaryDirectory() as directory,patch.object(formal,'OUTPUT',Path(directory)), \
             patch.object(formal,'require_complete_bank'),patch.object(formal,'require_formal_paper'), \
             patch.object(formal,'prepare_answer_plan',return_value=plans) as prepare, \
             patch.object(formal,'fill_paper') as fill,patch.object(formal,'submit_when_ready') as submit, \
             patch('sys.stdout',new=io.StringIO()):
            formal.diagnose(page)
            self.assertTrue((Path(directory)/'formal_diagnosis.json').is_file())
        page.run_js.assert_called_once_with(shared.PAPER_STATE)
        prepare.assert_called_once_with(state,is_mock=False)
        fill.assert_not_called()
        submit.assert_not_called()

    def test_existing_formal_paper_resumes_without_starting_another_attempt(self):
        state = {'path':f'/lab-study-front/routine-exam/{shared.TASK_ID}','paper_ready':True}
        page = SimpleNamespace(url='https://sysaq.ustc.edu.cn'+state['path'],run_js=lambda *args:state)
        active = {'is_mock':0,'exam_id':int(shared.TASK_ID),'next_step':5}
        with patch.object(formal,'current_exam',return_value=active), \
             patch.object(formal,'require_formal_paper') as ready, \
             patch.object(formal,'start_formal') as start, \
             patch.object(formal,'confirm_promise') as promise, \
             patch('sys.stdout',new=io.StringIO()):
            formal.launch_formal(page)
        ready.assert_called_once_with(page)
        start.assert_not_called()
        promise.assert_not_called()


class AuthorizedPromiseTests(unittest.TestCase):
    def test_known_promise_with_personal_details_is_accepted(self):
        formal.validate_promise('<p>'+formal.AUTHORIZED_PROMISE+'</p><p>所在单位：某学院</p><p>学号(工号)：123</p>',1)
        formal.validate_promise(formal.AUTHORIZED_PROMISE,'1')

    def test_changed_liability_terms_are_not_silently_accepted(self):
        with self.assertRaisesRegex(RuntimeError,'不一致'):
            formal.validate_promise(formal.AUTHORIZED_PROMISE+'另外承担新的义务。',1)

    def test_handwritten_signature_is_not_generated(self):
        with self.assertRaisesRegex(RuntimeError,'签名'):
            formal.validate_promise(formal.AUTHORIZED_PROMISE,2)

    def test_authorized_launch_confirms_once_then_queues(self):
        def state(stage):
            return {'path':f'/lab-study-front/examTask/{shared.TASK_ID}' if stage!='paper' else
                    f'/lab-study-front/routine-exam/{shared.TASK_ID}',
                    'paper_ready':stage=='paper','formal_owner':stage!='home',
                    'dialogs':{'confirm':['考试准入 开始考试'],'queue':['排队等待'],
                               'ready':['您已完成排队'],'generating':['试卷生成中']}.get(stage,[]),
                    'detail':stage=='confirm','messages':[],
                    'promise':{'type':1,'content':formal.AUTHORIZED_PROMISE} if stage=='promise' else None}
        states = [state(s) for s in ['home','home','home','confirm','confirm','promise','promise',
                                     'queue','queue','ready','generating','generating','paper']]
        page = SimpleNamespace(url=shared.STUDY_URL,run_js=lambda *args:states.pop(0))
        actions = []
        with patch.object(formal,'current_exam',return_value=None), \
             patch.object(formal,'start_formal',side_effect=lambda p:actions.append('start')), \
             patch.object(formal,'click_formal_modal',side_effect=lambda p,k:actions.append(k)), \
             patch.object(formal,'confirm_promise',side_effect=lambda p,v:actions.append('promise')), \
             patch.object(formal,'require_formal_paper') as ready, \
             patch.object(formal.time,'sleep'),patch('sys.stdout',new=io.StringIO()):
            formal.launch_formal(page)
        self.assertEqual(actions,['start','detail','promise','queue'])
        ready.assert_called_once_with(page)


if __name__ == '__main__':
    unittest.main()
