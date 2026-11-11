"""检查当前页面或打开题库目录；不启动模拟考试或正式考试。"""

import argparse
import json
import re
import time
from urllib.parse import urlsplit

from project_config import CONFIG, ROOT


SNAPSHOT = r"""
const visible = e => !!e && e.getClientRects().length > 0;
const vms = [...new Set(Array.from(document.querySelectorAll('*'))
    .map(e => e.__vue__).filter(Boolean))];
const unique = new Map();
for (const v of vms) if (!unique.has(v.$options.name)) unique.set(v.$options.name, v);
const practice = /^\/lab-study-front\/questionBank\/exercises\/\d+\/?$/.test(location.pathname)
    ? vms.find(v => v.$options.name === 'questionBankExercises') : null;
const summarize = (obj, depth=0) => {
    if (!obj || typeof obj !== 'object') return obj;
    if (Array.isArray(obj)) return {length:obj.length,
        sample:depth < 5 ? obj.slice(0, 2).map(v => summarize(v, depth+1)) : []};
    return Object.fromEntries(Object.entries(obj).filter(([k]) => !/Uuid|practiveId|timer/i.test(k))
        .map(([k,v]) => [k, depth < 5 ? summarize(v, depth+1) : typeof v]));
};
return {
    url: location.origin + location.pathname,
    title: document.title,
    text: document.body.innerText.slice(0, 35000),
    buttons: Array.from(document.querySelectorAll('button,a,.ivu-menu-item'))
        .filter(visible).map(e => ({text: e.innerText.trim(), tag: e.tagName,
            class: e.className, disabled: !!e.disabled,
            href: e.getAttribute('href')})),
    components: [...unique.values()].filter(v => !/^(Icon|Button|Input|Option|Select|transition|Table|TableBody|TableHead|TableCell|Page|Spin|Checkbox|Radio|Menu|MenuItem|Submenu|Dropdown|DropdownItem|DropdownMenu|Layout|Content|Header|Footer|Sider|Modal|Row|Col|Tooltip|Poptip|Form|FormItem|Notice|Message|Breadcrumb|BreadcrumbItem|Tabs|TabPane|Collapse|Panel|Scroll|Divider|Affix|BackTop|Badge|Tag|Avatar|Card|i-)/i.test(v.$options.name || ''))
        .map(v => ({name: v.$options.name, fields: Object.keys(v.$data || {}),
            methods: Object.keys(v.$options.methods || {})})),
    practice: practice ? {
        data: summarize(practice.$data),
        store: summarize(Object.fromEntries(['practicePage','pageConfig','subjectList','statusList']
            .map(k => [k, practice.$store?.state?.[k]]))),
        item: summarize(vms.find(v => v.$options.name === 'm-item')?.$props),
        itemData: summarize(vms.find(v => v.$options.name === 'm-item')?.$data),
        itemHtml: vms.find(v => v.$options.name === 'm-item')?.$el.outerHTML.slice(0, 10000),
        storeFields: Object.keys(practice.$store?.state || {}),
    } : null,
    rows: Array.from(document.querySelectorAll('.ivu-table-body tr.ivu-table-row'))
        .map(e => e.innerText),
    dialogs: Array.from(document.querySelectorAll('.ivu-modal,.ivu-message-notice'))
        .filter(visible).map(e => e.innerText),
};
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--open-bank', action='store_true', help='点击“题库练习”目录入口')
    parser.add_argument('--practice', help='从当前题库目录打开指定分类的在线练习')
    parser.add_argument('--catalog', action='store_true', help='读取题库目录全部分页；当前为练习页时先正常退出')
    args = parser.parse_args()
    from DrissionPage import Chromium, ChromiumOptions

    browser = Chromium(ChromiumOptions(read_file=False)
                       .set_address(f"127.0.0.1:{CONFIG['browser_port']}")
                       .existing_only())
    tabs = browser.get_tabs(url='sysaq.ustc.edu.cn/lab-study-front/')
    if not tabs:
        raise RuntimeError('未找到已登录的学校网站标签页。请先打开项目浏览器并登录。')
    page = tabs[0]
    allowed = re.fullmatch(r'/lab-study-front/(?:examTask/\d+|questionBank(?:/exercises/\d+)?)/?',
                           urlsplit(page.url).path)
    if not allowed:
        raise RuntimeError('当前不是任务首页或普通练习页面，未执行任何操作。')
    output = ROOT / 'exam_prep'
    output.mkdir(exist_ok=True)
    if '/questionBank/exercises/' in page.url:
        preview = page.run_js(r"""
            const vms = [...new Set(Array.from(document.querySelectorAll('.m-item'))
                .map(e => e.__vue__).filter(Boolean))];
            return vms.filter(v => v.$options.name === 'm-item').map(v => ({
                question: v.item, feedback: v.answer?.rightAnswer != null ? {
                    rightAnswer: v.answer.rightAnswer, myAnswer: v.answer.myAnswer,
                    isRight: v.answer.isRight, analysis: v.answer.analysis,
                } : null,
            }));
        """)
        write_json(output / 'practice_preview.json', {'source': urlsplit(page.url).path,
                   'questions': preview})
    if args.catalog:
        collect_catalog(page, output)
    if args.open_bank:
        if '/routine-exam/' in page.url:
            raise RuntimeError('当前标签页正在考试中，未执行导航。')
        menu = page.ele('xpath://a[normalize-space(.)="题库练习"]', timeout=3)
        if not menu:
            raise RuntimeError('未找到题库练习入口。')
        menu.click()
        page.wait(1)
    if args.practice:
        if urlsplit(page.url).path.rstrip('/') != '/lab-study-front/questionBank':
            raise RuntimeError('只有在题库目录页才能打开练习。')
        rows = page.eles('css:.ivu-table-body tr.ivu-table-row')
        row = next((r for r in rows if r.ele('tag:td').text.strip() == args.practice), None)
        if not row:
            raise RuntimeError(f'当前页未找到题库：{args.practice}')
        action = row.ele('text:在线练习')
        if not action:
            raise RuntimeError('未找到在线练习按钮。')
        action.click()
        page.wait(1)
    result = page.run_js(SNAPSHOT)
    write_json(output / 'page_snapshot.json', result)
    print(json.dumps({k: result[k] for k in ('url','title','rows','components','dialogs')},
                     ensure_ascii=False, indent=2))


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def wait_for(callback, message):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        result = callback()
        if result:
            return result
        time.sleep(0.2)
    raise RuntimeError(message)


CATALOG = r"""
return {
    rows: Array.from(document.querySelectorAll('.ivu-table-body tr.ivu-table-row'))
        .map(row => Array.from(row.querySelectorAll('td')).map(td => td.innerText.trim())),
    page: Number(document.querySelector('.ivu-page-item-active')?.textContent),
    total: document.querySelector('.ivu-page-total')?.textContent,
    next: !!document.querySelector('.ivu-page-next:not(.ivu-page-disabled)'),
};
"""


def collect_catalog(page, output):
    if '/questionBank/exercises/' in page.url:
        exit_button = page.ele('text:退出', timeout=2)
        if not exit_button:
            raise RuntimeError('找不到普通练习的退出按钮。')
        exit_button.click()
    elif urlsplit(page.url).path.rstrip('/') != '/lab-study-front/questionBank':
        menu = page.ele('xpath://a[normalize-space(.)="题库练习"]', timeout=2)
        if not menu:
            raise RuntimeError('找不到题库目录入口。')
        menu.click()
    wait_for(lambda: urlsplit(page.url).path.rstrip('/') == '/lab-study-front/questionBank',
             '当前不是题库目录页。')
    wait_for(lambda: page.run_js(CATALOG)['rows'], '题库目录未加载。')
    first = page.ele('css:.ivu-page-item[title="1"]', timeout=0.2)
    if first and page.run_js(CATALOG)['page'] != 1:
        old = page.run_js(CATALOG)
        first.click()
        wait_for(lambda: (new := page.run_js(CATALOG))['page'] == 1
                 and new['rows'] and new['rows'] != old['rows'], '未能返回目录第一页。')
    categories = []
    seen = set()
    while True:
        state = page.run_js(CATALOG)
        if state['page'] in seen:
            raise RuntimeError('目录分页重复，已停止读取。')
        seen.add(state['page'])
        for row in state['rows']:
            if len(row) < 6 or not row[2].isdigit():
                raise RuntimeError(f'无法识别题库目录行：{row}')
            categories.append({'name': row[0], 'question_count': int(row[2]),
                               'practiced_count': row[3], 'error_rate': row[4]})
        if not state['next']:
            break
        page.ele('css:.ivu-page-next:not(.ivu-page-disabled)').click()
        wait_for(lambda: (new := page.run_js(CATALOG))['page'] == state['page'] + 1
                 and new['rows'] and new['rows'] != state['rows'], '下一页目录未加载。')
    total = re.search(r'\d+', state['total'] or '')
    if not total or int(total[0]) != len(categories):
        raise RuntimeError('读取的分类数与网站目录总数不一致。')
    result = {'category_count': len(categories),
              'question_count': sum(c['question_count'] for c in categories),
              'categories': categories}
    write_json(output / 'catalog.json', result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
