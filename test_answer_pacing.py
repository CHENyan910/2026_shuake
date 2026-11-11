"""用虚拟时钟核对实际答题调用时刻，测试不会真实等待15秒。"""

import io
import json
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase,main
from unittest.mock import patch

import mock_exam as exam
from exam_bank import AnswerBank


class Clock:
    def __init__(self):
        self.now = 0
        self.waits = []

    def sleep(self,seconds):
        self.waits.append(seconds)
        self.now += seconds


class Page:
    def __init__(self,clock,resume=False,remaining=7200):
        self.clock,self.remaining = clock,remaining
        self.questions = [{'id':i,'subjectType':2,'subjectContent':f'问题{i}',
                           'subjectItems':[{'id':1,'content':'甲'},{'id':2,'content':'乙'}],
                           'answer':{'answer':'1'} if resume and i==1 else None} for i in (1,2,3)]
        self.starts,self.saved,self.batch_sizes = [],[],[]
        self.listen = SimpleNamespace(start=lambda *a,**k:None,wait=self.response)

    def run_js(self,script,*args):
        if script == exam.PAPER_STATE:
            return {'questions':deepcopy(self.questions),'requestId':'one-attempt',
                    'remaining':self.remaining,'isSubmit':False,'unanswered':0,
                    'config':{'minSubmitTime':20,'subjectTimeLength':120}}
        if 'const plans=JSON.parse(arguments[0])' in script:
            plans = json.loads(args[0])
            self.batch_sizes.append(len(plans))
            for plan in plans:
                self.starts.append(self.clock.now)
                self.clock.now += 1  # 一秒后收到服务器保存成功反馈。
                next(q for q in self.questions if q['id']==plan['subject_id'])['answer'] = {'answer':exam.expected_answer(plan)}
                self.saved.append(self.clock.now)
            return
        if 'vm.getData()' in script:
            return
        raise AssertionError('未知浏览器调用')

    def response(self,**kwargs):
        body = {'code':200,'data':{'data':{'paper':{'subjects':deepcopy(self.questions)}}}}
        return SimpleNamespace(response=SimpleNamespace(body=body))


class PacingTests(TestCase):
    def run_fill(self,page,is_mock):
        records = [{'question':q,'feedback':{'rightAnswer':'1'}} for q in page.questions]
        with TemporaryDirectory() as directory,patch.object(exam,'OUTPUT',Path(directory)), \
             patch.object(exam,'require_complete_bank'),patch.object(exam,'verify_paper'), \
             patch.object(exam,'verify_mock'),patch.object(exam.AnswerBank,'load',return_value=AnswerBank(records)), \
             patch.object(exam.time,'monotonic',side_effect=lambda:page.clock.now), \
             patch.object(exam.time,'sleep',side_effect=page.clock.sleep), \
             patch('sys.stdout',new=io.StringIO()):
            exam.fill_paper(page,is_mock=is_mock)

    def test_formal_next_question_waits_fifteen_seconds_after_saved(self):
        page = Page(Clock())
        self.run_fill(page,False)
        self.assertEqual(page.batch_sizes,[1,1,1])
        self.assertEqual([page.starts[i+1]-page.saved[i] for i in range(2)],[15,15])
        self.assertEqual(page.clock.waits,[15,15])
        self.assertEqual(page.clock.now,page.saved[-1])  # 最后一题之后不额外等待。

    def test_resume_skips_saved_answer_and_keeps_initial_gap(self):
        page = Page(Clock(),resume=True)
        self.run_fill(page,False)
        self.assertEqual(len(page.starts),2)
        self.assertEqual(page.starts[0],15)
        self.assertEqual(page.starts[1]-page.saved[0],15)

    def test_mock_keeps_original_batching_without_formal_delay(self):
        page = Page(Clock())
        self.run_fill(page,True)
        self.assertEqual(page.batch_sizes,[3])
        self.assertEqual(page.clock.waits,[])

    def test_not_enough_time_does_not_start_answering(self):
        page = Page(Clock(),remaining=30)
        with self.assertRaisesRegex(RuntimeError,'剩余考试时间不足'):
            self.run_fill(page,False)
        self.assertEqual(page.starts,[])

    def test_all_conflicts_are_saved_before_any_answer_is_written(self):
        page = Page(Clock())
        records = [{'question':q,'feedback':{'rightAnswer':'1'}} for q in page.questions]
        for row in deepcopy(records[1:]):
            row['feedback']['rightAnswer'] = '2'
            records.append(row)
        with TemporaryDirectory() as directory,patch.object(exam,'OUTPUT',Path(directory)), \
             patch.object(exam,'require_complete_bank'),patch.object(exam,'verify_paper'), \
             patch.object(exam.AnswerBank,'load',return_value=AnswerBank(records)):
            with self.assertRaisesRegex(ValueError,'共2题需处理'):
                exam.fill_paper(page,is_mock=False)
            snapshot = json.loads((Path(directory)/'formal_paper.json').read_text(encoding='utf-8'))
            report = json.loads((Path(directory)/'formal_answer_plan.json').read_text(encoding='utf-8'))
        self.assertEqual(len(snapshot['questions']),3)
        self.assertEqual([p['subject_id'] for p in report['matched']],[1])
        self.assertEqual([p['subject_id'] for p in report['missing']],[2,3])
        self.assertEqual(report['request_id'],'one-attempt')
        self.assertEqual(page.starts,[])


if __name__ == '__main__':
    main()
