"""Optional Chromium preference checks: python tests/browser_dashboard_preferences.py."""
import base64
from itertools import product
from pathlib import Path
import shutil
import sys

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from axispy_ui import HTML

KEY = 'axispy.dashboard.preferences.v1'
DEFAULTS = dict(usage=False, video=False, debug=False)


def assert_preferences(page, expected):
    page.wait_for_function('document.querySelectorAll(".camera").length === 2')
    assert page.locator('#resources').is_visible() == expected['usage']
    assert page.locator('#usageButton').get_attribute('aria-expanded') == str(expected['usage']).lower()
    assert page.locator('#usageButton').text_content() == ('Hide' if expected['usage'] else 'Show') + ' system usage'
    assert page.locator('#debug').is_visible() == expected['debug']
    assert page.locator('#debugButton').get_attribute('aria-expanded') == str(expected['debug']).lower()
    assert page.locator('#videoButton').get_attribute('aria-pressed') == str(expected['video']).lower()
    assert page.locator('#videoButton').text_content() == ('Hide' if expected['video'] else 'Show') + ' live video'
    if expected['video']:
        page.wait_for_function('[...document.querySelectorAll(".preview img")].filter(i=>i.naturalWidth===640&&!i.hidden).length === 2')
    else:
        assert page.locator('.preview img').count() == 0
    if expected['debug']:
        page.wait_for_function('document.getElementById("logs").textContent.includes("Test log")')


def main():
    with sync_playwright() as playwright:
        executable = shutil.which('chromium') or shutil.which('chromium-browser')
        browser = playwright.chromium.launch(
            **({'executable_path': executable} if executable else {}),
            headless=True, args=['--no-sandbox'])
        generator = browser.new_page()
        jpeg = base64.b64decode(generator.evaluate("""() => {
            const c=document.createElement('canvas');c.width=640;c.height=360;
            return c.toDataURL('image/jpeg').split(',')[1];
        }"""))
        generator.close()
        errors = []
        contexts = []

        def new_context(init_script=None):
            context = browser.new_context(viewport=dict(width=1920, height=1080))
            contexts.append(context)
            if init_script:
                context.add_init_script(init_script)
            requests = []
            payload = dict(version='test', selected=0, joystick=None, armed=False,
                           time='2026-01-01T00:00:00Z', poll_interval_ms=60000,
                           preview_fps=25, preview_resolution='640x360', system=None,
                           cameras=[dict(id=i, slot=i+1, name=f'J{i+1}', url='http://camera.local',
                                         channel=1, online=True, selected=i == 0,
                                         focus='auto', focus_source='camera', ptz_activity='idle') for i in range(2)])
            def route_request(route):
                path = route.request.url.removeprefix('http://dashboard.test')
                requests.append(path)
                if path == '/':
                    route.fulfill(content_type='text/html', body=HTML)
                elif path == '/api/status':
                    route.fulfill(json=payload)
                elif path.startswith('/api/stream/'):
                    route.fulfill(content_type='image/jpeg', body=jpeg)
                elif path.startswith('/api/logs'):
                    route.fulfill(json=dict(cursor=1, entries=[dict(time='now', level='INFO', message='Test log')]))
                else:
                    route.fulfill(status=404)
            context.route('http://dashboard.test/**', route_request)
            context.on('page', lambda page: page.on('pageerror', lambda error: errors.append(str(error))))
            return context, requests

        for usage, video, debug in product((False, True), repeat=3):
            context, requests = new_context()
            page = context.new_page()
            page.goto('http://dashboard.test/', wait_until='domcontentloaded')
            assert_preferences(page, DEFAULTS)
            expected = dict(usage=usage, video=video, debug=debug)
            for name, selector in [('usage', '#usageButton'), ('video', '#videoButton'), ('debug', '#debugButton')]:
                if expected[name]:
                    page.locator(selector).click()
            assert_preferences(page, expected)
            if any(expected.values()):
                assert page.evaluate('(key)=>JSON.parse(localStorage.getItem(key))', KEY) == expected
            page.reload(wait_until='domcontentloaded')
            assert_preferences(page, expected)
            # Reload/page lifecycle cleanup must not overwrite the saved video choice.
            page.evaluate("window.dispatchEvent(new Event('pagehide'))")
            assert page.locator('.preview img').count() == 0
            page.evaluate("window.dispatchEvent(new Event('pageshow'))")
            assert_preferences(page, expected)
            second = context.new_page()
            second.goto('http://dashboard.test/', wait_until='domcontentloaded')
            assert_preferences(second, expected)
            # Explicit off choices are saved as well, without clearing other preferences.
            for name, selector in [('usage', '#usageButton'), ('video', '#videoButton'), ('debug', '#debugButton')]:
                if expected[name]:
                    page.locator(selector).click()
            page.reload(wait_until='domcontentloaded')
            assert_preferences(page, DEFAULTS)
            if not video:
                assert not any(path.startswith('/api/stream/') for path in requests)
            if not debug:
                assert not any(path.startswith('/api/logs') for path in requests)
            context.close()
        print('PASS all 8 preference combinations: reload, new tab, off choices, labels and lifecycle cleanup')

        for stored, expected in [('invalid JSON', DEFAULTS), ('null', DEFAULTS), ('[]', DEFAULTS),
                ('{"usage":"true","video":1,"debug":null}', DEFAULTS),
                ('{"usage":true,"unknown":true}', dict(usage=True, video=False, debug=False))]:
            context, _ = new_context()
            page = context.new_page()
            page.goto('http://dashboard.test/', wait_until='domcontentloaded')
            page.evaluate('([key,value])=>localStorage.setItem(key,value)', [KEY, stored])
            page.reload(wait_until='domcontentloaded')
            assert_preferences(page, expected)
            context.close()
        print('PASS missing, corrupt, partial and invalidly typed preferences')

        for failure in ('SecurityError', 'QuotaExceededError'):
            context, _ = new_context("""for (const name of ['getItem','setItem']) {
                Storage.prototype[name]=()=>{throw new DOMException('Storage blocked', '%s')};
            }""" % failure)
            page = context.new_page()
            page.goto('http://dashboard.test/', wait_until='domcontentloaded')
            assert_preferences(page, DEFAULTS)
            for selector in ('#usageButton', '#videoButton', '#debugButton'):
                page.locator(selector).click()
            assert_preferences(page, dict(usage=True, video=True, debug=True))
            page.reload(wait_until='domcontentloaded')
            assert_preferences(page, DEFAULTS)
            context.close()
        print('PASS blocked/full storage: controls still function and reload falls back safely')
        assert not errors, errors
        for context in contexts:
            context.close()
        browser.close()


if __name__ == '__main__':
    main()
