#!/usr/bin/env python3
"""Capture key workbench screens for UI redesign evidence (fixture data only)."""
import base64
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import test_core  # noqa: E402

OUT = Path(os.environ.get("UI_SCREENSHOT_DIR", ROOT / "build/evidence/ui-redesign"))
PREFIX = os.environ.get("UI_SCREENSHOT_PREFIX", "after")
SCENES = [
    ("workbench", "工作台"),
    ("chat", "聊天"),
    ("tasks", "任务"),
    ("operations", "待确认"),
    ("settings", "连接与授权"),
]


def seed(fixture):
    cid = "1234567890abcdef1234567890abcdef"
    fixture.call(
        "messages",
        dict(
            conversation_id=cid,
            body="Fixture redesign preview message for screenshot capture.",
            expected_sequence=0,
            request_id="ui-redesign-shot",
        ),
    )
    task = fixture.call("state")["tasks"][0]
    claim = fixture.call("tasks/" + task["id"] + "/claim", {}, role="worker")
    fixture.call(
        "model/run",
        dict(task_id=task["id"], attempt_id=claim["attempt_id"], source_ids=[]),
        role="worker",
    )
    attachment = fixture.call(
        "attachments",
        dict(
            name="fixture-project.txt",
            base64=base64.b64encode(b"Fixture evidence").decode(),
        ),
    )
    fixture.call(
        "operations",
        dict(
            kind="gmail",
            payload=dict(
                **{"from": "fixture@example.test", "to": "review@example.test"},
                subject="Fixture review",
                body=("Full approval paragraph.\n" * 8),
                attachment_ids=[attachment["id"]],
            ),
        ),
    )


def capture(browser, fixture, prefix, color_scheme, width, height, label):
    context = browser.new_context(
        viewport={"width": width, "height": height},
        color_scheme=color_scheme,
    )
    page = context.new_page()
    page.goto(f"http://127.0.0.1:{fixture.port}/")
    page.locator("#token").fill((fixture.data / "owner.token").read_text())
    page.locator("#connect").click()

    nav_map = {
        "工作台": "home",
        "聊天": "chat",
        "任务": "tasks",
        "待确认": "operations",
        "连接与授权": "settings",
    }

    def navigate(nav_label):
        page.evaluate(
            """([name, map]) => { if (typeof go === 'function' && map[name]) go(map[name]); }""",
            [nav_label, nav_map],
        )
        page.wait_for_timeout(250)

    for scene, nav_label in SCENES:
        navigate(nav_label)
        page.wait_for_timeout(200)
        path = OUT / f"{PREFIX}-{scene}-{label}.png"
        full_page = "mobile" not in label
        page.screenshot(path=str(path), full_page=full_page)
    context.close()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    fixture = test_core.CoreContracts()
    fixture.setUp()
    try:
        seed(fixture)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                capture(browser, fixture, PREFIX, "light", 1440, 900, "desktop-light")
                capture(browser, fixture, PREFIX, "dark", 1440, 900, "desktop-dark")
                capture(browser, fixture, PREFIX, "light", 390, 844, "mobile-light")
                capture(browser, fixture, PREFIX, "dark", 390, 844, "mobile-dark")
            finally:
                browser.close()
    finally:
        fixture.tearDown()
    print("Wrote screenshots to", OUT)


if __name__ == "__main__":
    main()
