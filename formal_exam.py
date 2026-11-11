"""正式考试流程；支持按用户明确授权确认已核对的安全承诺。"""

import argparse
import hashlib
import json
import time
from urllib.parse import urlsplit

from exam_bank import normalize
from mock_exam import (PAPER_STATE, connect, current_exam, fill_paper, read_result,
                       open_task, prepare_answer_plan, require_complete_bank,
                       submit_when_ready, verify_paper)
from practice_bank import OUTPUT, wait_for, write_json
from project_config import TASK_ID, STUDY_URL


AUTHORIZED_PROMISE = (
    '实验室安全承诺书本人承诺积极参加学校与学院的实验室安全知识的学习与考核，并保证：'
    '在校期间，将严格遵守实验室各项规章制度与操作规程，服从实验室管理，并不断加强学习，'
    '防止安全事故的发生。如因自己违反相关规定发生实验室安全环保事故，造成人身伤害、'
    '财产损失或环境污染，本人愿意承担相应责任。'
)


def validate_promise(content, promise_type):
    if str(promise_type) != '1':
        raise RuntimeError('网站当前要求扫码或手写签名，请按网页完成，程序不会生成签名。')
    body = normalize(content).split('所在单位:',1)[0]
    if body != normalize(AUTHORIZED_PROMISE):
        raise RuntimeError('网站安全承诺内容与本次授权的原文不一致，未自动确认。')


def validate_active(active):
    if active and (active.get('is_mock') != 0 or str(active.get('exam_id')) != TASK_ID):
        raise RuntimeError('当前账号正在进行模拟或其他任务的考试，已保留原页面。')


LAUNCH_STATE = r"""
const taskId=arguments[0];
const visible=e=>!!e&&e.getClientRects().length>0;
const vms=[...new Set(Array.from(document.querySelectorAll('*')).map(e=>e.__vue__).filter(Boolean))];
const owner=vms.find(v=>v.$options.name==='GoExam'&&Number(v.isMockExam)===0&&String(v.examId)===taskId);
const promise=owner?.$refs.promise;
const dialogs=Array.from(document.querySelectorAll('.ivu-modal')).filter(visible);
return {path:location.pathname,
    paper_ready:vms.some(v=>v.$options.name==='studentExaming'&&v.subjectDataList?.length),
    formal_owner:!!owner,dialogs:dialogs.map(e=>e.innerText),
    promise:promise?.flag?{type:promise.exam?.type,content:promise.exam?.content,
        checked:promise.promise,request_id:promise.examRequestId,
        submit_ready:dialogs.some(e=>e.innerText.includes('实验室安全承诺书')&&
            Array.from(e.querySelectorAll('button')).some(b=>b.innerText.trim()==='开始考试'&&!b.disabled))}:null,
    detail:!!owner?.$refs.ExamDetail?.flag,
    messages:Array.from(document.querySelectorAll('.ivu-message-notice')).filter(visible).map(e=>e.innerText),
};
"""


def launch_stage(state):
    if state['path'].rstrip('/') == f'/lab-study-front/routine-exam/{TASK_ID}':
        return 'paper' if state['paper_ready'] else 'loading'
    if state.get('promise'):
        if not state['formal_owner']:
            raise RuntimeError('未确认当前承诺属于本任务的正式考试。')
        return 'promise' if state['promise'].get('type') else 'loading'
    text = '\n'.join(state['dialogs'])
    if state['dialogs']:
        if not state['formal_owner']:
            raise RuntimeError('当前弹窗不是本任务的正式考试，已停止。')
        if '您已完成排队' in text:
            return 'queue_ready'
        if '排队等待' in text or '排在第' in text:
            return 'queue'
        if '试卷生成中' in text:
            return 'generating'
        if state['detail'] and '开始考试' in text:
            return 'confirm'
        raise RuntimeError('网站提示需要处理：' + text)
    if state['path'].rstrip('/') == f'/lab-study-front/examTask/{TASK_ID}':
        return 'home'
    return 'loading'


def start_formal(page):
    button = page.ele('xpath://button[normalize-space(.)="开始考试"]',timeout=3)
    if not button:
        raise RuntimeError('未找到正式考试入口。')
    button.click(by_js=True)


def click_formal_modal(page, kind):
    page.run_js(r"""
        const taskId=arguments[0],kind=arguments[1];
        const vms=[...new Set(Array.from(document.querySelectorAll('*')).map(e=>e.__vue__).filter(Boolean))];
        const owner=vms.find(v=>v.$options.name==='GoExam'&&Number(v.isMockExam)===0&&String(v.examId)===taskId);
        if(!owner)throw new Error('未确认正式考试模式');
        const modal=Array.from(document.querySelectorAll('.ivu-modal')).find(e=>e.getClientRects().length&&(
            kind==='queue'?e.innerText.includes('您已完成排队'):
            kind==='promise'?e.innerText.includes('实验室安全承诺书'):
            owner.$refs.ExamDetail?.flag&&e.innerText.includes('考试准入')));
        const label=kind==='queue'?'进入考试':'开始考试';
        const button=modal&&Array.from(modal.querySelectorAll('button'))
            .find(e=>e.innerText.trim()===label&&!e.disabled);
        if(!button)throw new Error('未找到当前步骤的正常确认按钮');
        if(kind==='promise'&&(!owner.$refs.promise?.promise||Number(owner.$refs.promise.exam?.type)!==1))
            throw new Error('承诺尚未正常勾选');
        button.click();
    """,TASK_ID,kind)


def confirm_promise(page, promise):
    validate_promise(promise.get('content'),promise.get('type'))
    active = current_exam(page)
    validate_active(active)
    if not active or active.get('request_id') != promise.get('request_id'):
        raise RuntimeError('安全承诺与当前正式考试编号不一致，未确认。')
    record = {'exam_id':TASK_ID,'request_id':promise['request_id'],'content':promise['content'],
              'content_sha256':hashlib.sha256(promise['content'].encode('utf-8')).hexdigest(),
              'authorized_action':'用户明确要求自动勾选并确认本安全承诺',
              'confirmed':False,'time':time.strftime('%Y-%m-%d %H:%M:%S')}
    write_json(OUTPUT / 'formal_promise_confirmation.json',record)
    page.run_js(r"""
        const modal=Array.from(document.querySelectorAll('.ivu-modal')).find(e=>e.getClientRects().length&&
            e.innerText.includes('实验室安全承诺书'));
        const checkbox=modal&&Array.from(modal.querySelectorAll('.ivu-checkbox-wrapper'))
            .find(e=>e.innerText.includes('已认真阅读并将遵守实验室安全承诺书内容'));
        if(!checkbox)throw new Error('未找到网站承诺勾选框');
        if(!checkbox.classList.contains('ivu-checkbox-wrapper-checked'))checkbox.click();
    """)
    wait_for(lambda: (p := (page.run_js(LAUNCH_STATE,TASK_ID).get('promise') or {})).get('checked')
             and p.get('submit_ready'),
             '安全承诺勾选状态未更新。')
    page.listen.start('/api/exam_center/promise',method='POST')
    click_formal_modal(page,'promise')
    response = page.listen.wait(timeout=20)
    if not response:
        raise RuntimeError('未收到网站安全承诺确认响应，请检查页面。')
    body = response.response.body
    record['response'] = body
    record['confirmed'] = isinstance(body,dict) and body.get('code') == 200
    write_json(OUTPUT / 'formal_promise_confirmation.json',record)
    if not record['confirmed']:
        raise RuntimeError('网站未确认安全承诺成功，已停止。')


def restore_launch(page):
    """按服务器返回的阶段恢复现有记录，不重新创建一次考试。"""
    page.run_js(r"""
        const taskId=arguments[0];
        const vms=[...new Set(Array.from(document.querySelectorAll('*')).map(e=>e.__vue__).filter(Boolean))];
        const owner=vms.find(v=>v.$options.name==='GoExam'&&String(v.examId)===taskId&&Number(v.isMockExam)===0)
            ||vms.find(v=>v.$options.name==='GoExam');
        if(!owner)throw new Error('未找到考试入口组件');
        return new Promise((resolve,reject)=>owner.commonMethods.emitAjax({
            path:'/api/exam_center/current_exam',success:e=>{
                if(!e||e.isMockExam!==0||String(e.exam?.id)!==taskId||![2,3,4].includes(e.nextStep))
                    return reject(new Error('现有正式考试阶段不符合恢复条件'));
                owner.examId=e.exam.id;owner.isMockExam=e.isMockExam;owner.examRequestId=e.examRequestId;
                if(owner.$refs.ExamDetail)owner.$refs.ExamDetail.flag=false;
                owner.stepto(e);resolve(true);
            },error:e=>reject(new Error(e.message||'恢复考试状态失败')),
        }));
    """,TASK_ID)


def launch_formal(page):
    active = current_exam(page)
    validate_active(active)
    if active and active.get('next_step') == 5:
        if urlsplit(page.url).path.rstrip('/') != f'/lab-study-front/routine-exam/{TASK_ID}':
            from urllib.parse import urlencode
            page.get(f'https://sysaq.ustc.edu.cn/lab-study-front/routine-exam/{TASK_ID}?'
                     + urlencode({'examRequestId':active['request_id']}))
    else:
        state = page.run_js(LAUNCH_STATE,TASK_ID)
        if state['path'].rstrip('/') != f'/lab-study-front/examTask/{TASK_ID}':
            open_task(page)
        state = page.run_js(LAUNCH_STATE,TASK_ID)
        if active:
            stage = launch_stage(state)
            expected = {2:{'promise','loading'},3:{'queue','queue_ready'},4:{'generating'}}
            if stage not in expected.get(active.get('next_step'),set()):
                restore_launch(page)
    deadline = time.monotonic()+30*60
    last_report = 0
    last_stage = None
    while time.monotonic() < deadline:
        state = page.run_js(LAUNCH_STATE,TASK_ID)
        stage = launch_stage(state)
        now = time.monotonic()
        if stage != last_stage or now-last_report >= 30:
            labels = {'home':'打开正式考试说明','confirm':'确认开始本次正式考试',
                      'promise':'确认已授权的实验室安全承诺','queue':'网站正在排队',
                      'queue_ready':'排队完成，进入正式试卷','generating':'网站正在生成试卷',
                      'loading':'等待网站加载','paper':'正式试卷已就绪'}
            print(labels[stage],flush=True)
            last_stage,last_report = stage,now
        if stage == 'paper':
            require_formal_paper(page)
            return
        if stage == 'home':
            start_formal(page)
        elif stage == 'confirm':
            click_formal_modal(page,'detail')
        elif stage == 'promise':
            confirm_promise(page,state['promise'])
        elif stage == 'queue_ready':
            click_formal_modal(page,'queue')
        else:
            time.sleep(1)
            continue
        wait_for(lambda: launch_stage(page.run_js(LAUNCH_STATE,TASK_ID)) != stage,
                 '正式考试操作后页面未更新，请查看网站提示。',timeout=45)
    raise RuntimeError('网站排队或组卷等待超时，已保留当前考试记录。')


def require_formal_paper(page):
    active = current_exam(page)
    if not active or active.get('is_mock') != 0 or str(active.get('exam_id')) != TASK_ID:
        raise RuntimeError('尚未进入本任务正式试卷，请选择菜单12自动处理已授权承诺，或由本人在网页进入。')
    if active.get('next_step') != 5 or urlsplit(page.url).path.rstrip('/') != f'/lab-study-front/routine-exam/{TASK_ID}':
        raise RuntimeError('尚未完成承诺、排队或进入试卷步骤，请选择菜单12自动处理，或由本人在网页完成。')
    verify_paper(page,is_mock=False)
    return active


def status(page):
    active = current_exam(page)
    result = {'path':urlsplit(page.url).path,'active':active,
              'dialogs':page.run_js(r"""
                return Array.from(document.querySelectorAll('.ivu-modal'))
                    .filter(e=>e.getClientRects().length).map(e=>e.innerText);
              """)}
    write_json(OUTPUT / 'formal_status.json',result)
    if (active and active.get('is_mock') == 0 and str(active.get('exam_id')) == TASK_ID
            and active.get('next_step') == 5
            and result['path'].rstrip('/') == f'/lab-study-front/routine-exam/{TASK_ID}'):
        paper = page.run_js(PAPER_STATE)
        write_json(OUTPUT / 'formal_paper.json',paper)
        result['paper_summary'] = {'question_count':len(paper['questions']),
                                  'remaining_seconds':paper['remaining']}
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)


def run(page, *, confirm_safety_promise=False):
    require_complete_bank()
    if confirm_safety_promise:
        launch_formal(page)
    require_formal_paper(page)
    old_result = OUTPUT / 'formal_result.json'
    previous = json.loads(old_result.read_text(encoding='utf-8')).get('session_id') if old_result.exists() else None
    fill_paper(page,is_mock=False)
    submit_when_ready(page,is_mock=False)
    result = read_result(page,wait=True,exclude_session=previous,is_mock=False)
    if result is None or result['score'] is None:
        print('本次正式试卷已交卷，网站尚未公布成绩。',flush=True)


def diagnose(page):
    """只读当前正式试卷并离线匹配，不导航、不作答或交卷。"""
    require_complete_bank()
    require_formal_paper(page)
    state = page.run_js(PAPER_STATE)
    plans = prepare_answer_plan(state,is_mock=False)
    result = {'question_count':len(state['questions']),'matched':len(plans),
              'remaining_seconds':state['remaining'],
              'already_answered':sum(bool(q.get('answer')) for q in state['questions']),
              'resolved_conflicts':[p['subject_id'] for p in plans if p.get('resolution')]}
    write_json(OUTPUT / 'formal_diagnosis.json',result)
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['status','diagnose','run','result'])
    parser.add_argument('--confirm-promise',action='store_true',help='按用户授权自动勾选并确认已核对的安全承诺')
    args = parser.parse_args()
    page = connect()
    if args.command == 'status':
        status(page)
    elif args.command == 'diagnose':
        diagnose(page)
    elif args.command == 'run':
        run(page,confirm_safety_promise=args.confirm_promise)
    else:
        read_result(page,wait=True,is_mock=False)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError,ValueError,FileNotFoundError) as exc:
        print(f'操作未完成：{exc}',flush=True)
        raise SystemExit(1)
