"""只读检查项目已打开的课程标签页，不点击页面或提交学习记录。"""

import json
import argparse
from DrissionPage import Chromium

from project_config import CONFIG, ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--brief', action='store_true')
    args = parser.parse_args()
    browser = Chromium(f"127.0.0.1:{CONFIG['browser_port']}")
    tabs = browser.get_tabs(url="sysaq.ustc.edu.cn/lab-study-front/")
    if not tabs:
        raise RuntimeError("未找到已打开的课程标签页。")
    names_file = ROOT / "name_list.txt"
    names = names_file.read_text(encoding="utf-8").splitlines()[:3] if names_file.exists() else []
    for page in tabs:
        result = page.run_js("""
            const names = JSON.parse(arguments[0]);
            const elements = Array.from(document.querySelectorAll('a,span,div,h1,h2,h3'));
            const matches = elements.filter(e => e.childElementCount === 0 &&
                names.some(n => n && (e.textContent.trim() === n || e.textContent.trim().includes(n))));
            const vms = [...new Set(Array.from(document.querySelectorAll('*')).map(e => e.__vue__).filter(Boolean))];
            const pick = (obj, keys) => Object.fromEntries(keys.filter(k => obj && k in obj).map(k => [k, obj[k]]));
            const detail = vms.find(v => v.$store?.state?.examDetail)?.$store.state.examDetail;
            return {
                path: location.pathname,
                scripts: Array.from(document.scripts).map(e => e.src).filter(Boolean),
                courseMatches: matches.slice(0, 6).map(e => ({tag: e.tagName,
                    class: e.className, parent: e.parentElement?.outerHTML.slice(0, 7500)})),
                clocks: Array.from(document.querySelectorAll('.text-1')).map(e =>
                    ({text: e.innerText, parent: e.parentElement?.outerHTML.slice(0, 5000)})),
                buttons: Array.from(document.querySelectorAll('button')).map(e =>
                    ({text: e.innerText, class: e.className, disabled: e.disabled})),
                media: Array.from(document.querySelectorAll('video,iframe')).map(e =>
                    ({tag: e.tagName, class: e.className, paused: e.paused})),
                containers: Array.from(document.querySelectorAll('#chingo > *, .ivu-layout-content > *'))
                    .map(e => ({tag: e.tagName, class: e.className})),
                rows: Array.from(document.querySelectorAll('.ivu-table-body tbody tr')).slice(0, 3)
                    .map(e => e.outerHTML.slice(0, 11000)),
                examRequirements: pick(detail, ['id', 'mustStudyLength', 'mustStudyRequireLength',
                    'selectStudyLength', 'selectStudyRequireLength', 'practiceType', 'studyContentType']),
                studyComponents: vms.filter(v => v.$data?.studyContentParams || v.$data?.listData).map(v => ({
                    name: v.$options.name,
                    params: v.$data.studyContentParams,
                    totalElements: v.$data.totalElements,
                    data: (v.$data.data || v.$data.listData || []).map(r => pick(r, ['id', 'title', 'tableCode',
                        'mustStudy', 'studyLength', 'requireStudyLength', 'practiceStatus'])),
                })),
                timingConfig: pick(vms.find(v => v.config)?.config, ['NoOperationTime', 'uploadTime']),
                table: Array.from(document.querySelectorAll('.ivu-table-body tr.ivu-table-row')).map(row => ({
                    title: row.querySelector('.titBox .ppp')?.textContent?.trim(),
                    text: row.innerText,
                })),
                pagination: Array.from(document.querySelectorAll('.ivu-page')).map(e => e.outerHTML),
                dialogs: Array.from(document.querySelectorAll('.ivu-modal,.ivu-notice-notice,.ivu-message-notice'))
                    .filter(e => e.getClientRects().length > 0).map(e => e.innerText),
            };
        """, json.dumps(names, ensure_ascii=False))
        if args.brief:
            result = {k: result[k] for k in ['path', 'examRequirements', 'timingConfig', 'table', 'dialogs', 'media']}
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
