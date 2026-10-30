"""Optional Chromium layout checks: python tests/browser_dashboard_layout.py.

Install Playwright in the development environment and use a system Chromium,
or run `python -m playwright install chromium`. No camera hardware is required.
"""
from pathlib import Path
import shutil
import sys

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from axispy_ui import HTML
from browser_preview_support import STREAM_FIXTURE


VIEWPORTS = [(width, 900) for width in (
    390, 559, 560, 599, 600, 601, 768, 1100, 1101, 1199, 1200,
    1280, 1281, 1348, 1349, 1350, 1439, 1440,
)] + [(1440, 719), (1440, 720), (1920, 1080)]


def columns(page):
    return page.locator('#cameras').evaluate(
        "e => getComputedStyle(e).gridTemplateColumns.split(' ').length")


def check_preview_position(page, count, width, selector):
    expected_right = width >= 600 and (width <= 1280 or count == 1 or (count == 2 and width < 1440))
    for index in range(count):
        card = page.locator('.camera').nth(index)
        info = card.locator('.camera-info').bounding_box()
        preview = card.locator('.preview').bounding_box()
        if expected_right:
            assert preview['x'] >= info['x'] + info['width'] - 1, (count, width, info, preview)
        else:
            assert preview['y'] >= info['y'] + info['height'] - 1, (count, width, info, preview)
        frame = card.locator(selector).bounding_box()
        assert frame['width'] > 0 and frame['height'] > 0
        assert abs(frame['width'] / frame['height'] - 16 / 9) < .01, frame
        box = card.bounding_box()
        assert frame['x'] >= box['x'] and frame['x'] + frame['width'] <= box['x'] + box['width'] + 1


def main():
    with sync_playwright() as playwright:
        executable = shutil.which('chromium') or shutil.which('chromium-browser')
        browser = playwright.chromium.launch(
            **({'executable_path': executable} if executable else {}),
            headless=True, args=['--no-sandbox'])
        page = browser.new_page()
        page.add_init_script(STREAM_FIXTURE)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        payload = dict(version='test', selected=0, joystick='Test joystick', armed=True,
                       time='2026-01-01T00:00:00Z', poll_interval_ms=60000,
                       preview_fps=25, preview_resolution='640x360',
                       system=dict(cpu_percent=25, cpu_status='ok',
                                   memory=dict(percent=50, used_bytes=2**30, total_bytes=2**31)))

        def route_request(route):
            path = route.request.url.removeprefix('http://dashboard.test')
            if path == '/':
                route.fulfill(content_type='text/html', body=HTML)
            elif path == '/api/status':
                route.fulfill(json=payload)
            elif path.startswith('/api/logs'):
                route.fulfill(json=dict(cursor=0, entries=[]))
            else:
                route.fulfill(status=404)

        page.route('http://dashboard.test/**', route_request)
        checked = 0
        for count in range(1, 5):
            payload['cameras'] = [dict(id=i, slot=i+1, name=f'Camera J{i+1}',
                selected=i == 0, online=True, url='http://camera.local', channel=1,
                focus='auto', focus_source='camera', ptz_activity='idle') for i in range(count)]
            page.goto('http://dashboard.test/', wait_until='domcontentloaded')
            page.wait_for_function('(n)=>document.querySelectorAll(".camera").length===n', arg=count)
            assert page.locator('#resources').is_hidden()
            for width, height in VIEWPORTS:
                page.set_viewport_size(dict(width=width, height=height))
                page.evaluate('window.scrollTo(0,0)')
                expected_columns = 1 if width < 600 or count == 1 else 2 if width <= 1280 else count
                assert columns(page) == expected_columns, (count, width, columns(page))
                page.get_by_role('button', name='Show live video').click()
                page.wait_for_function('(n)=>[...document.querySelectorAll(".preview img")].filter(i=>i.naturalWidth===640&&!i.hidden).length===n', arg=count)
                check_preview_position(page, count, width, 'img')
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (count, width)
                if width >= 1440 and height >= 720:
                    assert page.evaluate('document.documentElement.scrollHeight <= innerHeight'), (count, width, height)
                before = page.locator('#cameras').bounding_box()
                starts = page.evaluate("streamStarts")
                page.evaluate('window.savedImages=[...document.querySelectorAll(".preview img")]')
                page.get_by_role('button', name='Show system usage').click()
                assert page.locator('#resources').is_visible()
                assert page.locator('#cameras').bounding_box() == before
                page.evaluate('status()')
                assert page.evaluate('savedImages.every((img,i)=>img===document.querySelectorAll(".preview img")[i])')
                assert page.evaluate("streamStarts") == starts
                if width > 1280:
                    brand = page.locator('.brand').bounding_box()
                    usage = page.locator('.usage-slot').bounding_box()
                    actions = page.locator('.actions').bounding_box()
                    assert brand['x'] + brand['width'] <= usage['x']
                    assert usage['x'] + usage['width'] <= actions['x']
                for camera in payload['cameras']:
                    camera['online'] = False
                page.evaluate('status()')
                assert page.locator('.preview img').count() == 0
                check_preview_position(page, count, width, '.preview-placeholder')
                for camera in payload['cameras']:
                    camera['online'] = True
                page.get_by_role('button', name='Hide live video').click()
                page.evaluate('status()')
                assert columns(page) == expected_columns
                assert page.locator('.preview:visible').count() == 0
                before_hide = page.locator('#cameras').bounding_box()
                page.get_by_role('button', name='Hide system usage').click()
                assert page.locator('#cameras').bounding_box() == before_hide
                if width >= 1440 and height >= 720:
                    page.get_by_role('button', name='Debug logs').click()
                    assert page.locator('#cameras').bounding_box() == before_hide
                    assert page.locator('#debug').is_visible()
                    page.get_by_role('button', name='Debug logs').click()
                checked += 1
            print(f'PASS {count} camera(s): {len(VIEWPORTS)} viewports, live/offline/off, usage and debug toggles')
        assert not errors, errors
        browser.close()
        print(f'PASS {checked} layout combinations')


if __name__ == '__main__':
    main()
