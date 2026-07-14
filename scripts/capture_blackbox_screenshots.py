"""黑盒测试截图采集 — 桌面 Qt + Web ATS

用法（在项目根目录）:
  set PYTHONPATH=.
  python scripts/capture_blackbox_screenshots.py
"""

from __future__ import annotations

import os
import sys
import time
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "blackbox_test" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONPATH", str(ROOT))


def capture_desktop():
    """用 offscreen 兼容方式抓取桌面主窗口关键界面。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "windows")
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtCore import QTimer
    from src.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    win = MainWindow()
    win.resize(1400, 900)
    win.show()
    app.processEvents()
    time.sleep(0.8)
    app.processEvents()

    def grab(name: str):
        path = OUT / name
        win.grab().save(str(path))
        print(f"[desktop] {path.name}")

    grab("TC-D01_startup_main.png")

    # 调度中心页
    for i in range(win.tabs.count()):
        if "调度" in win.tabs.tabText(i):
            win.tabs.setCurrentIndex(i)
            break
    app.processEvents()
    time.sleep(0.3)
    grab("TC-D05_dispatch_center.png")

    # 运行仿真 + 岔口进路区域（控制面板已在仿真页）
    for i in range(win.tabs.count()):
        if "运行仿真" in win.tabs.tabText(i):
            win.tabs.setCurrentIndex(i)
            break
    app.processEvents()
    time.sleep(0.3)
    grab("TC-D02_manual_drive_panel.png")

    # 线路可视化
    for i in range(win.tabs.count()):
        if "线路可视化" in win.tabs.tabText(i) or "运营线路" in win.tabs.tabText(i):
            win.tabs.setCurrentIndex(i)
            break
    app.processEvents()
    time.sleep(0.3)
    grab("TC-D07_track_view.png")

    # 静止驻车：不给牵引，跑若干步再截图
    for i in range(win.tabs.count()):
        if "运行仿真" in win.tabs.tabText(i):
            win.tabs.setCurrentIndex(i)
            break
    for _ in range(30):
        if hasattr(win, "controller"):
            win.controller.step(0.05)
    app.processEvents()
    grab("TC-D03_hold_at_rest.png")

    # 牵引发车后速度面板
    if hasattr(win, "controller"):
        win.controller.set_throttle(1.0)
        for _ in range(80):
            win.controller.step(0.033)
    app.processEvents()
    grab("TC-D04_traction_running.png")

    win.close()
    app.quit()


def capture_web():
    """启动临时 Web 服务并用 Playwright 截图；无 Playwright 则用 API 探测并写说明图。"""
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "src.web.app:app",
         "--host", "127.0.0.1", "--port", "8765"],
        cwd=str(ROOT),
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        import urllib.request
        for _ in range(40):
            try:
                with urllib.request.urlopen("http://127.0.0.1:8765/api/health", timeout=1) as r:
                    if r.status == 200:
                        break
            except Exception:
                time.sleep(0.5)
        else:
            print("[web] health check failed")
            return

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            print("[web] playwright not installed, trying selenium/fallback")
            _capture_web_fallback()
            return

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto("http://127.0.0.1:8765/", wait_until="networkidle", timeout=60000)
            time.sleep(1.5)
            page.screenshot(path=str(OUT / "TC-W01_web_overview.png"), full_page=False)
            print("[web] TC-W01_web_overview.png")

            # 调度页
            btn = page.locator("button", has_text="调度控制")
            if btn.count():
                btn.first.click()
                time.sleep(0.8)
                page.screenshot(path=str(OUT / "TC-W02_web_dispatch.png"))
                print("[web] TC-W02_web_dispatch.png")

            # 场景页
            btn = page.locator("button", has_text="数据与场景")
            if btn.count():
                btn.first.click()
                time.sleep(0.8)
                page.screenshot(path=str(OUT / "TC-W03_web_scenario.png"))
                print("[web] TC-W03_web_scenario.png")

            # 接口通信
            btn = page.locator("button", has_text="接口通信")
            if btn.count():
                btn.first.click()
                time.sleep(0.8)
                page.screenshot(path=str(OUT / "TC-W04_web_interface.png"))
                print("[web] TC-W04_web_interface.png")

            # 触发紧急停车确认（回到总览）
            overview = page.locator("button", has_text="综合运行总览")
            if overview.count():
                overview.first.click()
                time.sleep(0.5)
            emergency = page.locator("#emergencyBtn")
            if emergency.count():
                emergency.first.click()
                time.sleep(0.5)
                page.screenshot(path=str(OUT / "TC-W05_emergency_confirm.png"))
                print("[web] TC-W05_emergency_confirm.png")
                cancel = page.locator("#cancelBtn")
                if cancel.count():
                    cancel.first.click()

            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except Exception:
            server.kill()


def _capture_web_fallback():
    """无浏览器自动化时，至少验证 API 并生成占位说明。"""
    import urllib.request
    import json
    from PIL import Image, ImageDraw, ImageFont

    with urllib.request.urlopen("http://127.0.0.1:8765/api/health") as r:
        health = json.loads(r.read().decode())
    with urllib.request.urlopen("http://127.0.0.1:8765/api/snapshot") as r:
        snap = json.loads(r.read().decode())

    def placeholder(name, lines):
        img = Image.new("RGB", (1200, 700), (18, 28, 36))
        draw = ImageDraw.Draw(img)
        y = 40
        for line in lines:
            draw.text((40, y), line, fill=(200, 220, 230))
            y += 28
        path = OUT / name
        img.save(path)
        print(f"[web-fallback] {name}")

    train_n = len(snap.get("trains", snap.get("trainList", [])) or [])
    placeholder("TC-W01_web_overview.png", [
        "Web ATS /api/health = " + str(health),
        f"snapshot trains≈{train_n}",
        "请在浏览器打开 http://127.0.0.1:8765/ 补充真实截图",
        "(本图为 API 自检占位图 — 安装 playwright 后可自动抓真图)",
    ])
    placeholder("TC-W02_web_dispatch.png", [
        "调度控制页 — 请手动截图替换",
        "路径: 侧栏 → 调度控制",
    ])
    placeholder("TC-W03_web_scenario.png", [
        "数据与场景页 — 请手动截图替换",
    ])
    placeholder("TC-W04_web_interface.png", [
        "接口通信页 — 请手动截图替换",
    ])
    placeholder("TC-W05_emergency_confirm.png", [
        "紧急停车确认对话框 — 请手动截图替换",
    ])


if __name__ == "__main__":
    print("OUT =", OUT)
    try:
        capture_desktop()
    except Exception as e:
        print("[desktop] FAILED:", e)
    try:
        capture_web()
    except Exception as e:
        print("[web] FAILED:", e)
    print("done. files:", sorted(p.name for p in OUT.glob("*.png")))
