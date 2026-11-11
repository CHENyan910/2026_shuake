"""适配新版任务表格，按网站必学/选学要求逐项学习。"""

import argparse
import json
import sys
import time

from project_config import open_browser, STUDY_URL
from site_adapter import Site, candidates, clock, seconds


def supported(course):
    return course.module in {"安全知识", "安全手册", "安全标识", "规章制度", "法律法规", "事故案例", "安全视频"}


def learn(site, course, verify_seconds=None):
    print(f"\n进入：{course.title}（{'必学' if course.must else '选学'}，{course.module}）", flush=True)
    site.open_course(course)
    try:
        def ready():
            state = site.detail()
            return state if state["done"] and state["required"] else False
        state = site.wait_for(ready, "课程计时器未出现，请检查页面加载或课程类型。")
        initial = previous = seconds(state["done"])
        started = last_change = last_report = time.monotonic()
        print(f"页面计时：{clock(initial)} / {clock(seconds(state['required']))}", flush=True)
        observed = False
        video = course.module == "安全视频"
        reached_at = None
        blocked_reported = False
        while True:
            if site.continue_prompt():
                print("已通过页面按钮继续当前课程。", flush=True)
            state = site.detail()
            if not state["done"] or not state["required"]:
                raise RuntimeError("课程计时器消失，可能登录失效或页面已退出。")
            if video:
                status = site.play_video()
                if status == "error":
                    raise RuntimeError("视频播放器报告加载错误，请检查网站的视频资源和网络。")
                if status == "blocked" and not blocked_reported:
                    print("浏览器阻止了自动播放，请在课程页面点击播放。", flush=True)
                    blocked_reported = True
            current, required = seconds(state["done"]), seconds(state["required"])
            now = time.monotonic()
            events = site.packets()  # 检查网站是否拒绝开始学习或保存进度。
            if current > previous:
                observed = True
                last_change = now
                previous = current
            if current >= required:
                if reached_at is None:
                    reached_at = now
                    print(f"课程页面已达到要求：{clock(current)} / {clock(required)}", flush=True)
                if not video or ("saved" in events and now > reached_at):
                    break
                if now - reached_at > 45:
                    raise RuntimeError("视频已达到要求，但 45 秒内未收到网站保存确认。")
            if verify_seconds is not None and now - started >= verify_seconds:
                if not observed:
                    raise RuntimeError("验证期间页面时长没有增加。")
                # 视频首播加载需要时间，须等至少一次实际上传确认，不能只等固定墙钟时长。
                if not video or site.saved_during_course:
                    print(f"验证：页面计时从 {clock(initial)} 增加到 {clock(current)}。", flush=True)
                    break
                if now - started >= verify_seconds + 60:
                    raise RuntimeError("验证期间视频已有计时，但未收到网站保存确认。")
            if now - last_change > 60:
                raise RuntimeError("页面时长已连续 60 秒没有增加，请查看网站提示。")
            if now - last_report >= 30:
                print(f"当前课程：{clock(current)} / {clock(required)}", flush=True)
                last_report = now
            time.sleep(2)
    finally:
        saved = site.leave_course()
        print("网站已确认保存本次学习进度。" if saved else "未收到保存确认，请检查网站记录后再继续。", flush=True)
    if not saved:
        raise RuntimeError("网站未确认本次学习进度已保存，已停止自动切换课程。")
    return {"title": course.title, "initial_seconds": initial, "final_seconds": current,
            "required_seconds": required, "save_confirmed": saved}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="检查网站要求和全部未学课程，不开始学习")
    mode.add_argument("--verify", action="store_true", help="用真实课程验证约 20 秒的计时及进度保存")
    parser.add_argument("--module", help="仅验证指定课程类型，例如：安全视频（须与 --verify 一起使用）")
    args = parser.parse_args(argv)
    if args.module and not args.verify:
        parser.error("--module 只能与 --verify 一起使用")
    site = Site(open_browser())
    if not (args.check or args.verify):
        site.page.get(STUDY_URL)
        input("请完成登录并确认课程列表显示正常，按回车开始（Ctrl+C 停止）。")
    state = site.open_task()
    requirements, courses = site.collect_courses(state)
    print("网站要求：" + requirements.describe(), flush=True)
    print(f"已读取全部 {len(courses)} 个未学条目。", flush=True)
    if args.check:
        modules = {}
        for course in courses:
            modules[course.module] = modules.get(course.module, 0) + 1
        print("课程类型：" + json.dumps(modules, ensure_ascii=False), flush=True)
        print(f"支持的待学条目：{sum(supported(c) and c.remaining > 0 for c in courses)}", flush=True)
        return 0
    completed = set()
    while True:
        if requirements.complete:
            print("网站显示必学和选学时长均已达标。", flush=True)
            return 0
        available = candidates(courses, requirements, completed)
        supported_courses = [c for c in available if supported(c) and (not args.module or c.module == args.module)]
        if not supported_courses:
            if available:
                print("未找到可由当前模式处理的待学条目：", flush=True)
                for course in available[:10]:
                    print(f"  {course.title}（{course.module}）", flush=True)
                return 1
            print("本轮可学习内容已处理完成。网站汇总时长可能延迟 5 分钟更新，请稍后检查。", flush=True)
            return 0
        course = supported_courses[0]
        result = learn(site, course, verify_seconds=20 if args.verify else None)
        if args.verify:
            print("实站验证通过：" + json.dumps(result, ensure_ascii=False), flush=True)
            return 0
        completed.add(course.key)
        requirements, courses = site.collect_courses(site.open_task())
        print("网站当前汇总：" + requirements.describe(), flush=True)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已停止；浏览器保留打开，请检查当前课程记录。")
        sys.exit(130)
    except Exception as exc:
        print(f"运行停止：{exc}", file=sys.stderr)
        sys.exit(1)
