"""本机课程配置；浏览器资料保存在项目目录。"""

import json
import math
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8-sig"))


def parse_study_url(value):
    """从任务首页或课程详情地址取得任务编号，不保存查询参数。"""
    url = urlsplit(str(value).strip())
    parts = url.path.strip("/").split("/")
    if (url.scheme != "https" or url.netloc != "sysaq.ustc.edu.cn"
            or parts[:2] != ["lab-study-front", "examTask"]
            or len(parts) < 3 or not parts[2].isascii() or not parts[2].isdigit()):
        raise ValueError("请在项目浏览器中登录，进入要学习的任务首页，再使用当前浏览器任务。")
    task_id = parts[2]
    return f"https://sysaq.ustc.edu.cn/lab-study-front/examTask/{task_id}", task_id


STUDY_URL, TASK_ID = parse_study_url(CONFIG["study_url"])


def use_study_url(value):
    """保存用户选中的任务，保留其他配置。"""
    global STUDY_URL, TASK_ID
    target, task_id = parse_study_url(value)
    path = ROOT / "config.json"
    config = json.loads(path.read_text(encoding="utf-8-sig"))
    config["study_url"] = target
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    CONFIG.update(config)
    STUDY_URL, TASK_ID = target, task_id
    return target, task_id


REQUIRED_TOTAL_HOURS = float(CONFIG["required_hours"])
if not math.isfinite(REQUIRED_TOTAL_HOURS) or REQUIRED_TOTAL_HOURS <= 0:
    raise ValueError("config.json 的 required_hours 必须大于 0。")


def browser_options():
    from DrissionPage import ChromiumOptions

    browser = Path(CONFIG["browser_path"])
    if not browser.is_file():
        raise FileNotFoundError(f"未找到浏览器，请修改 config.json 的 browser_path：{browser}")
    return (ChromiumOptions(read_file=False)
            .set_browser_path(str(browser))
            .set_local_port(int(CONFIG["browser_port"]))
            .set_user_data_path(str(ROOT / ".browser_data")))


def open_browser():
    from DrissionPage import ChromiumPage

    return ChromiumPage(browser_options())
