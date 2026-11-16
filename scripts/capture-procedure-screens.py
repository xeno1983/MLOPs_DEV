"""Capture live UGV MLOps screens for the operator procedure deck."""
from __future__ import annotations

from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "procedure" / "screens"
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
PORTAL = "http://127.0.0.1:8080/"
LS = "http://127.0.0.1:8081/"
MLFLOW = "http://127.0.0.1:5000/"
XTREME = "http://127.0.0.1:8190/"
CATALOG = "http://127.0.0.1:8000/docs"


def shot(page, name: str, wait_ms: int = 800) -> Path:
    page.wait_for_timeout(wait_ms)
    dest = OUT / f"{name}.png"
    page.screenshot(path=str(dest), full_page=False)
    print("saved", dest.name, dest.stat().st_size)
    return dest


def open_flow_step(page, index: int) -> None:
    page.locator("#tab-flow").click()
    page.wait_for_selector("#flow .step")
    page.wait_for_timeout(400)
    page.locator("#flow .step").nth(index).click()
    page.wait_for_selector("#detail .form-grid, #detail h3")
    page.wait_for_timeout(2500)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=str(CHROME),
            headless=True,
            args=["--hide-scrollbars", "--disable-gpu"],
        )
        page = browser.new_page(viewport={"width": 1600, "height": 1000}, device_scale_factor=1)
        page.set_default_timeout(90000)

        page.goto(PORTAL, wait_until="load")
        page.wait_for_selector("#hint")
        page.wait_for_function("() => document.querySelector('#dash-now h3')", timeout=90000)
        page.wait_for_timeout(1500)
        shot(page, "01_dashboard")

        page.locator("#dash-more summary").click()
        page.wait_for_timeout(800)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(600)
        shot(page, "02_dashboard_lists")
        page.evaluate("window.scrollTo(0, 0)")

        page.locator("#tab-data").click()
        page.wait_for_selector("#data-missions")
        page.wait_for_timeout(2500)
        shot(page, "03_data_preview")

        page.locator("#tab-flow").click()
        page.wait_for_selector("#flow .step")
        page.wait_for_timeout(800)
        shot(page, "04_flow_overview")

        names = [
            "05_step1_collect",
            "06_step2_ingest",
            "07_step3_annotate",
            "08_step4_dataset",
            "09_step5_train",
            "10_step6_gate",
            "11_step7_package",
            "12_step8_sync",
        ]
        for i, name in enumerate(names):
            open_flow_step(page, i)
            if i == 2:
                tool = page.locator("#act-tool")
                if tool.count():
                    tool.select_option("salt")
                    page.wait_for_timeout(400)
            shot(page, name)

        open_flow_step(page, 2)
        tool = page.locator("#act-tool")
        if tool.count():
            tool.select_option("xtreme1")
            page.wait_for_timeout(400)
            shot(page, "13_step3_xtreme1")
            tool.select_option("labelstudio")
            page.wait_for_timeout(400)
            shot(page, "14_step3_labelstudio")

        page.locator("#tab-iss").click()
        page.wait_for_selector("#iss-form")
        page.wait_for_timeout(800)
        shot(page, "15_issues")

        try:
            page.goto(LS, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            email = page.locator('input[type="email"], input[name="email"]')
            pwd = page.locator('input[type="password"]')
            if email.count() and pwd.count():
                email.first.fill("admin@ugv.local")
                pwd.first.fill("label_change_me")
                btn = page.locator('button[type="submit"], button:has-text("Log in"), button:has-text("Sign in")')
                if btn.count():
                    btn.first.click()
                    page.wait_for_timeout(2500)
            shot(page, "16_labelstudio")
        except PlaywrightTimeout:
            print("skip labelstudio")

        try:
            page.goto(MLFLOW, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
            shot(page, "17_mlflow")
        except PlaywrightTimeout:
            print("skip mlflow")

        try:
            page.goto(CATALOG, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            shot(page, "18_catalog_docs")
        except PlaywrightTimeout:
            print("skip catalog docs")

        try:
            resp = page.goto(XTREME, wait_until="load", timeout=8000)
            if resp and resp.ok:
                page.wait_for_timeout(1500)
                shot(page, "19_xtreme1")
            else:
                print("xtreme1 not up", resp.status if resp else None)
        except Exception as exc:
            print("skip xtreme1", exc)

        browser.close()


if __name__ == "__main__":
    main()
