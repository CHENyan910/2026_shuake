"""通过学校网站普通练习页整理题库及网站反馈；不操作考试页面。"""

import argparse
import csv
import html
import json
import re
import time
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import urlsplit

from project_config import CONFIG, ROOT

OUTPUT = ROOT / 'exam_prep'
PRACTICE_PATH = re.compile(r'/lab-study-front/questionBank/exercises/\d+/?')


def write_json(path, value):
    path.parent.mkdir(exist_ok=True, parents=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def wait_for(callback, message, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = callback()
        if value:
            return value
        time.sleep(0.15)
    raise RuntimeError(message)


def connect():
    from DrissionPage import Chromium, ChromiumOptions
    browser = Chromium(ChromiumOptions(read_file=False)
                       .set_address(f"127.0.0.1:{CONFIG['browser_port']}").existing_only())
    tabs = browser.get_tabs(url='sysaq.ustc.edu.cn/lab-study-front/')
    if not tabs:
        raise RuntimeError('请先在项目浏览器中登录学校网站。')
    page = tabs[0]
    if not re.fullmatch(r'/lab-study-front/(?:examTask/\d+|questionBank(?:/exercises/\d+)?)/?',
                        urlsplit(page.url).path):
        raise RuntimeError('当前不是任务首页或普通练习页，未操作考试页面。')
    return page


def catalog(page):
    from inspect_exam import collect_catalog
    collect_catalog(page, OUTPUT)
    return json.loads((OUTPUT / 'catalog.json').read_text(encoding='utf-8'))


def open_category(page, name):
    from inspect_exam import CATALOG
    path = urlsplit(page.url).path.rstrip('/')
    if PRACTICE_PATH.fullmatch(path):
        page.ele('text:退出', timeout=3).click()
    elif path != '/lab-study-front/questionBank':
        page.ele('xpath://a[normalize-space(.)="题库练习"]', timeout=3).click()
    wait_for(lambda: urlsplit(page.url).path.rstrip('/') == '/lab-study-front/questionBank',
             '未正常返回题库目录。')
    wait_for(lambda: page.run_js(CATALOG)['rows'], '题库目录未加载。')
    state = page.run_js(CATALOG)
    if state['page'] != 1:
        old = state
        page.ele('css:.ivu-page-item[title="1"]').click()
        wait_for(lambda: (s := page.run_js(CATALOG))['page'] == 1
                 and s['rows'] != old['rows'], '目录第一页未加载。')
    for _ in range(10):
        rows = page.eles('css:.ivu-table-body tr.ivu-table-row')
        row = next((r for r in rows if r.ele('tag:td').text.strip() == name), None)
        if row:
            row.ele('text:在线练习').click()
            def ready():
                if PRACTICE_PATH.fullmatch(urlsplit(page.url).path):
                    return True
                resume = page.ele('text:继续练习', timeout=0.1)
                if resume and resume.states.is_displayed:
                    resume.click(by_js=True)
                return False
            wait_for(ready, '普通练习未打开。')
            return wait_for(lambda: (s := read_state(page))['questions'] and s, '练习题目未加载。')
        old = page.run_js(CATALOG)
        if not old['next']:
            break
        page.ele('css:.ivu-page-next:not(.ivu-page-disabled)').click()
        wait_for(lambda: (s := page.run_js(CATALOG))['page'] == old['page'] + 1
                 and s['rows'] and s['rows'] != old['rows'], '目录翻页未完成。')
    raise RuntimeError(f'未找到题库：{name}')


STATE = r"""
if (!/^\/lab-study-front\/questionBank\/exercises\/\d+\/?$/.test(location.pathname))
    throw new Error('只读取普通练习页');
const vms = [...new Set(Array.from(document.querySelectorAll('*')).map(e => e.__vue__).filter(Boolean))];
const vm = vms.find(v => v.$options.name === 'questionBankExercises');
const items = vms.filter(v => v.$options.name === 'm-item');
return {
    bank_id: vm?.id, page: vm?.$store.state.practicePage, size: vm?.size,
    total: vm?.$store.state.pageConfig.subjectCount, end: vm?.pageEnd,
    questions: items.map(v => ({question:v.item, feedback:v.answer?.rightAnswer != null ? {
        rightAnswer:v.answer.rightAnswer, myAnswer:v.answer.myAnswer,
        isRight:v.answer.isRight, analysis:v.answer.analysis,
    } : null, disabled:v.disabled})),
};
"""


def read_state(page):
    return page.run_js(STATE)


ANSWER_BATCH = r"""
if (!/^\/lab-study-front\/questionBank\/exercises\/\d+\/?$/.test(location.pathname))
    throw new Error('只允许普通练习');
const items = [...new Set(Array.from(document.querySelectorAll('.m-item'))
    .map(e => e.__vue__).filter(Boolean))];
const pending = items.filter(v => v.answer?.rightAnswer == null).slice(0, 3);
const started = [];
for (const v of pending) {
    if (v.disabled) continue;
    const radio = v.$el.querySelector('.ivu-radio-wrapper:not(.ivu-radio-wrapper-disabled)');
    const checkbox = v.$el.querySelector('.ivu-checkbox-wrapper:not(.ivu-checkbox-wrapper-disabled)');
    if (radio) radio.click();
    else if (checkbox) { checkbox.click(); v.submitBtn(); }
    else throw new Error('未知题型：' + v.item.id + '/' + v.item.subjectType);
    started.push(v.item.id);
}
return started;
"""


def answer_page(page):
    while True:
        state = read_state(page)
        pending = [r for r in state['questions'] if r['feedback'] is None]
        if not pending:
            return state
        started = page.run_js(ANSWER_BATCH)
        if not started:
            raise RuntimeError('题目仍未返回答案，已停止以避免重复提交。')
        def finished():
            current = read_state(page)
            received = {r['question']['id'] for r in current['questions'] if r['feedback'] is not None}
            return current if set(started) <= received else False
        wait_for(finished, f'练习答案反馈超时，题目 {started}。', timeout=35)
        time.sleep(0.2)


def turn_page(page, old, backwards=False):
    target = old['page'] + (-1 if backwards else 1)
    ids = [r['question']['id'] for r in old['questions']]
    def turn():
        return page.run_js(r"""
            const backwards = arguments[0];
            if (!/^\/lab-study-front\/questionBank\/exercises\/\d+\/?$/.test(location.pathname))
                throw new Error('只允许练习翻页');
            const vm = [...new Set(Array.from(document.querySelectorAll('*'))
                .map(e => e.__vue__).filter(Boolean))]
                .find(v => v.$options.name === 'questionBankExercises');
            if (!vm || vm.toTopTimer) return false;
            backwards ? vm.prevPage() : vm.nextPage();
            return true;
        """, backwards)
    wait_for(turn, '等待练习页滚动完成超时。')
    return wait_for(lambda: (s := read_state(page))['page'] == target and s['questions']
                    and [r['question']['id'] for r in s['questions']] != ids and s,
                    '练习翻页数据未更新。')


def clean_text(value):
    return html.unescape(re.sub(r'<[^>]+>', '', str(value or ''))).strip()


def answer_display(question, raw):
    if question['subjectType'] == 1:
        return {'true':'正确', 'false':'错误', '1':'正确', '0':'错误'}.get(str(raw).lower(), str(raw))
    values = str(raw).split(',')
    options = question.get('subjectItems') or []
    if not isinstance(options, list):
        return str(raw)
    labels = {str(o['id']): chr(65 + i) for i, o in enumerate(options)}
    return ','.join(labels.get(v.strip(), v.strip()) for v in values)


def export_summary():
    categories = []
    for path in sorted((OUTPUT / 'banks').glob('*.json')):
        categories.append(json.loads(path.read_text(encoding='utf-8')))
    questions = [dict(category=b['name'], **r) for b in categories for r in b['questions']]
    notes_path = OUTPUT / 'review_notes.json'
    notes = json.loads(notes_path.read_text(encoding='utf-8')) if notes_path.exists() else {}
    summary = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'source': '学校普通题库练习作答反馈',
        'category_count': len(categories), 'question_count':len(questions),
        'verified_answers':sum(r['feedback'] is not None for r in questions),
        'types':dict(Counter(r['question']['subjectType'] for r in questions)),
        'review_notes':notes,
        'categories':[{'name':b['name'],'expected':b['expected'],
                       'collected':len(b['questions']),'complete':b['complete']} for b in categories],
        'questions':questions,
    }
    write_json(OUTPUT / 'question_bank.json', summary)
    with (OUTPUT / '题库汇总.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['分类','网站题号','题型','题目','选项','正确答案','答案原值','网站解析','复习备注'])
        for row in questions:
            q, f = row['question'], row['feedback']
            opts = q.get('subjectItems') or []
            option_text = '\n'.join(f'{chr(65+i)}. {clean_text(o.get("content"))}'
                                    for i,o in enumerate(opts)) if isinstance(opts,list) else str(opts)
            writer.writerow([row['category'],q['id'],{1:'判断',2:'单选',3:'多选'}.get(q['subjectType'],q['subjectType']),
                             clean_text(q['subjectContent']),option_text,
                             answer_display(q,f['rightAnswer']) if f else '',
                             f['rightAnswer'] if f else '',clean_text(f.get('analysis')) if f else '',
                             notes.get(str(q['id']),'')])
    return summary


def collect(page, args):
    manifest = json.loads((OUTPUT / 'catalog.json').read_text(encoding='utf-8'))
    categories = manifest['categories']
    if args.category:
        categories = [c for c in categories if c['name'] == args.category]
        if not categories:
            raise RuntimeError('未找到指定分类。')
    else:
        categories.sort(key=lambda c: (c['name'] != '化学安全', c['name'] != '通识类题库'))
    for category in categories:
        path = OUTPUT / 'banks' / (category['name'] + '.json')
        if path.exists():
            prior = json.loads(path.read_text(encoding='utf-8'))
            if prior.get('complete') and len(prior['questions']) == category['question_count']:
                print(f"已完成，跳过：{category['name']}", flush=True)
                continue
        state = open_category(page, category['name'])
        if state['total'] != category['question_count']:
            raise RuntimeError('网站题目数量已变化，请重新检查目录。')
        while state['page'] > 0:
            state = turn_page(page, state, backwards=True)
        expected_ids = page.run_js(r"""
            const vm = [...new Set(Array.from(document.querySelectorAll('*'))
                .map(e => e.__vue__).filter(Boolean))]
                .find(v => v.$options.name === 'questionBankExercises');
            return vm.$store.state.statusList.flat().map(s => s.subjectId);
        """)
        records = {}
        page_count = 0
        while True:
            state = answer_page(page)
            for row in state['questions']:
                q = row['question']
                if not row['feedback'] or not q.get('subjectContent'):
                    raise RuntimeError('题目或网站答案缺失。')
                records[q['id']] = {'question':q, 'feedback':row['feedback']}
            page_count += 1
            complete = len(records) == category['question_count'] and set(records) == set(expected_ids)
            result = {'name':category['name'],'bank_id':state['bank_id'],
                      'expected':category['question_count'],'complete':complete,
                      'questions':list(records.values())}
            write_json(path, result)
            print(f"{category['name']}：{len(records)}/{category['question_count']}，第 {state['page']+1} 页，网站答案已保存", flush=True)
            if args.max_pages and page_count >= args.max_pages:
                export_summary()
                return
            if complete:
                break
            if len(records) > category['question_count'] or len(state['questions']) < state['size']:
                raise RuntimeError('读取到末页但总数或题目编号不匹配。')
            state = turn_page(page, state)
        export_summary()
    summary = export_summary()
    print(json.dumps({k:v for k,v in summary.items() if k != 'questions'}, ensure_ascii=False, indent=2), flush=True)


def probe(page):
    state = read_state(page)
    details = page.run_js(r"""
        const vms = [...new Set(Array.from(document.querySelectorAll('*')).map(e => e.__vue__).filter(Boolean))];
        const item = vms.find(v => v.$options.name === 'm-item');
        const vm = vms.find(v => v.$options.name === 'questionBankExercises');
        return {itemMethods: Object.fromEntries(Object.entries(item.$options.methods)
                    .map(([k,v]) => [k, String(v)])),
                pageMethods:Object.fromEntries(['prevPage','nextPage','getExamData','next']
                    .map(k => [k, String(vm.$options.methods[k])])),
                html:item.$el.outerHTML};
    """)
    result = {'state':state, 'details':details}
    write_json(OUTPUT / 'practice_probe.json', result)
    print(json.dumps({'bank_id':state['bank_id'], 'page':state['page'], 'total':state['total'],
                      'details':details}, ensure_ascii=False, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['probe','collect','export'])
    parser.add_argument('--category')
    parser.add_argument('--max-pages', type=int, default=0)
    args = parser.parse_args()
    OUTPUT.mkdir(exist_ok=True)
    if args.command == 'export':
        export_summary()
        return
    page = connect()
    if args.command == 'probe':
        open_category(page, args.category or '化学安全')
        probe(page)
    elif args.command == 'collect':
        collect(page, args)


if __name__ == '__main__':
    main()
