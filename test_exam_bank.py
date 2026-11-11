import unittest
import json
from copy import deepcopy
from pathlib import Path

from exam_bank import AnswerBank


def record(question_id=10, content='应当如何处置？', answer='1', kind=2, options=None):
    return {'question':{'id':question_id, 'subjectType':kind, 'subjectContent':content,
                        'subjectItems':options if options is not None else [
                            {'id':1,'content':'方法甲'},{'id':2,'content':'方法乙'}]},
            'feedback':{'rightAnswer':answer}}


class MatchingTests(unittest.TestCase):
    def test_shuffled_option_identifiers(self):
        bank = AnswerBank([record()])
        question = record(options=[{'id':1,'content':'方法乙'},{'id':7,'content':'方法甲'}])['question']
        self.assertEqual(bank.match(question)['answer'], [7])

    def test_text_match_across_different_question_ids(self):
        bank = AnswerBank([record(content='<p>应当如何处置？</p>')])
        question = record(question_id=999)['question']
        self.assertEqual(bank.match(question)['answer'], [1])

    def test_conflicting_answers_are_rejected(self):
        bank = AnswerBank([record(answer='1'),record(answer='2')])
        with self.assertRaisesRegex(ValueError, '冲突'):
            bank.match(record()['question'])

    def test_different_options_are_not_matched(self):
        bank = AnswerBank([record()])
        question = record(options=[{'id':1,'content':'其他方法'}])['question']
        with self.assertRaisesRegex(ValueError, '未覆盖'):
            bank.match(question)

    def test_multiple_choice_mapping(self):
        bank = AnswerBank([record(answer='1,2',kind=3)])
        question = record(kind=3,options=[{'id':8,'content':'方法乙'},{'id':9,'content':'方法甲'}])['question']
        self.assertEqual(bank.match(question)['answer'], [8,9])

    def test_judgment_false_is_not_truthy_string(self):
        bank = AnswerBank([record(answer='false',kind=1,options=[])])
        self.assertIs(bank.match(record(kind=1,options=[])['question'])['answer'], False)

    def test_question_images_remain_part_of_identity(self):
        bank = AnswerBank([record(content='标识<img src="a.png">的意义？')])
        with self.assertRaisesRegex(ValueError, '未覆盖'):
            bank.match(record(content='标识<img src="b.png">的意义？')['question'])

    def test_duplicate_distractors_do_not_block_unique_correct_option(self):
        row = record(options=[{'id':1,'content':'正确选项'},{'id':2,'content':'干扰选项'},
                              {'id':3,'content':'干扰选项'}])
        self.assertEqual(AnswerBank([row]).match(row['question'])['answer'], [1])

    def test_ambiguous_correct_option_is_rejected(self):
        row = record(options=[{'id':1,'content':'相同内容'},{'id':2,'content':'相同内容'}])
        with self.assertRaisesRegex(ValueError, '正确选项内容重复'):
            AnswerBank([row]).match(row['question'])


class ReviewedConflictTests(unittest.TestCase):
    def setUp(self):
        self.rules = json.loads(Path(__file__).with_name('answer_resolutions.json')
                                .read_text(encoding='utf-8'))['resolutions']
        self.rows = [record(question_id=15517,answer='3',
                            content=self.rules[0]['subject_content'],options=[
                                {'id':1,'content':'先将试剂瓶在热水中浸泡一段时间'},
                                {'id':2,'content':'振荡一段时间'},
                                {'id':3,'content':'先将试剂瓶在冷水中浸泡一段时间'},
                                {'id':4,'content':'先将试剂瓶颠倒一下'}])]
        duplicate = deepcopy(self.rows[0])
        duplicate['question']['id'] = 15881
        duplicate['feedback']['rightAnswer'] = '1'
        self.rows.append(duplicate)
        self.question = deepcopy(self.rows[0]['question'])
        self.question['id'] = 20983

    def test_new_exam_id_maps_reviewed_cold_water_answer_after_option_shuffle(self):
        self.question['subjectItems'] = [
            {'id':7,'content':'先将试剂瓶颠倒一下'},
            {'id':8,'content':'先将试剂瓶在热水中浸泡一段时间'},
            {'id':9,'content':'先将试剂瓶在冷水中浸泡一段时间'},
            {'id':10,'content':'振荡一段时间'}]
        plan = AnswerBank(self.rows,resolutions=self.rules).match(self.question)
        self.assertEqual(plan['answer'],[9])
        self.assertEqual(plan['bank_ids'],[15517,15881])
        self.assertEqual(plan['resolution']['selected_bank_id'],15517)
        self.assertTrue(plan['resolution']['sources'])

    def test_original_site_id_retains_original_feedback(self):
        plan = AnswerBank(self.rows,resolutions=self.rules).match(self.rows[1]['question'])
        self.assertEqual(plan['answer'],[1])
        self.assertNotIn('resolution',plan)

    def test_changed_source_answer_requires_new_review(self):
        self.rows[1]['feedback']['rightAnswer'] = '2'
        with self.assertRaisesRegex(ValueError,'冲突'):
            AnswerBank(self.rows,resolutions=self.rules).match(self.question)

    def test_new_conflicting_source_requires_new_review(self):
        new = deepcopy(self.rows[1])
        new['question']['id'] = 999
        self.rows.append(new)
        with self.assertRaisesRegex(ValueError,'冲突'):
            AnswerBank(self.rows,resolutions=self.rules).match(self.question)

    def test_changed_options_do_not_use_old_rule(self):
        self.question['subjectItems'][3]['content'] = '其他做法'
        for row in self.rows:
            row['question']['subjectItems'][3]['content'] = '其他做法'
        with self.assertRaisesRegex(ValueError,'冲突'):
            AnswerBank(self.rows,resolutions=self.rules).match(self.question)


if __name__ == '__main__':
    unittest.main()
