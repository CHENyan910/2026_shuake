"""题库答案匹配；按选项内容映射，避免试卷选项重排导致误答。"""

import json
import re
import unicodedata
from collections import defaultdict
from html import unescape
from html.parser import HTMLParser
from pathlib import Path


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag == 'img':
            values = dict(attrs)
            self.parts.append('[图片:' + values.get('src', '') + ']')


def text_content(value):
    parser = PlainText()
    parser.feed(unescape(str(value or '')))
    return ''.join(parser.parts).strip()


def normalize(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text_content(value)))


def truth_value(raw):
    value = str(raw).strip().lower()
    if value in {'true', '1', '正确', '对', '是'}:
        return True
    if value in {'false', '0', '2', '错误', '错', '否'}:
        return False
    raise ValueError(f'无法识别判断答案：{raw!r}')


def answer_tokens(raw):
    if isinstance(raw, (list, tuple)):
        return [str(v).strip() for v in raw]
    return [v.strip() for v in str(raw).split(',') if v.strip()]


def answer_signature(record):
    question, feedback = record['question'], record['feedback']
    if feedback is None:
        raise ValueError('题库缺少网站答案。')
    if question['subjectType'] == 1:
        return ('truth', truth_value(feedback['rightAnswer']))
    options = question.get('subjectItems') or []
    by_id = {str(o['id']): normalize(o['content']) for o in options}
    tokens = answer_tokens(feedback['rightAnswer'])
    if not tokens or any(t not in by_id for t in tokens):
        raise ValueError('网站正确答案与题库选项编号不匹配。')
    return ('choices', tuple(sorted(by_id[t] for t in tokens)))


class AnswerBank:
    def __init__(self, records, *, resolutions=()):
        self.by_id = defaultdict(list)
        self.by_text = defaultdict(list)
        self.resolutions = list(resolutions)
        for record in records:
            if record.get('feedback') is None:
                continue
            q = record['question']
            self.by_id[str(q['id'])].append(record)
            self.by_text[(q['subjectType'], normalize(q['subjectContent']))].append(record)

    @classmethod
    def load(cls, path):
        data = json.loads(path.read_text(encoding='utf-8'))
        if data['question_count'] != data['verified_answers']:
            raise ValueError('题库尚有未经网站确认的答案。')
        resolutions = json.loads(Path(__file__).with_name('answer_resolutions.json')
                                 .read_text(encoding='utf-8'))['resolutions']
        return cls(data['questions'],resolutions=resolutions)

    def resolve_conflict(self, question, candidates):
        """只应用题目、来源及原始答案均已核对的规则；新增冲突仍然停止。"""
        actual = defaultdict(set)
        for record in candidates:
            actual[str(record['question']['id'])].add(answer_signature(record))
        for rule in self.resolutions:
            if (question['subjectType'] != rule['subject_type']
                    or normalize(question['subjectContent']) != normalize(rule['subject_content'])
                    or sorted(normalize(o['content']) for o in question.get('subjectItems') or [])
                    != sorted(normalize(o) for o in rule['option_contents'])):
                continue
            expected = {str(entry['bank_id']):{('choices',tuple(sorted(
                normalize(value) for value in entry['correct_contents'])))}
                for entry in rule['candidate_answers']}
            if dict(actual) != expected:
                continue
            selected = expected.get(str(rule['selected_bank_id']))
            if selected:
                return next(iter(selected)), rule
        raise ValueError(f"题库答案存在冲突：{question['id']}")

    def match(self, question):
        key = (question['subjectType'], normalize(question['subjectContent']))
        candidates = [r for r in self.by_id.get(str(question['id']), [])
                      if (r['question']['subjectType'], normalize(r['question']['subjectContent'])) == key]
        source = '网站题号及题干'
        if not candidates:
            candidates = self.by_text.get(key, [])
            source = '题干及选项内容'
        if question['subjectType'] != 1:
            option_set = sorted(normalize(o['content']) for o in question.get('subjectItems') or [])
            candidates = [r for r in candidates if sorted(normalize(o['content'])
                          for o in r['question'].get('subjectItems') or []) == option_set]
        if not candidates:
            raise ValueError(f"题库未覆盖：{question['id']} {text_content(question['subjectContent'])[:80]}")
        signatures = {answer_signature(r) for r in candidates}
        resolution = None
        if len(signatures) != 1:
            (kind, correct), resolution = self.resolve_conflict(question,candidates)
            source += '（已核对冲突规则）'
        else:
            kind, correct = signatures.pop()
        if kind == 'truth':
            answer = correct
        else:
            options = question.get('subjectItems') or []
            contents = [normalize(o['content']) for o in options]
            if any(contents.count(value) != 1 for value in correct):
                raise ValueError('正确选项内容重复，不能唯一映射。')
            answer = [o['id'] for o in options if normalize(o['content']) in correct]
            if len(answer) != len(correct):
                raise ValueError('选项映射不完整。')
        result = {'subject_id':question['id'], 'type':question['subjectType'],
                  'answer':answer, 'source':source,
                  'bank_ids':sorted({r['question']['id'] for r in candidates})}
        if resolution:
            result['resolution'] = {k:resolution[k] for k in ('selected_bank_id','reason','sources')}
        return result
