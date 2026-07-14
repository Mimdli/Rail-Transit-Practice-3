"""补拍 Web 调度/接口/紧急停车截图。"""
from __future__ import annotations

import os
import sys
import time
import subprocess
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "blackbox_test" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)


def wait_health(timeout=25):
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen("http://127.0.0.1:8765/api/health", timeout=1)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def main():
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "src.web.app:app",
         "--host", "127.0.0.1", "--port", "8765"],
        cwd=str(ROOT),
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert wait_health(), "health failed"
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto("http://127.0.0.1:8765/", wait_until="networkidle", timeout=60000)
            time.sleep(1.2)

            def close_subpage():
                page.evaluate(
                    "() => { const s=document.getElementById('subpage');"
                    " if (s) { s.classList.remove('open'); s.hidden=true; } }"
                )

            # sidebar nav: data-page attributes from index.html
            mapping = {
                "dispatch": "TC-W02_web_dispatch.png",
                "interface": "TC-W04_web_interface.png",
                "scene": "TC-W03_web_scenario.png",
            }
            for page_id, fname in mapping.items():
                close_subpage()
                page.locator(f"button[data-page='{page_id}']").first.click(force=True)
                time.sleep(1.0)
                page.screenshot(path=str(OUT / fname))
                print("saved", fname)

            close_subpage()
            page.locator("button[data-page='overview']").first.click(force=True)
            time.sleep(0.8)
            close_subpage()
            # hide open subpage overlay just in case
            page.evaluate(
                "() => { const s=document.getElementById('subpage');"
                " if (s) { s.classList.remove('open'); s.style.display='none'; } }"
            )
            page.locator("#emergencyBtn").click(force=True)
            time.sleep(0.7)
            page.screenshot(path=str(OUT / "TC-W05_emergency_confirm.png"))
            print("saved TC-W05_emergency_confirm.png")
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except Exception:
            server.kill()


if __name__ == "__main__":
    main()
