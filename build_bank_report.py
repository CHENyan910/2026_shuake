"""将已核对题库生成可搜索的本地复习文件。"""

import json
from collections import Counter

from exam_bank import AnswerBank, answer_signature, text_content
from practice_bank import OUTPUT, answer_display, export_summary, write_json


HTML = """<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>实验室安全题库复习</title>
<style>
body{font:16px/1.65 system-ui,sans-serif;color:#172b3a;background:#f4f7fa;margin:0}
main{max-width:1040px;margin:auto;padding:30px 22px}h1{font-size:28px;margin-bottom:6px}
.muted{color:#526575}header,.question{background:white;padding:20px 24px;border-radius:10px;margin-bottom:16px}
.filters{display:flex;gap:12px;flex-wrap:wrap;position:sticky;top:0;background:#f4f7fa;padding:14px 0}
input,select,button{font:inherit;border:1px solid #b6c4cf;border-radius:5px;padding:8px 12px;background:white}
input{flex:1;min-width:220px}button{cursor:pointer;color:#175a80}button:disabled{opacity:.4;cursor:default}
.tag{font-size:13px;color:#526575}.prompt{white-space:pre-wrap;font-weight:600}.option{white-space:pre-wrap;padding:4px 10px}
.correct{border-left:3px solid #18846a;background:#eef9f5}.answer{color:#12664f;font-weight:600;margin-top:12px}
.note{background:#fff5dc;color:#775514;padding:10px 14px;border-radius:5px}
details{margin-top:10px}summary{cursor:pointer}.pager{display:flex;align-items:center;gap:15px;padding:16px 0}
</style><main><header><h1>实验室安全题库复习</h1><p id="stats"></p>
<p class="muted">正确答案来自学校普通练习反馈。分类、题号和全部选项均予保留；没有解析的题目会明确标注。</p>
</header><div class="filters"><input id="search" placeholder="搜索题干或选项"><select id="category"><option value="">全部分类</option></select>
<select id="type"><option value="">全部题型</option><option>判断题</option><option>单选题</option><option>多选题</option></select></div>
<p id="count" class="muted"></p><div id="questions"></div><div class="pager"><button id="prev">上一页</button><span id="page"></span><button id="next">下一页</button></div>
</main><script id="data" type="application/json">__DATA__</script><script>
const data=JSON.parse(document.getElementById('data').textContent);
const byId=id=>document.getElementById(id);let page=0;const size=50;
byId('stats').textContent=`${data.categories} 类 · ${data.questions.length} 道题 · ${data.verified} 个网站确认答案`;
for(const c of [...new Set(data.questions.map(q=>q.category))]){let o=document.createElement('option');o.value=c;o.textContent=c;byId('category').append(o)}
function element(tag,content,cls){const e=document.createElement(tag);e.textContent=content;if(cls)e.className=cls;return e}
function render(){const query=byId('search').value.toLowerCase().trim();const category=byId('category').value;const type=byId('type').value;
const selected=data.questions.filter(q=>(!category||q.category===category)&&(!type||q.type===type)&&(!query||(q.prompt+' '+q.options.map(o=>o.text).join(' ')).toLowerCase().includes(query)));
const pages=Math.max(1,Math.ceil(selected.length/size));page=Math.min(page,pages-1);byId('count').textContent=`匹配 ${selected.length} 道题`;
const container=byId('questions');container.replaceChildren();
for(const q of selected.slice(page*size,(page+1)*size)){const article=element('article','','question');article.append(element('div',`${q.category} · ${q.type} · 网站题号 ${q.id}`,'tag'));article.append(element('p',q.prompt,'prompt'));
for(const o of q.options)article.append(element('div',o.label+'、'+o.text,'option'+(o.correct?' correct':'')));
article.append(element('div','正确答案：'+q.answer,'answer'));
if(q.note)article.append(element('p','复习备注：'+q.note,'note'));
const detail=document.createElement('details');detail.append(element('summary','查看解析'));detail.append(element('p',q.analysis||'网站未提供解析。'));article.append(detail);container.append(article)}
byId('page').textContent=`第 ${page+1} / ${pages} 页`;byId('prev').disabled=page===0;byId('next').disabled=page+1>=pages}
for(const id of ['search','category','type'])byId(id).addEventListener('input',()=>{page=0;render()});
byId('prev').onclick=()=>{page--;render();window.scrollTo(0,0)};byId('next').onclick=()=>{page++;render();window.scrollTo(0,0)};render();
</script></html>
"""


def main():
    summary = export_summary()
    bank = AnswerBank(summary['questions'])
    rows = []
    errors = []
    for record in summary['questions']:
        q, f = record['question'], record['feedback']
        try:
            bank.match(q)
            kind, correct = answer_signature(record)
        except ValueError as exc:
            errors.append({'id':q['id'],'error':str(exc)})
            kind, correct = 'unknown', ()
        options = []
        from exam_bank import normalize
        for i, option in enumerate(q.get('subjectItems') or []):
            options.append({'label':chr(65+i),'text':text_content(option['content']),
                            'correct':kind == 'choices' and normalize(option['content']) in correct})
        rows.append({'category':record['category'],'id':q['id'],
                     'type':{1:'判断题',2:'单选题',3:'多选题'}.get(q['subjectType'],'其他'),
                     'prompt':text_content(q['subjectContent']),'options':options,
                     'answer':answer_display(q,f['rightAnswer']) if f else '尚未核对',
                     'note':summary.get('review_notes',{}).get(str(q['id']),''),
                     'analysis':text_content(f.get('analysis')) if f else ''})
    data = {'categories':summary['category_count'],'verified':summary['verified_answers'],'questions':rows}
    content = json.dumps(data, ensure_ascii=False).replace('<','\\u003c')
    (OUTPUT / '题库复习.html').write_text(HTML.replace('__DATA__',content), encoding='utf-8')
    report = {'category_count':summary['category_count'],'question_count':len(rows),
              'verified_answers':summary['verified_answers'],
              'types':dict(Counter(q['type'] for q in rows)),
              'with_analysis':sum(bool(q['analysis']) for q in rows),
              'with_review_notes':sum(bool(q['note']) for q in rows),
              'categories':summary['categories'],'mapping_errors':errors}
    write_json(OUTPUT / 'validation.json',report)
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
