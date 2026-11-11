"""Windows 启动菜单。"""

import os
import subprocess
import sys

import project_config as settings
from project_config import ROOT, open_browser


def main():
    os.chdir(ROOT)
    while True:
        print(f"\n任务 {settings.TASK_ID}：{settings.STUDY_URL}")
        print("目标时长：自动读取网站的必学和选学要求")
        print("1. 打开课程页面并登录")
        print("2. 读取并导出全部课程名称（可选）")
        print("3. 开始自动学习")
        print("4. 检查浏览器配置")
        print("5. 检查网站适配及全部课程（不开始学习）")
        print("6. 验证约 20 秒的真实课程计时及保存")
        print("7. 使用当前浏览器任务（换账号后选择）")
        print("8. 整理全部题库及网站答案（会记录普通练习作答）")
        print("9. 生成可搜索的题库复习文件及 CSV")
        print("10. 自动模拟考试（答题、等待交卷及查分）")
        print("11. 查看最近模拟成绩（不开始考试）")
        print("12. 自动正式考试（确认已授权安全承诺，题间等待15秒）")
        print("0. 退出")
        choice = input("请输入编号：").strip()
        if choice == "0":
            return
        try:
            if choice == "1":
                page = open_browser()
                page.get(settings.STUDY_URL)
                input("请在浏览器完成登录；完成后按回车返回菜单。")
                continue
            if choice == "7":
                page = open_browser()
                target, task_id = settings.use_study_url(page.url)
                print(f"已切换到任务 {task_id}：{target}")
                print("接下来可以选择 5 检查任务，或选择 3 开始学习。")
                continue
            scripts = {"2": ["get_name.py"], "3": ["auto_expriment.py"], "4": ["drimmsionpage_init.py"],
                       "5": ["auto_expriment.py", "--check"], "6": ["auto_expriment.py", "--verify"],
                       "8": ["practice_bank.py", "collect"], "9": ["build_bank_report.py"],
                       "10": ["mock_exam.py", "run"], "11": ["mock_exam.py", "recent"],
                       "12": ["formal_exam.py", "run", "--confirm-promise"]}
            if choice not in scripts:
                print("请输入菜单中的编号。")
                continue
            result = subprocess.run([sys.executable, *scripts[choice]], cwd=ROOT)
            if result.returncode:
                print("操作未完成，请查看上面的错误信息。")
        except Exception as exc:
            print(f"操作失败：{exc}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已停止。")
