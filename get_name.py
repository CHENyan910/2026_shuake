"""按新版网页自动翻页，导出全部未学课程。"""

from dataclasses import asdict
import json

from project_config import ROOT, STUDY_URL, TASK_ID, open_browser
from site_adapter import Site


def main():
    page = open_browser()
    page.get(STUDY_URL)
    input("请完成登录并确认课程列表显示正常，然后按回车读取全部课程。")
    requirements, courses = Site(page).collect_courses()
    (ROOT / "name_list.txt").write_text("\n".join(c.title for c in courses) + "\n", encoding="utf-8")
    (ROOT / "name_list_task.txt").write_text(TASK_ID, encoding="utf-8")
    output = {"task_id": TASK_ID, "requirements": asdict(requirements),
              "courses": [asdict(c) for c in courses]}
    (ROOT / "course_list.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已自动翻页并保存全部 {len(courses)} 个未学条目。")
    print(requirements.describe())


if __name__ == "__main__":
    main()
