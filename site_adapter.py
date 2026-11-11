"""适配当前 USTC 学习页面；通过页面按钮进入课程，由网站自行计时及上传。"""

from dataclasses import dataclass
import re
import time
from urllib.parse import urlsplit

from project_config import TASK_ID, STUDY_URL


def seconds(value):
    """解析页面中的 MM:SS / HH:MM:SS；缺失值不能作为零处理。"""
    text = str(value).strip()
    if not re.fullmatch(r"\d+:\d{2}(?::\d{2})?", text):
        raise ValueError(f"无法解析页面时长：{text!r}")
    parts = [int(v) for v in text.split(":")]
    if parts[-1] >= 60 or (len(parts) == 3 and parts[-2] >= 60):
        raise ValueError(f"页面时长超出范围：{text!r}")
    return sum(n * 60 ** i for i, n in enumerate(reversed(parts)))


def clock(value):
    value = max(0, int(value))
    return f"{value // 3600:02d}:{value // 60 % 60:02d}:{value % 60:02d}"


@dataclass(frozen=True)
class Requirements:
    must_done: int
    must_required: int
    elective_done: int
    elective_required: int

    @property
    def complete(self):
        return self.must_done >= self.must_required and self.elective_done >= self.elective_required

    def needed(self, must):
        return (self.must_done < self.must_required) if must else (self.elective_done < self.elective_required)

    @classmethod
    def from_cards(cls, cards):
        result = {}
        for card in cards:
            label = card["label"].strip()
            if label in ("必学时长", "选学时长"):
                result[label] = (seconds(card["done"]), seconds(card["required"].lstrip("/ ")))
        if set(result) != {"必学时长", "选学时长"}:
            raise RuntimeError("未找到必学/选学两项时长，请确认已登录且当前显示任务首页。")
        return cls(*result["必学时长"], *result["选学时长"])

    def describe(self):
        return (f"必学 {clock(self.must_done)} / {clock(self.must_required)}；"
                f"选学 {clock(self.elective_done)} / {clock(self.elective_required)}")


@dataclass(frozen=True)
class Course:
    title: str
    module: str
    must: bool
    done: int
    required: int
    page_number: int
    row_index: int
    action: str
    disabled: bool

    @property
    def key(self):
        return self.title, self.module, self.must

    @property
    def remaining(self):
        return max(0, self.required - self.done)

    @classmethod
    def from_row(cls, row, page_number):
        values = re.findall(r"\d+:\d{2}(?::\d{2})?", row["time_text"])
        if len(values) != 2:
            raise RuntimeError(f"课程“{row['title']}”的学习进度格式发生变化：{row['time_text']}")
        return cls(row["title"], row["module"], row["must"], seconds(values[0]), seconds(values[1]),
                   page_number, row["index"], row["action"], row["disabled"])


def candidates(courses, requirements, completed=()):
    completed = set(completed)
    return sorted((c for c in courses if c.key not in completed and c.remaining > 0
                   and requirements.needed(c.must) and c.action == "去学习" and not c.disabled),
                  key=lambda c: not c.must)


TASK_SNAPSHOT = """
const visible = e => !!e && e.getClientRects().length > 0;
const text = e => e?.textContent?.trim() || '';
const rows = Array.from(document.querySelectorAll('.ivu-table-body tr.ivu-table-row'));
const page = document.querySelector('.ivu-page-item-active');
const next = document.querySelector('.ivu-page-next');
return {
    path: location.pathname,
    busy: Array.from(document.querySelectorAll('.ivu-spin-fix')).some(visible),
    page_number: Number(text(page)) || 1,
    total_text: text(document.querySelector('.ivu-page-total')),
    has_next: !!next && !next.classList.contains('ivu-page-disabled'),
    cards: Array.from(document.querySelectorAll('.m-center')).filter(e => e.querySelector('.risk-name'))
        .map(e => ({label: text(e.querySelector('.risk-name')), done: text(e.querySelector('.text-1')),
                    required: text(e.querySelector('.text-2'))})),
    rows: rows.map((row, index) => {
        const cells = row.querySelectorAll('td');
        const button = Array.from(row.querySelectorAll('button')).find(e => text(e) === '去学习');
        return {index, title: text(row.querySelector('.titBox .ppp')),
            module: text(cells[1]), time_text: text(cells[2]),
            must: visible(row.querySelector('.must')), action: text(button), disabled: !button || button.disabled};
    }).filter(row => row.title),
};
"""


class Site:
    def __init__(self, page, timeout=30):
        self.page = page
        self.timeout = timeout
        self.in_course = False
        self.saved_during_course = False

    def snapshot(self):
        return self.page.run_js(TASK_SNAPSHOT)

    def wait_for(self, callback, message, timeout=None):
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        while time.monotonic() < deadline:
            result = callback()
            if result:
                return result
            time.sleep(0.25)
        raise RuntimeError(message)

    def wait_task(self, number=None):
        pending = "等待课程列表"
        def ready():
            nonlocal pending
            state = self.snapshot()
            if state["path"].rstrip("/") != f"/lab-study-front/examTask/{TASK_ID}":
                pending = f"尚未回到任务首页，当前路径：{state['path']}"
                return False
            if state["busy"]:
                pending = "任务首页的加载遮罩尚未消失"
                return False
            if not state["cards"]:
                pending = "任务首页尚未显示必学/选学时长"
                return False
            if number is not None and state["page_number"] != number:
                pending = f"等待第 {number} 页，当前第 {state['page_number']} 页"
                return False
            # 空表格须等到分页总数或明确的“暂无数据”提示出现。
            if not state["rows"] and not self.page.ele('css:.ivu-table-tip', timeout=0.1):
                pending = "尚未显示课程条目或空列表提示"
                return False
            Requirements.from_cards(state["cards"])
            return state
        try:
            return self.wait_for(ready, "课程表格加载超时。")
        except RuntimeError as exc:
            if str(exc) != "课程表格加载超时。":
                raise
            raise RuntimeError(f"课程表格加载超时：{pending}。") from exc

    def open_task(self):
        self.page.get(STUDY_URL)
        self.in_course = False
        state = self.wait_task()
        tabs = self.page.eles('css:.ivu-tabs-tab', timeout=1)
        for tab in tabs:
            if tab.text.strip() == "未学":
                if "ivu-tabs-tab-active" not in tab.attr("class"):
                    tab.click(by_js=True)
                    state = self.wait_task(1)
                break
        return state

    def next_page(self, old):
        button = self.page.ele('css:.ivu-page-next:not(.ivu-page-disabled)', timeout=1)
        if not button:
            raise RuntimeError("未找到下一页按钮，分页结构可能已变化。")
        old_titles = [r["title"] for r in old["rows"]]
        button.click(by_js=True)
        def changed():
            state = self.snapshot()
            if (not state["busy"] and state["page_number"] == old["page_number"] + 1
                    and [r["title"] for r in state["rows"]] != old_titles):
                return state
            return False
        return self.wait_for(changed, "翻页后课程数据没有更新，请检查网络。")

    def collect_courses(self, state=None):
        state = state or self.open_task()
        requirements = Requirements.from_cards(state["cards"])
        courses = []
        visited = set()
        while True:
            number = state["page_number"]
            if number in visited:
                raise RuntimeError("分页重复，已停止读取以避免遗漏或重复课程。")
            visited.add(number)
            courses.extend(Course.from_row(row, number) for row in state["rows"])
            if not state["has_next"]:
                break
            state = self.next_page(state)
        total_match = re.search(r"\d+", state["total_text"])
        if total_match and int(total_match[0]) != len(courses):
            raise RuntimeError(f"课程总数在读取期间发生变化：页面 {total_match[0]}，已读取 {len(courses)}。请重试。")
        return requirements, courses

    def open_course(self, course):
        state = self.open_task()
        while state["page_number"] < course.page_number:
            if not state["has_next"]:
                break
            state = self.next_page(state)
        match = next((r for r in state["rows"] if
                      r["title"] == course.title and r["module"] == course.module and r["must"] == course.must), None)
        if match is None:
            raise RuntimeError("课程位置已变化，请重新扫描课程列表后再试。")
        rows = self.page.eles('css:.ivu-table-body tr.ivu-table-row')
        buttons = rows[match["index"]].eles('tag:button')
        button = next((b for b in buttons if b.text.strip() == "去学习" and b.states.is_enabled), None)
        if not button:
            raise RuntimeError(f"课程“{course.title}”没有可用的“去学习”按钮。")
        self.page.listen.start(["/api/study/", "/api/study_upload/"], method="POST")
        self.saved_during_course = False
        button.click(by_js=True)
        self.in_course = True
        self.wait_for(lambda: urlsplit(self.page.url).path.rstrip("/") != f"/lab-study-front/examTask/{TASK_ID}",
                      "点击“去学习”后没有进入课程，请检查页面提示。")

    def detail(self):
        return self.page.run_js("""
            const text = s => document.querySelector(s)?.textContent?.trim();
            const visible = e => e.getClientRects().length > 0;
            return {done: text('.alredyTime'), required: text('.allTime'),
                path: location.pathname,
                messages: Array.from(document.querySelectorAll('.ivu-message-notice-content'))
                    .filter(visible).map(e => e.innerText),
                has_video: !!document.querySelector('video'),
            };
        """)

    def play_video(self):
        """让真实播放器正常播放，不修改播放进度、速度或网站计时器。"""
        return self.page.run_js("""
            const video = document.querySelector('video');
            if (!video) return 'loading';
            if (video.error) return 'error';
            if (!video.paused && !video.ended) return 'playing';
            // 阿里云播放器会包装 play()，有时不返回 Promise。
            try {
                return Promise.resolve(video.play()).then(() => 'playing').catch(() => 'blocked');
            } catch (error) {
                return 'blocked';
            }
        """)

    def continue_prompt(self):
        for modal in self.page.eles('css:.ivu-modal', timeout=0.1):
            if not modal.states.is_displayed:
                continue
            text = modal.text
            if "秒后自动退出" in text and "是否继续" in text:
                button = next((b for b in modal.eles('tag:button') if b.text.strip() == "确定"), None)
                if not button:
                    raise RuntimeError("继续学习提示已出现，但未找到确定按钮。")
                button.click(by_js=True)
                return True
        return False

    def packets(self):
        result = []
        while True:
            packet = self.page.listen.wait(timeout=0.05, raise_err=False)
            if not packet:
                return result
            body = packet.response.body
            if packet.response.status != 200 or not isinstance(body, dict):
                raise RuntimeError("网站开始学习或保存进度请求失败，请检查网络并重新打开课程。")
            code = body.get("code")
            data = body.get("data")
            if code not in (None, 0, 200, "0", "200") or (isinstance(data, dict) and data.get("code") == 400):
                raise RuntimeError(f"网站拒绝开始学习或保存进度（代码 {code}），请检查页面提示。")
            if "/api/study_upload/" in urlsplit(packet.url).path:
                if not data:
                    raise RuntimeError("网站未确认进度保存成功，请重新打开课程检查。")
                result.append("saved")
                self.saved_during_course = True
            else:
                result.append("started")

    def leave_course(self):
        if not self.in_course:
            return False
        # 直接进入明确的任务路由，同样触发网站组件的离开/保存流程。
        # 历史后退可能只回退内嵌 PDF 或停留在另一详情页，不能保证到达任务首页。
        is_video = "/safetyVideo/" in urlsplit(self.page.url).path
        saved = self.saved_during_course if is_video else False
        try:
            routed = self.page.run_js("""
                const vm = Array.from(document.querySelectorAll('*'))
                    .map(e => e.__vue__).find(v => v && v.$router);
                if (!vm) return false;
                return Promise.resolve(vm.$router.push({name: 'examTask', params: {examId: arguments[0]}}))
                    .then(() => true).catch(error => {
                        if (error.name === 'NavigationDuplicated') return true;
                        throw error;
                    });
            """, TASK_ID)
            if not routed:
                raise RuntimeError("无法定位网站的课程导航，已保留页面，请手动返回以保存进度。")
            self.wait_for(lambda: urlsplit(self.page.url).path.rstrip("/") ==
                          f"/lab-study-front/examTask/{TASK_ID}", "网站未能切换到当前任务首页。")
            self.in_course = False
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if "saved" in self.packets():
                    saved = True
                if saved:
                    break
                time.sleep(0.25)
        finally:
            self.page.listen.stop()
        if not saved:
            return False
        try:
            self.wait_task()
        except RuntimeError as exc:
            if not str(exc).startswith("课程表格加载超时："):
                raise
            # 仅在网站已确认保存后允许重新加载列表，最多重试一次。
            print("学习进度已保存，课程列表未及时加载，正在重新打开任务首页。", flush=True)
            self.page.get(STUDY_URL)
            try:
                self.wait_task()
            except RuntimeError as retry_exc:
                raise RuntimeError(f"学习进度已保存，但返回列表失败：{retry_exc}") from retry_exc
        return saved
