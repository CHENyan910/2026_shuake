"""将现有课程返回任务列表并检查已记录进度；不开始新课程。"""

import json
import argparse
from urllib.parse import urlsplit

from project_config import open_browser, TASK_ID
from site_adapter import Site, clock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--title', help='当前已在任务首页时，核对指定课程的已学记录')
    args = parser.parse_args()
    page = open_browser()
    site = Site(page)
    current = urlsplit(page.url).path
    expected = f'/lab-study-front/examTask/{TASK_ID}'
    if current.rstrip('/') == expected:
        print('当前已在任务首页，无需修复返回。', flush=True)
        if not args.title:
            return
        title = args.title
    else:
        if not current.startswith(expected + '/'):
            raise RuntimeError('当前不是本任务的课程详情页，未执行导航。')
        title = page.run_js("return document.querySelector('.m-right-title')?.innerText.replace(/返回/g, '').trim()")
        print('当前课程：' + (title or '未读取到标题'), flush=True)
        print('返回前页面计时：' + json.dumps(site.detail(), ensure_ascii=False), flush=True)
        page.listen.start(['/api/study/', '/api/study_upload/'], method='POST')
        site.in_course = True
        saved = site.leave_course()
        print(f'已回到任务首页；本次离开时收到新的保存响应：{saved}', flush=True)
        if not saved:
            site.wait_task()
    print('网站当前汇总：' + site.collect_courses(site.wait_task())[0].describe(), flush=True)
    for tab in page.eles('css:.ivu-tabs-tab', timeout=1):
        if tab.text.strip() == '已学':
            tab.click(by_js=True)
            break
    state = site.wait_task(1)
    _, learned = site.collect_courses(state)
    match = next((c for c in learned if c.title == title), None)
    if match:
        print(f'网站已学列表记录：{match.title} {clock(match.done)} / {clock(match.required)}', flush=True)
    else:
        print('网站已学列表暂未显示该课程，请稍后核对。', flush=True)
    site.open_task()


if __name__ == '__main__':
    main()
