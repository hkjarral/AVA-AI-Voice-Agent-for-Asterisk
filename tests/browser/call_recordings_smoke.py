"""Synthetic browser acceptance. Run after npm run build + vite preview on loopback.

python tests/browser/call_recordings_smoke.py http://127.0.0.1:4177
Requires Playwright Chromium. PLAYWRIGHT_CHROMIUM_EXECUTABLE can select a local
browser. An optional second argument supplies a locally generated synthetic WAV
(e.g. SoX-decoded GSM) instead of the default PCM fixture. Never use caller audio.
All API requests are intercepted; non-loopback traffic is blocked.
"""
import io
import os
import sys
import tempfile
import wave
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright, expect


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:4177'
    assert urlparse(base).hostname in ('127.0.0.1', 'localhost'), 'Loopback only'
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b'\0\0' * 24000)
    audio_bytes = Path(sys.argv[2]).read_bytes() if len(sys.argv) > 2 else buffer.getvalue()
    record = dict(id='synthetic-record', call_id='synthetic-call', caller_number=None,
                  caller_name='Synthetic fixture', start_time='2026-01-01T00:00:00Z',
                  duration_seconds=3, provider_name='synthetic', outcome='completed',
                  conversation_history=[], tool_calls=[], pre_call_tool_calls=[],
                  post_call_tool_calls=[], total_turns=0, barge_in_count=0)
    state = {'metadata': 'available', 'download_error': False}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=os.getenv('PLAYWRIGHT_CHROMIUM_EXECUTABLE'))
        page = browser.new_page(accept_downloads=True)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.add_init_script("localStorage.setItem('token', 'synthetic-browser-token')")
        def route(request):
            url = urlparse(request.request.url)
            if url.hostname not in ('127.0.0.1', 'localhost'):
                request.abort()
                return
            path = url.path
            if not path.startswith('/api/'):
                request.continue_()
                return
            if '/recording' in path:
                assert request.request.headers.get('authorization') == 'Bearer synthetic-browser-token'
                if path.endswith('/recording'):
                    if state['metadata'] == 'error':
                        request.fulfill(status=409, json={'detail': 'Multiple recordings match this call'})
                    else:
                        request.fulfill(json={'has_recording': state['metadata'] == 'available', 'filename': 'synthetic.wav', 'file_size_bytes': len(audio_bytes), 'duration_hint': None})
                elif path.endswith('/download') and state['download_error']:
                    request.fulfill(status=404, json={'detail': 'Recording file not found'})
                else:
                    request.fulfill(body=audio_bytes, content_type='audio/wav')
            elif path == '/api/auth/me':
                request.fulfill(json={'username': 'synthetic-admin', 'must_change_password': False})
            elif path == '/api/wizard/status':
                request.fulfill(json={'configured': True, 'resume_required': False})
            elif path == '/api/calls':
                request.fulfill(json={'calls': [record], 'total': 1, 'page': 1, 'page_size': 20, 'total_pages': 1})
            elif path == '/api/calls/synthetic-record':
                request.fulfill(json=record)
            elif path == '/api/calls/stats':
                request.fulfill(body='null', content_type='application/json')
            elif path == '/api/calls/redaction-policy':
                request.fulfill(json={'configured_mode': 'strict', 'modes': {'strict': 'Synthetic privacy policy'}})
            elif path == '/api/calls/filters':
                request.fulfill(json={'providers': [], 'pipelines': [], 'contexts': [], 'outcomes': []})
            elif path == '/api/agents':
                request.fulfill(json=[])
            else:
                request.fulfill(json={})
        page.route('**/*', route)
        page.goto(base + '/history?id=synthetic-record')
        expect(page.get_by_role('dialog', name='Call Details')).to_be_visible()
        page.get_by_role('button', name='Play recording', exact=True).click()
        expect(page.get_by_role('button', name='Pause', exact=True)).to_be_visible()
        expect(page.get_by_role('slider', name='Recording position')).to_be_enabled()
        page.get_by_role('button', name='Pause', exact=True).click()
        with page.expect_download() as download:
            page.get_by_role('button', name='Download recording', exact=True).click()
        with tempfile.TemporaryDirectory(prefix='aava-browser-recording-') as directory:
            target = Path(directory) / 'download.wav'
            download.value.save_as(target)
            assert target.read_bytes() == audio_bytes
            assert download.value.suggested_filename == 'synthetic.wav'
        state['download_error'] = True
        page.get_by_role('button', name='Download recording', exact=True).click()
        expect(page.get_by_role('alert')).to_have_text('Recording file not found')
        state['metadata'] = 'error'
        page.get_by_role('button', name='Refresh recording', exact=True).click()
        expect(page.get_by_role('alert')).to_have_text('Multiple recordings match this call')
        expect(page.get_by_text('No recording available', exact=True)).not_to_be_visible()
        state['metadata'] = 'missing'
        page.get_by_role('button', name='Refresh recording', exact=True).click()
        expect(page.get_by_text('No recording available', exact=True)).to_be_visible()
        state['metadata'] = 'available'
        page.get_by_role('button', name='Refresh recording', exact=True).click()
        expect(page.get_by_role('button', name='Play recording', exact=True)).to_be_enabled()
        page.get_by_role('button', name='Close call details', exact=True).click()
        expect(page.get_by_role('dialog', name='Call Details')).not_to_be_visible()
        assert not errors, errors
        browser.close()
    print('PASS: real Chromium synthetic playback/pause/seek availability, download byte fidelity, blob errors, missing/late refresh, close; no page errors')


if __name__ == '__main__':
    main()
