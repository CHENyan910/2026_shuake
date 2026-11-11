"""检查本机配置，并保存项目专用的浏览器配置。"""

from project_config import ROOT, STUDY_URL, browser_options


if __name__ == "__main__":
    options = browser_options()
    options.save(str(ROOT / "browser.ini"))
    print(f"浏览器配置已保存到：{ROOT / 'browser.ini'}")
    print(f"学习地址：{STUDY_URL}")
    print("目标时长：自动读取网站的必学和选学要求")
