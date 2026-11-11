"""检查并验证当前任务的模拟考试；不进入正式考试。"""

import argparse
import json
import re
import time
from urllib.parse import urlsplit

from exam_bank import AnswerBank
from practice_bank import OUTPUT, wait_for, write_json
from project_config import CONFIG, STUDY_URL, TASK_ID


TASK_STATE = r"""
const visible = e => e && e.getClientRects().length > 0;
const vms = [...new Set(Array.from(document.querySelectorAll('*')).map(e => e.__vue__).filter(Boolean))];
const root = vms.find(v => v.$store?.state?.examDetail);
const relevant = vms.filter(v => Object.keys(v.$options.methods || {}).some(k => /exam|test|simulat/i.test(k)));
return {
    path:location.pathname,
    text:document.body.innerText.slice(0,25000),
    detail:root?.$store.state.examDetail,
    components:relevant.map(v => ({name:v.$options.name,fields:Object.keys(v.$data || {}),
        methods:Object.fromEntries(Object.entries(v.$options.methods || {})
            .filter(([k]) => /exam|test|simulat/i.test(k)).map(([k,f]) => [k,String(f)]))})),
    dialogs:Array.from(document.querySelectorAll('.ivu-modal,.ivu-message-notice'))
        .filter(visible).map(e=>e.innerText),
    goExam:vms.filter(v=>v.$options.name==='GoExam').map(v=>({
        examId:v.examId,isMockExam:v.isMockExam,flag:v.flag,nextStep:v.nextStep,
        isExam:v.isExam,instructions:v.instructions,
        methods:Object.fromEntries(['openDetail','stepto','confirm','startExam']
            .filter(k=>v.$options.methods[k]).map(k=>[k,String(v.$options.methods[k])]))})),
    buttons:Array.from(document.querySelectorAll('button')).filter(visible)
        .map(e=>({text:e.innerText,disabled:e.disabled})),
    examState: /\/routine-exam\//.test(location.pathname) ? {
        studentExam:root?.$store.state.studentExam,
        studentExamConfig:root?.$store.state.studentExamConfig,
        components:vms.map(v=>({name:v.$options.name,fields:Object.keys(v.$data||{}),
            methods:Object.keys(v.$options.methods||{})})).filter(v=>v.fields.length),
    } : null,
};
"""


def require_complete_bank():
    if not (OUTPUT / 'catalog.json').is_file() or not (OUTPUT / 'question_bank.json').is_file():
        raise RuntimeError('题库尚未准备好，请先使用菜单8整理题库，再使用菜单9生成复习文件。')
    manifest = json.loads((OUTPUT / 'catalog.json').read_text(encoding='utf-8'))
    data = json.loads((OUTPUT / 'question_bank.json').read_text(encoding='utf-8'))
    if data['question_count'] != manifest['question_count'] or data['verified_answers'] != manifest['question_count']:
        raise RuntimeError('尚未收齐全部题库及网站答案，未开始模拟考试。')
    if len(data['categories']) != manifest['category_count'] or not all(c['complete'] for c in data['categories']):
        raise RuntimeError('仍有分类未完成，未开始模拟考试。')
    return data


def open_task(page):
    path = urlsplit(page.url).path
    if re.fullmatch(r'/lab-study-front/questionBank/exercises/\d+/?',path):
        page.ele('text:退出',timeout=3).click()
        wait_for(lambda: urlsplit(page.url).path.rstrip('/') == '/lab-study-front/questionBank',
                 '未正常退出普通练习。')
    page.get(STUDY_URL)
    wait_for(lambda: '模拟考试' in page.run_js('return document.body.innerText'), '任务首页未加载。')


def connect():
    from DrissionPage import Chromium, ChromiumOptions
    browser = Chromium(ChromiumOptions(read_file=False)
                       .set_address(f"127.0.0.1:{CONFIG['browser_port']}").existing_only())
    tabs = browser.get_tabs(url='sysaq.ustc.edu.cn/lab-study-front/')
    if not tabs:
        raise RuntimeError('没有已登录的项目浏览器标签页。')
    page = tabs[0]
    if not re.fullmatch(r'/lab-study-front/(?:person|examTask/\d+|questionBank(?:/exercises/\d+)?|routine-exam/'
                        + re.escape(TASK_ID) + r')/?', urlsplit(page.url).path):
        raise RuntimeError('当前页面不在本任务的模拟验证范围内。')
    return page


def brief(value, depth=0):
    if isinstance(value,list):
        return {'length':len(value),'sample':[brief(v,depth+1) for v in value[:1]]} if depth < 4 else {'length':len(value)}
    if isinstance(value,dict):
        return {k:brief(v,depth+1) for k,v in value.items() if k != 'components'}
    return value


def verify_paper(page, *, is_mock):
    if urlsplit(page.url).path.rstrip('/') != f'/lab-study-front/routine-exam/{TASK_ID}':
        raise RuntimeError('当前不是本任务试卷页。')
    status = page.run_js(r"""
        const vms=[...new Set(Array.from(document.querySelectorAll('*')).map(e=>e.__vue__).filter(Boolean))];
        const vm=vms.find(v=>v.$options.name==='studentExaming');
        if(!vm)throw new Error('未找到试卷组件');
        return new Promise((resolve,reject)=>vm.commonMethods.emitAjax({
            path:'/api/exam_center/current_exam',
            success:e=>resolve({isMockExam:e?.isMockExam,examId:e?.exam?.id,
                requestId:e?.examRequestId,storeRequestId:vm.$store.state.studentExam.examRequestId}),
            error:e=>reject(new Error(e.message||'无法确认模拟状态')),
        }));
    """)
    mode = 1 if is_mock else 0
    label = '模拟' if is_mock else '正式'
    if (status.get('isMockExam') != mode or str(status.get('examId')) != TASK_ID
            or not status.get('requestId') or status['requestId'] != status['storeRequestId']):
        raise RuntimeError(f'网站未确认当前试卷为本任务的{label}考试，未作答或交卷。')
    return status


def verify_mock(page):
    return verify_paper(page,is_mock=True)


def current_exam(page):
    """在导航前检查账号当前考试，避免离开正在进行的正式考试。"""
    return page.run_js(r"""
        const vm=[...new Set(Array.from(document.querySelectorAll('*')).map(e=>e.__vue__).filter(Boolean))]
            .find(v=>v.commonMethods?.emitAjax);
        if(!vm)throw new Error('页面尚未登录或未加载完成，请先使用菜单1登录');
        return new Promise((resolve,reject)=>vm.commonMethods.emitAjax({
            path:'/api/exam_center/current_exam',
            success:e=>resolve(e ? {is_mock:e.isMockExam,exam_id:e.exam?.id,
                request_id:e.examRequestId,next_step:e.nextStep} : null),
            error:e=>reject(new Error(e.message||'无法读取当前考试状态')),
        }));
    """)


def validate_active_exam(active):
    if active and (active.get('is_mock') not in (True,1) or str(active.get('exam_id')) != TASK_ID):
        raise RuntimeError('当前账号有正式考试或其他任务的考试正在进行，未执行模拟操作。')


LAUNCH_STATE = r"""
const visible=e=>!!e&&e.getClientRects().length>0;
const vms=[...new Set(Array.from(document.querySelectorAll('*')).map(e=>e.__vue__).filter(Boolean))];
const dialogs=Array.from(document.querySelectorAll('.ivu-modal')).filter(visible);
return {
    path:location.pathname,
    paper_ready:vms.some(v=>v.$options.name==='studentExaming'&&v.subjectDataList?.length),
    mock_owner:vms.some(v=>v.$options.name==='GoExam'&&Number(v.isMockExam)===1&&String(v.examId)===arguments[0]),
    dialogs:dialogs.map(e=>e.innerText),
    mock_confirmation:dialogs.some(e=>Array.from(e.querySelectorAll('button'))
        .some(b=>visible(b)&&b.innerText.trim()==='模拟考试'&&!b.disabled)),
    home_button:Array.from(document.querySelectorAll('button')).some(e=>visible(e)&&e.innerText.trim()==='模拟考试'),
    messages:Array.from(document.querySelectorAll('.ivu-message-notice,.ivu-notice-notice'))
        .filter(visible).map(e=>e.innerText),
};
"""


def launch_stage(state):
    path = state['path'].rstrip('/')
    if path == f'/lab-study-front/routine-exam/{TASK_ID}':
        return 'paper' if state['paper_ready'] else 'loading_paper'
    if path != f'/lab-study-front/examTask/{TASK_ID}':
        raise RuntimeError('页面已离开当前任务，请先使用菜单1打开任务首页并登录。')
    text = '\n'.join(state['dialogs'])
    if state['dialogs']:
        if not state['mock_owner']:
            raise RuntimeError('当前弹窗未确认是模拟考试，已停止。')
        if '您已完成排队' in text:
            return 'queue_ready'
        if '排队等待' in text or '排在第' in text:
            return 'queue'
        if '试卷生成中' in text:
            return 'generating'
        if state['mock_confirmation']:
            return 'confirm'
        raise RuntimeError('模拟考试出现需要处理的提示：' + text)
    if state['home_button']:
        return 'home'
    return 'loading'


def start_mock(page):
    button = page.ele('xpath://*[normalize-space(.)="模拟考试" and not(*[normalize-space(.)="模拟考试"])]',timeout=3)
    if not button:
        raise RuntimeError('未找到明确标注的模拟考试入口。')
    button.click(by_js=True)


def begin_mock(page):
    page.run_js(r"""
        const visible=e=>e.getClientRects().length>0;
        const vms=[...new Set(Array.from(document.querySelectorAll('*')).map(e=>e.__vue__).filter(Boolean))];
        if(!vms.some(v=>v.$options.name==='GoExam'&&Number(v.isMockExam)===1&&String(v.examId)===arguments[0]))
            throw new Error('未确认本任务的模拟模式');
        const modal=Array.from(document.querySelectorAll('.ivu-modal')).find(e=>visible(e)&&e.innerText.includes('模拟考试'));
        const button=modal&&Array.from(modal.querySelectorAll('button')).find(e=>e.innerText.trim()==='模拟考试'&&!e.disabled);
        if(!button)throw new Error('未找到模拟考试确认按钮');
        button.click();
    """,TASK_ID)


def enter_mock(page):
    page.run_js(r"""
        const vms=[...new Set(Array.from(document.querySelectorAll('*')).map(e=>e.__vue__).filter(Boolean))];
        if(!vms.some(v=>v.$options.name==='GoExam'&&Number(v.isMockExam)===1&&String(v.examId)===arguments[0]))
            throw new Error('未确认本任务的模拟模式');
        const modal=Array.from(document.querySelectorAll('.ivu-modal'))
            .find(e=>e.getClientRects().length&&e.innerText.includes('您已完成排队'));
        const button=modal&&Array.from(modal.querySelectorAll('button'))
            .find(e=>e.innerText.trim()==='进入考试'&&!e.disabled);
        if(!button)throw new Error('未找到排队完成后的进入考试按钮');
        button.click();
    """,TASK_ID)


def launch_mock(page, active):
    validate_active_exam(active)
    path = urlsplit(page.url).path.rstrip('/')
    if active and active.get('next_step') == 5 and path != f'/lab-study-front/routine-exam/{TASK_ID}':
        from urllib.parse import urlencode
        if not active.get('request_id'):
            raise RuntimeError('未取得未完成模拟考试的编号。')
        print('检测到未完成的模拟考试，继续原试卷。',flush=True)
        page.get(f'https://sysaq.ustc.edu.cn/lab-study-front/routine-exam/{TASK_ID}?'
                 + urlencode({'examRequestId':active['request_id']}))
    elif path not in (f'/lab-study-front/examTask/{TASK_ID}',f'/lab-study-front/routine-exam/{TASK_ID}'):
        open_task(page)
    deadline = time.monotonic() + 30 * 60
    last_stage, last_report = None, 0
    while time.monotonic() < deadline:
        state = page.run_js(LAUNCH_STATE,TASK_ID)
        stage = launch_stage(state)
        now = time.monotonic()
        if stage != last_stage or now-last_report >= 30:
            labels = {'home':'打开模拟考试说明','confirm':'确认开始模拟考试',
                      'queue':'网站正在排队，请保留浏览器窗口','queue_ready':'排队完成，进入模拟考试',
                      'generating':'网站正在生成模拟试卷','loading':'等待任务页面加载',
                      'loading_paper':'等待模拟试卷加载','paper':'模拟试卷已就绪'}
            print(labels[stage],flush=True)
            last_stage, last_report = stage, now
        if stage == 'paper':
            verify_mock(page)
            return
        actions = {'home':start_mock,'confirm':begin_mock,'queue_ready':enter_mock}
        if stage in actions:
            actions[stage](page)
            wait_for(lambda: launch_stage(page.run_js(LAUNCH_STATE,TASK_ID)) != stage,
                     '模拟考试操作后页面未更新：' + '\n'.join(state['messages']),timeout=45)
        else:
            time.sleep(1)
    raise RuntimeError('网站排队或组卷等待超时，已保留当前页面，可稍后再次选择自动模拟考试。')


def run_mock(page):
    require_complete_bank()
    active = current_exam(page)
    validate_active_exam(active)
    old_session = None
    old_result = OUTPUT / 'mock_result.json'
    if old_result.exists():
        old_session = json.loads(old_result.read_text(encoding='utf-8')).get('session_id')
    launch_mock(page,active)
    fill_paper(page)
    submit_when_ready(page)
    result = read_result(page,wait=True,exclude_session=old_session)
    if result is None or result['score'] is None:
        print('模拟已交卷，网站尚未公布成绩。稍后可选择菜单11查询。',flush=True)
    else:
        print('自动模拟考试流程已完成。',flush=True)


def probe_paper(page):
    status = verify_mock(page)
    result = page.run_js(r"""
        const vms=[...new Set(Array.from(document.querySelectorAll('*')).map(e=>e.__vue__).filter(Boolean))];
        const vm=vms.find(v=>v.$options.name==='studentExaming');
        const item=vms.find(v=>v.$options.name==='m-item');
        return {
            fields:Object.keys(vm.$data),computed:Object.keys(vm.$options.computed||{}),
            methods:Object.fromEntries(['getData','save','savePaper','getAnswers','startComputedTime','setPageData',
                'set_Time','isTimeOut','clickDiv','handercontinue']
                .map(k=>[k,String(vm.$options.methods[k])])),
            itemMethods:Object.fromEntries(Object.entries(item.$options.methods).map(([k,v])=>[k,String(v)])),
            itemProps:item.$props,itemData:item.$data,itemHtml:item.$el.outerHTML,
            radioProps:vms.filter(v=>v.$options.name==='Radio').slice(0,2).map(v=>v.$props),
            data:{total:vm.total,time:vm.time,timeOut:vm.timeOut,subjectIndex:vm.subjectIndex,
                isSubmit:vm.isSubmit,submitted:vm.submitted,subjectDataList:vm.subjectDataList,
                subjectListSource:vm.subjectListSource},
        };
    """)
    write_json(OUTPUT / 'mock_paper_probe.json',result)
    print(json.dumps({'verified_mock':status['isMockExam'],
                     **{k:v for k,v in result.items() if k != 'data'}},ensure_ascii=False,indent=2))


PAPER_STATE = r"""
const vm=[...new Set(Array.from(document.querySelectorAll('*')).map(e=>e.__vue__).filter(Boolean))]
    .find(v=>v.$options.name==='studentExaming');
if(!vm)throw new Error('当前没有试卷');
return {questions:vm.subjectDataList,remaining:vm.time,isSubmit:vm.isSubmit,
    unanswered:vm.noAnserwer,submitted:vm.submitted,
    requestId:vm.examRequestId,config:{minSubmitTime:vm.examConfig.minSubmitTime,
        subjectTimeLength:vm.examConfig.subjectTimeLength}};
"""


def expected_answer(plan):
    if plan['type'] == 1:
        return '1' if plan['answer'] else '0'
    return ','.join(str(v) for v in sorted(plan['answer']))


FORMAL_ANSWER_INTERVAL = 15


def wait_answer_interval(last_saved, interval):
    """按保存成功的时刻计算下一题间隔；单次等待不超过30秒。"""
    if last_saved is None or not interval:
        return
    while (remaining := interval-(time.monotonic()-last_saved)) > 0:
        time.sleep(min(remaining,30))


def prepare_answer_plan(state, *, is_mock=True):
    """作答前核对整份试卷，失败时也保存快照和全部问题，便于恢复排查。"""
    prefix = 'mock' if is_mock else 'formal'
    write_json(OUTPUT / f'{prefix}_paper.json',state)
    bank = AnswerBank.load(OUTPUT / 'question_bank.json')
    plans,missing = [],[]
    for question in state['questions']:
        try:
            plans.append(bank.match(question))
        except ValueError as exc:
            missing.append({'subject_id':question['id'],'error':str(exc)})
    interval = 0 if is_mock else FORMAL_ANSWER_INTERVAL
    write_json(OUTPUT / f'{prefix}_answer_plan.json',{'matched':plans,'missing':missing,
               'request_id':state['requestId'],'interval_seconds':interval})
    if missing:
        raise ValueError(f"{missing[0]['error']}；共{len(missing)}题需处理，尚未开始本次填写。"
                         f"详情见 exam_prep/{prefix}_answer_plan.json。")
    for plan in plans:
        if plan.get('resolution'):
            print(f"题目 {plan['subject_id']} 使用已核对的冲突规则，来源题号："
                  f"{plan['resolution']['selected_bank_id']}；依据已写入答题计划。",flush=True)
    return plans


def fill_paper(page, *, is_mock=True):
    prefix,label = ('mock','模拟') if is_mock else ('formal','正式')
    require_complete_bank()
    verify_mock(page) if is_mock else verify_paper(page,is_mock=False)
    state = page.run_js(PAPER_STATE)
    plans = prepare_answer_plan(state,is_mock=is_mock)
    interval = 0 if is_mock else FORMAL_ANSWER_INTERVAL
    saved_answers = {q['id']:q.get('answer') for q in state['questions']}
    pending = [p for p in plans if str((saved_answers[p['subject_id']] or {}).get('answer')) != expected_answer(p)]
    already_saved = len(plans)-len(pending)
    last_saved = None
    if not is_mock:
        last_saved = time.monotonic() if any(saved_answers.values()) else None
        gaps = max(0,len(pending)-1) + int(bool(pending and last_saved is not None))
        if pending and state['remaining'] <= gaps*interval + max(30,len(pending)):
            raise RuntimeError(f'剩余考试时间不足以按每题间隔{interval}秒完成未答题，未继续作答。')
        print(f'正式考试逐题保存，题间间隔{interval}秒；已保存 {already_saved} 题，待处理 {len(pending)} 题。',flush=True)
    size = 3 if is_mock else 1
    for start in range(0,len(pending),size):
        batch = pending[start:start+size]
        wait_answer_interval(last_saved,interval)
        page.run_js(r"""
            const plans=JSON.parse(arguments[0]);
            const vms=[...new Set(Array.from(document.querySelectorAll('.m-item')).map(e=>e.__vue__).filter(Boolean))];
            for(const plan of plans){
                const item=vms.find(v=>v.item?.id===plan.subject_id);
                if(!item)throw new Error('未显示指定题目：'+plan.subject_id);
                const desired=plan.type===1?(plan.answer?'1':'0'):[...plan.answer].sort((a,b)=>a-b).join(',');
                if(String(item.item.answer?.answer)===desired)continue;
                const selector=plan.type===3?'.ivu-checkbox-wrapper':'.ivu-radio-wrapper';
                const labels=Array.from(item.$el.querySelectorAll(selector));
                if(plan.type===1){if(labels.length!==2)throw new Error('判断选项结构变化');labels[plan.answer?0:1].click()}
                else {
                    const options=item.item.subjectItems;
                    if(labels.length!==options.length)throw new Error('选项结构变化');
                    for(let i=0;i<options.length;i++){
                        const selected=plan.answer.some(id=>String(id)===String(options[i].id));
                        if(plan.type===3){
                            if(labels[i].classList.contains('ivu-checkbox-wrapper-checked')!==selected)labels[i].click();
                        }else if(selected)labels[i].click();
                    }
                }
            }
        """,json.dumps(batch))
        def saved():
            current = page.run_js(PAPER_STATE)
            answers = {q['id']:q.get('answer') for q in current['questions']}
            return all(str((answers[p['subject_id']] or {}).get('answer')) == expected_answer(p) for p in batch)
        wait_for(saved, f'{label}答案保存超时：{[p["subject_id"] for p in batch]}',timeout=30)
        last_saved = time.monotonic()
        if not is_mock:
            write_json(OUTPUT / 'formal_answer_pacing.json',{
                'request_id':state['requestId'],'subject_id':batch[-1]['subject_id'],
                'interval_seconds':interval,'saved_at':time.time(),
                'saved_count':already_saved+start+len(batch),'question_count':len(plans),
            })
        print(f'{label}答案已保存：{already_saved+start+len(batch)}/{len(plans)}',flush=True)
    # 使用网站原有的数据加载流程，重新读取服务器保存结果。
    page.listen.start('/api/exam_center/do_exam')
    page.run_js(r"""
        const vm=[...new Set(Array.from(document.querySelectorAll('*')).map(e=>e.__vue__).filter(Boolean))]
            .find(v=>v.$options.name==='studentExaming');
        vm.getData();
    """)
    response = page.listen.wait(timeout=20)
    if not response:
        raise RuntimeError('未收到重新读取试卷的服务器响应。')
    body = response.response.body
    write_json(OUTPUT / f'{prefix}_saved_response.json',body)
    node = body
    server_questions = None
    for _ in range(6):
        if isinstance(node,dict) and isinstance(node.get('paper'),dict):
            server_questions = node['paper'].get('subjects')
            break
        node = node.get('data') if isinstance(node,dict) else None
    if not isinstance(server_questions,list):
        raise RuntimeError('重新读取的试卷响应格式不符合预期。')
    answers = {q['id']:q.get('answer') for q in server_questions}
    wrong = [p['subject_id'] for p in plans
             if str((answers.get(p['subject_id']) or {}).get('answer')) != expected_answer(p)]
    result = {'question_count':len(plans),'server_saved':len(plans)-len(wrong),
              'mismatches':wrong,'is_mock':is_mock,'request_id':state['requestId']}
    write_json(OUTPUT / f'{prefix}_answer_verification.json',result)
    if wrong:
        raise RuntimeError(f'服务器保存结果与计划不一致：{wrong}')
    print(f"服务器核对完成：{result['server_saved']}/{result['question_count']} 道答案已保存。",flush=True)


def submit_when_ready(page, *, is_mock=True):
    prefix,label = ('mock','模拟') if is_mock else ('formal','正式')
    require_complete_bank()
    verify_mock(page) if is_mock else verify_paper(page,is_mock=False)
    plan = json.loads((OUTPUT / f'{prefix}_answer_plan.json').read_text(encoding='utf-8'))['matched']
    deadline = time.monotonic() + 30 * 60
    while True:
        state = page.run_js(PAPER_STATE)
        answers = {q['id']:q.get('answer') for q in state['questions']}
        if (len(answers) != len(plan) or state['unanswered']
                or any(str((answers.get(p['subject_id']) or {}).get('answer')) != expected_answer(p) for p in plan)):
            raise RuntimeError('当前试卷有未保存或与核对计划不一致的答案，未交卷。')
        progress = {'remaining_seconds':state['remaining'],'can_submit':state['isSubmit'],
                    'unanswered':state['unanswered'],'is_mock':is_mock,
                    'observed_at':time.strftime('%Y-%m-%d %H:%M:%S')}
        write_json(OUTPUT / f'{prefix}_progress.json',progress)
        remaining = max(0,int(state['config']['minSubmitTime'])*60
                        - (int(state['config']['subjectTimeLength'])*60-int(state['remaining'])))
        if state['isSubmit']:
            print(f'网站已允许交卷，正在提交{label}试卷。',flush=True)
        else:
            print(f'答案已核对保存，距最早交卷约 {remaining//60} 分 {remaining%60:02d} 秒。',flush=True)
        if state['isSubmit']:
            break
        if time.monotonic() >= deadline:
            raise RuntimeError('等候正常交卷时间超时；答案保留在网站，未绕过交卷限制。')
        time.sleep(30)
    verify_mock(page) if is_mock else verify_paper(page,is_mock=False)
    page.run_js(r"""
        const vm=[...new Set(Array.from(document.querySelectorAll('*')).map(e=>e.__vue__).filter(Boolean))]
            .find(v=>v.$options.name==='studentExaming');
        if(!vm.isSubmit||vm.noAnserwer)throw new Error('尚不满足正常交卷条件');
        vm.save();
    """)
    def confirmation():
        return page.run_js(r"""
            return Array.from(document.querySelectorAll('.ivu-modal'))
                .some(e=>e.getClientRects().length&&e.innerText.includes('是否确定交卷'));
        """)
    wait_for(confirmation,'没有出现交卷确认框。')
    page.listen.start('/api/exam_center/post_paper',method='POST')
    page.run_js(r"""
        const modal=Array.from(document.querySelectorAll('.ivu-modal'))
            .find(e=>e.getClientRects().length&&e.innerText.includes('是否确定交卷'));
        const button=modal&&Array.from(modal.querySelectorAll('button'))
            .find(e=>['确定','确认'].includes(e.innerText.trim())&&!e.disabled);
        if(!button)throw new Error('未找到交卷确认按钮');
        button.click();
    """)
    response = page.listen.wait(timeout=30)
    if not response:
        raise RuntimeError('未收到交卷响应，请保留页面检查，未重复提交。')
    write_json(OUTPUT / f'{prefix}_submit_response.json',response.response.body)
    wait_for(lambda: urlsplit(page.url).path.rstrip('/') == '/lab-study-front/person',
             '交卷后未进入个人中心，请检查服务器响应。')
    page.wait(1)
    result = page.run_js(r"""
        const vms=[...new Set(Array.from(document.querySelectorAll('*')).map(e=>e.__vue__).filter(Boolean))];
        return {path:location.pathname,text:document.body.innerText,
            components:vms.filter(v=>Object.keys(v.$data||{}).some(k=>/exam|score|result|list|record/i.test(k)))
                .map(v=>({name:v.$options.name,fields:Object.keys(v.$data),methods:Object.keys(v.$options.methods||{})})),
            buttons:Array.from(document.querySelectorAll('button,.ivu-tabs-tab')).map(e=>e.innerText)};
    """)
    write_json(OUTPUT / f'{prefix}_result_page.json',result)
    print(f'{label}交卷成功，正在读取个人中心成绩。',flush=True)


def result_record(rows, exclude_session=None, *, is_mock=True):
    records = [r for r in rows or [] if str(r.get('examId')) == TASK_ID
               and r.get('isMockExam') == (1 if is_mock else 0)
               and (exclude_session is None or str(r.get('sessionId')) != str(exclude_session))]
    return max(records,key=lambda r:r.get('endDate') or 0) if records else None


def fetch_results(page):
    return page.run_js(r"""
        const vm=[...new Set(Array.from(document.querySelectorAll('*')).map(e=>e.__vue__).filter(Boolean))]
            .find(v=>Array.isArray(v.$data?.dataList)&&'currentTestPaperSubjects' in v.$data);
        if(!vm)throw new Error('请在个人中心打开“我的成绩”');
        return new Promise((resolve,reject)=>vm.commonMethods.emitAjax({
            path:'/api/student_result/results',
            data:vm.commonMethods.connectObject({}, {...vm.formData,page:0}),
            success:r=>{vm.formData.page=1;vm.dataList=r.content||[];vm.totalElements=r.totalElements||0;resolve({rows:vm.dataList})},
            error:e=>reject(new Error(e.message||'读取成绩失败')),
        }));
    """)


def open_results(page):
    if current_exam(page):
        raise RuntimeError('当前账号仍有考试正在进行，已保留页面；模拟考试可选择菜单10继续。')
    path = urlsplit(page.url).path
    if re.fullmatch(r'/lab-study-front/questionBank/exercises/\d+/?',path):
        page.ele('text:退出',timeout=3).click()
        wait_for(lambda: urlsplit(page.url).path.rstrip('/') == '/lab-study-front/questionBank',
                 '未正常退出普通练习。')
    if urlsplit(page.url).path.rstrip('/') != '/lab-study-front/person':
        page.get('https://sysaq.ustc.edu.cn/lab-study-front/person')
    tab = page.ele('xpath://*[normalize-space(.)="我的成绩" and not(*[normalize-space(.)="我的成绩"])]',timeout=5)
    if not tab:
        raise RuntimeError('未找到个人中心的“我的成绩”入口，请检查登录状态。')
    tab.click(by_js=True)
    wait_for(lambda: page.run_js(r"""
        return Array.from(document.querySelectorAll('*')).some(e=>
            Array.isArray(e.__vue__?.$data?.dataList)&&'currentTestPaperSubjects' in e.__vue__.$data);
    """),'个人中心成绩列表未加载。')


def read_result(page, wait=False, exclude_session=None, *, is_mock=True):
    prefix,label = ('mock','模拟') if is_mock else ('formal','正式')
    if urlsplit(page.url).path.rstrip('/') != '/lab-study-front/person':
        raise RuntimeError('当前不是交卷后的个人中心。')
    result = page.run_js(r"""
        const vms=[...new Set(Array.from(document.querySelectorAll('*')).map(e=>e.__vue__).filter(Boolean))];
        const vm=vms.find(v=>Array.isArray(v.$data?.dataList)&&'currentTestPaperSubjects' in v.$data);
        return {rows:vm?.dataList,methods:vm?Object.fromEntries(['getTestPaper','getData']
            .map(k=>[k,String(vm.$options.methods[k])])):{},text:document.body.innerText};
    """)
    write_json(OUTPUT / f'{prefix}_results_probe.json',result)
    result = fetch_results(page)
    deadline = time.monotonic() + (10 * 60 if wait else 0)
    while True:
        row = result_record(result.get('rows'),exclude_session,is_mock=is_mock)
        report = None
        if row:
            report = {'exam_id':row['examId'],'session_id':row['sessionId'],
                      'exam_name':row['examName'],'is_mock':is_mock,'score':row.get('score'),
                      'result_status':row.get('resultStatus'),'duration_seconds':row.get('totalTime'),
                      'begin_timestamp':row.get('beginDate'),'end_timestamp':row.get('endDate')}
            write_json(OUTPUT / f'{prefix}_result.json',report)
            if report['score'] is None:
                print(f'{label}已交卷，网站暂未出分。' + ('30秒后重新查询。' if wait else ''),flush=True)
            else:
                print(f"网站{label}成绩：{report['score']} 分。",flush=True)
        else:
            print(f'等待网站生成本次{label}成绩记录。',flush=True)
        if (row and row.get('score') is not None) or time.monotonic() >= deadline:
            if not row and not wait:
                raise RuntimeError(f'未找到本任务的{label}考试成绩记录。')
            return report
        time.sleep(30)
        result = fetch_results(page)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['run','recent','cached','inspect','start','begin','enter','probe','fill','submit','result'])
    parser.add_argument('--wait-result',action='store_true',help='在个人中心等待网站公布模拟成绩，最多10分钟')
    args = parser.parse_args()
    page = connect()
    if args.command == 'run':
        run_mock(page)
        return
    if args.command == 'recent':
        open_results(page)
        read_result(page,wait=True)
        return
    if args.command == 'probe':
        probe_paper(page)
        return
    if args.command == 'fill':
        fill_paper(page)
        return
    if args.command == 'submit':
        submit_when_ready(page)
        return
    if args.command == 'result':
        read_result(page,wait=args.wait_result)
        return
    if args.command in ('inspect','start'):
        require_complete_bank()
        open_task(page)
    if args.command == 'start':
        start_mock(page)
        page.wait(1.5)
    if args.command == 'begin':
        require_complete_bank()
        begin_mock(page)
        page.wait(2)
    if args.command == 'enter':
        require_complete_bank()
        enter_mock(page)
        page.wait(2)
    result = page.run_js(TASK_STATE)
    write_json(OUTPUT / 'mock_task_snapshot.json',result)
    print(json.dumps(brief({k:v for k,v in result.items() if k in ('path','dialogs','buttons','examState')}),
                     ensure_ascii=False,indent=2))


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError,ValueError,FileNotFoundError) as exc:
        print(f'操作未完成：{exc}',flush=True)
        raise SystemExit(1)
