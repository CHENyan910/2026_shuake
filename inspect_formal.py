"""查看正式考试说明；不确认开始、不作答、不签署承诺或交卷。"""

import json

from mock_exam import TASK_STATE, connect, current_exam, open_task
from practice_bank import OUTPUT, write_json


def main():
    page = connect()
    active = current_exam(page)
    if active:
        raise RuntimeError('账号已有正在进行的考试，已保留原页面，未执行导航。')
    open_task(page)
    button = page.ele('xpath://button[normalize-space(.)="开始考试"]',timeout=3)
    if not button:
        raise RuntimeError('未找到任务首页的正式考试说明入口。')
    button.click(by_js=True)
    page.wait(1)
    result = page.run_js(TASK_STATE)
    result['detail_components'] = page.run_js(r"""
        const vms=[...new Set(Array.from(document.querySelectorAll('*')).map(e=>e.__vue__).filter(Boolean))];
        return vms.filter(v=>v.$options.name==='GoExam').map(v=>({
            examId:v.examId,isMockExam:v.isMockExam,
            data:v.$refs.ExamDetail?.$data,
            statuses:v.constObject?.STUDENT_EXAMING_STATUS,
            promise:v.$refs.promise ? {data:v.$refs.promise.$data,
                methods:Object.fromEntries(Object.entries(v.$refs.promise.$options.methods||{})
                    .map(([k,f])=>[k,String(f)])),render:String(v.$refs.promise.$options.render)} : null,
        }));
    """)
    write_json(OUTPUT / 'formal_instructions.json',result)
    print(json.dumps({'path':result['path'],'dialogs':result['dialogs'],
                      'buttons':result['buttons'],'exam':result['detail'],
                      'detail_components':result['detail_components']},
                     ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
