"""Read-only composer verification after the live Planner wait, in isolated Chrome."""
import io
import sys
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.config import Config
from publish import business_suite as bs, workflow, journal

WHEN = datetime(2026, 9, 25, 12, 30, tzinfo=timezone.utc)
TEXT = 'Freude mit #Neakasa @home'
HTML = '''<div role="combobox" contenteditable="true" aria-label="Write into the dialogue box to include text with your post.">Freude mit #Neakasa @home</div>
<input type="checkbox" role="switch" aria-label="Set date and time" checked>
<input aria-label="Date picker" value="09/25/2026">
<div role="application" aria-label="Time input"><input role="spinbutton" value="12"><input role="spinbutton" aria-label="minutes" value="30"><input role="spinbutton" aria-label="meridiem" value="PM"><span>12 : 30 PM</span></div>
<ul><li role="listitem"><img src="https://fixture.fbcdn.net/1.png"><button>Remove photo</button></li>
<li role="listitem"><img src="https://fixture.fbcdn.net/2.png"><button>Remove photo</button></li></ul>
<button id="submit">Schedule</button>'''


class FinalFormTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.pw = await async_playwright().start()
        # asyncTearDown is skipped if setup fails; register cleanup as resources are acquired.
        self.addAsyncCleanup(self.pw.stop)
        self.browser = await self.pw.chromium.launch(executable_path=Config().chrome_exe, headless=True)
        self.addAsyncCleanup(self.browser.close)
        self.context = await self.browser.new_context()
        self.addAsyncCleanup(self.context.close)
        self.page = await self.context.new_page()
        self.bodies, self.paths = {}, []
        for i, colour in enumerate(('red', 'green'), 1):
            output = io.BytesIO()
            picture = Image.new('RGB', (128,128), colour)
            picture.putdata([(x*2,0,0) if i == 1 else (0,255-x*2,0)
                             for _y in range(128) for x in range(128)])
            picture.save(output, format='PNG')
            self.bodies[f'https://fixture.fbcdn.net/{i}.png'] = output.getvalue()
            path = self.directory / f'{i}.png'
            path.write_bytes(output.getvalue())
            self.paths.append(path)
        async def route(request):
            if request.request.url in self.bodies:
                await request.fulfill(body=self.bodies[request.request.url], content_type='image/png')
            else:
                await request.abort()
        await self.context.route('**/*', route)

    async def run_workflow(self, change, *, transient_schedule=False):
        if self.page.is_closed():
            self.page = await self.context.new_page()
        if transient_schedule:
            await self.schedule_editor(transient_inputs=True)
        else:
            await self.page.set_content(HTML)
        await self.page.locator('img').first.wait_for()
        await self.page.wait_for_function('Array.from(document.images).every(i=>i.complete&&i.naturalWidth)')
        config = SimpleNamespace(state_dir=self.directory/'state', publish_debug_port=0,
            publish_profile_dir=self.directory/'unused-profile', assert_publish_chrome_isolated=lambda:None)
        post = SimpleNamespace(post_id='fixture-'+change, platform='facebook', text_de=TEXT,
            original_text_de=TEXT, source_text='English', image_paths=tuple(self.paths),
            image_sources=('media_de','media_de'), warnings=())
        async def live(*args, **kw):
            mutations = {
                'caption': "document.querySelector('[role=combobox]').textContent += ' changed'",
                'images': "document.querySelector('ul').append(document.querySelector('li'))",
                'count': "document.querySelector('li').remove()",
                'time': "document.querySelector('[aria-label=minutes]').value='31'",
                'display_time': "document.querySelector('[role=application] span').textContent='12 : 31 PM'",
                'date': "document.querySelector('[aria-label=\"Date picker\"]').value='09/26/2026'",
                'formatted_date': "document.querySelector('[aria-label=\"Date picker\"]').value='Sep 25, 2026'",
                'switch': "document.querySelector('[role=switch]').checked=false",
                'disabled': "document.querySelector('#submit').disabled=true",
                'enable_later': "document.querySelector('#submit').disabled=true;setTimeout(()=>document.querySelector('#submit').disabled=false,120)",
                'change_while_disabled': "document.querySelector('#submit').disabled=true;setTimeout(()=>{document.querySelector('[role=combobox]').textContent+=' changed';document.querySelector('#submit').disabled=false},120)",
                'preview_url': "document.querySelectorAll('img').forEach((img,i)=>img.src='blob:local-preview-'+i)",
                'none': '0',
            }
            await self.page.evaluate(mutations[change])
        async def get(url, **kw):
            return SimpleNamespace(status=200, body=AsyncMock(return_value=self.bodies[url]), dispose=AsyncMock())
        async def submit(*args, **kw):
            self.assertEqual(journal.load(config.state_dir)[-1]['status'], journal.STATUS_SUBMIT_AMBIGUOUS)
            return bs.SubmitResult(clicked=True, confirmed=False, error='fixture unresolved')
        submit_mock = AsyncMock(side_effect=submit)
        async def open_composer(*args, **kw):
            await self.page.bring_to_front()
            return self.page
        real_schedule = bs.set_schedule
        async def set_schedule(*args, **kw):
            return await real_schedule(*args, **kw, verify_device=False)
        with ExitStack() as stack:
            for obj, name, value in (
                (workflow,'cfg',lambda:config),
                (workflow,'attach',AsyncMock(return_value=(SimpleNamespace(stop=AsyncMock()), None, self.context))),
                (workflow.month_readback,'baseline',AsyncMock(return_value=bs.ScheduledBaseline('fixture',0))),
                (bs,'open_composer',AsyncMock(side_effect=open_composer)),
                (bs,'upload_images',AsyncMock(return_value=())),
                (bs,'fill_caption',AsyncMock()),
                (bs,'set_schedule',set_schedule if transient_schedule else AsyncMock(return_value='fixture')),
                (bs,'assert_page_usable',AsyncMock()),
                (workflow.channels,'select',AsyncMock(return_value={'channel':'facebook','account':'fixture'})),
                (workflow.channels,'verify_before_submit',AsyncMock()),
                (workflow,'check_live_slot',AsyncMock(side_effect=live)),
                (workflow.channel_evidence,'require',lambda _: {'context_ids':{}}),
                (self.page.request,'get',AsyncMock(side_effect=get)),
                (bs,'submit',submit_mock),
                (bs,'capture_failure',AsyncMock(return_value=SimpleNamespace(screenshot='', lines=lambda:[]))),
            ):
                stack.enter_context(patch.object(obj,name,value))
            result = await workflow._execute_unlocked(post, WHEN, ui_timezone='UTC',
                timeout=2 if transient_schedule else .3,
                stamp='fixture', submit_enabled=True, report=lambda *_:None)
        return result, submit_mock

    async def test_changes_during_planner_wait_stop_before_submit_intent_without_refill(self):
        for change in ('caption','images','count','time','date','switch'):
            with self.subTest(change=change):
                result, submit = await self.run_workflow(change)
                self.assertEqual(result.attempt.status, journal.STATUS_FAILED_PRE_SUBMIT, result.message)
                self.assertEqual(result.attempt.step, '提交前最终表单复核', result.message)
                submit.assert_not_called()
                self.assertFalse(any(row['status']==journal.STATUS_SUBMIT_AMBIGUOUS
                                     for row in journal.load(self.directory/'state')))

    async def test_unchanged_form_arms_durably_and_submits_once(self):
        result, submit = await self.run_workflow('none')
        self.assertEqual(result.attempt.status, journal.STATUS_SUBMIT_AMBIGUOUS, result.message)
        self.assertEqual(submit.await_count, 1)

    async def test_cleanup_closes_owned_planner_but_preserves_uncertain_composer(self):
        result, submit = await self.run_workflow('none')
        self.assertEqual(result.attempt.status, journal.STATUS_SUBMIT_AMBIGUOUS)
        self.assertEqual(submit.await_count, 1)
        self.assertEqual(self.context.pages, [self.page])

    async def test_pre_submit_failure_leaves_no_owned_browser_pages(self):
        human = await self.context.new_page()
        await human.set_content('<h1>Human tab</h1>')
        result, submit = await self.run_workflow('date')
        self.assertEqual(result.attempt.status, journal.STATUS_FAILED_PRE_SUBMIT)
        submit.assert_not_called()
        self.assertEqual(self.context.pages, [human])
        self.assertEqual(await human.locator('h1').inner_text(), 'Human tab')

    async def test_disabled_submit_is_a_pre_click_failure_not_an_uncertain_attempt(self):
        result, submit = await self.run_workflow('disabled')
        self.assertEqual(result.attempt.status, journal.STATUS_FAILED_PRE_SUBMIT, result.message)
        self.assertIn('按钮', result.message)
        submit.assert_not_called()
        self.assertFalse(any(row['status'] == journal.STATUS_SUBMIT_AMBIGUOUS
                             for row in journal.load(self.directory/'state')))

    async def test_button_wait_finishes_before_final_caption_verification(self):
        result, submit = await self.run_workflow('enable_later')
        self.assertEqual(result.attempt.status, journal.STATUS_SUBMIT_AMBIGUOUS, result.message)
        self.assertEqual(submit.await_count, 1)
        result, submit = await self.run_workflow('change_while_disabled')
        self.assertEqual(result.attempt.status, journal.STATUS_FAILED_PRE_SUBMIT, result.message)
        self.assertIn('正文', result.message)
        submit.assert_not_called()

    async def test_preview_url_changes_do_not_block_the_frozen_upload(self):
        result, submit = await self.run_workflow('preview_url')
        self.assertEqual(result.attempt.status, journal.STATUS_SUBMIT_AMBIGUOUS, result.message)
        self.assertEqual(submit.await_count, 1)

    async def test_date_display_format_changes_do_not_block_the_same_schedule(self):
        result, submit = await self.run_workflow('formatted_date')
        self.assertEqual(result.attempt.status, journal.STATUS_SUBMIT_AMBIGUOUS, result.message)
        self.assertEqual(submit.await_count, 1)

    async def schedule_editor(self, *, native_inputs=False, wrong_date_on_blur=False,
                              transient_inputs=False):
        await self.page.set_content(HTML)
        await self.page.evaluate('''({nativeInputs, wrongDate, transientInputs}) => {
            const date = document.querySelector('[aria-label="Date picker"]');
            date.onblur = () => {
                const parsed = /^(\\d{1,2})\\/(\\d{1,2})\\/(\\d{4})$/.exec(date.value);
                if (parsed) date.value = new Date(+parsed[3], +parsed[1]-1, +parsed[2])
                    .toLocaleDateString('en-US', {month:'short', day:'numeric', year:'numeric'});
                if (wrongDate) date.value = 'Oct 1, 2026';
            };
            const group = document.querySelector('[role="application"]');
            const inputs = [...group.querySelectorAll('input')];
            const shown = group.querySelector('span');
            if (nativeInputs) shown.remove();
            else if (transientInputs) {
                const committed = inputs.map(input => input.value);
                inputs.forEach((input, index) => {
                    input.oninput = () => {
                        if (input.value) committed[index] = input.value;
                        shown.textContent = committed[0] + ' : ' + committed[1] + ' ' + committed[2];
                    };
                    input.onblur = () => input.value = '';
                });
            }
            else {
                const render = () => shown.textContent = inputs[0].value + ' : ' + inputs[1].value + ' ' + inputs[2].value;
                inputs.forEach(input => input.oninput = render);
            }
        }''', {'nativeInputs': native_inputs, 'wrongDate': wrong_date_on_blur,
               'transientInputs': transient_inputs})

    async def test_committed_time_remains_readable_after_editing_buffers_clear_on_blur(self):
        await self.schedule_editor(transient_inputs=True)
        when = datetime.fromisoformat('2026-09-30T23:00:00+08:00')
        with patch.object(bs, 'assert_page_usable', AsyncMock()):
            readback = await bs.set_schedule(self.page, when, ui_timezone='Asia/Shanghai',
                verify_device=False, target_channels=('facebook',), timeout=1)
            self.assertIn('11 : 00 PM', readback)
            self.assertEqual(await self.page.get_by_role('spinbutton').evaluate_all('items=>items.map(i=>i.value)'),
                             ['', '', ''])
            await bs.verify_form(self.page, TEXT, when, ui_timezone='Asia/Shanghai', timeout=1)

    async def test_transient_clock_uses_real_g5_and_final_read_before_single_submit(self):
        for change in ('none', 'display_time', 'time'):
            with self.subTest(change=change):
                result, submit = await self.run_workflow(change, transient_schedule=True)
                if change == 'none':
                    self.assertEqual(result.attempt.status, journal.STATUS_SUBMIT_AMBIGUOUS, result.message)
                    self.assertEqual(submit.await_count, 1)
                else:
                    self.assertEqual(result.attempt.status, journal.STATUS_FAILED_PRE_SUBMIT, result.message)
                    self.assertEqual(result.attempt.step, '提交前最终表单复核')
                    self.assertIn('时刻', result.message)
                    submit.assert_not_called()

    async def test_empty_editing_fields_require_one_exact_committed_clock(self):
        await self.page.set_content(HTML)
        await self.page.get_by_role('spinbutton').evaluate_all('items=>items.forEach(i=>i.value="")')
        shown = self.page.locator('[role="application"] span')
        with patch.object(bs, 'assert_page_usable', AsyncMock()):
            for rendered in ('', 'unknown', '12 : 31 PM', '12 : 30 AM',
                             '12:30 PM / 11:30 PM', 'Time: 12:30 PM', '112:30 PM'):
                with self.subTest(rendered=rendered):
                    await shown.evaluate('(el, text)=>el.textContent=text', rendered)
                    with self.assertRaisesRegex(bs.PublishStepError, '时刻'):
                        await bs.verify_form(self.page, TEXT, WHEN, ui_timezone='UTC', timeout=1)

    async def test_visible_clock_does_not_override_nonempty_conflicting_fields(self):
        await self.page.set_content(HTML)
        with patch.object(bs, 'assert_page_usable', AsyncMock()):
            for values in (['11', '', ''], ['', '31', ''], ['', '', 'AM'], ['11', '30', 'PM']):
                with self.subTest(values=values):
                    await self.page.get_by_role('spinbutton').evaluate_all(
                        '(items, values)=>items.forEach((item, i)=>item.value=values[i])', values)
                    with self.assertRaisesRegex(bs.PublishStepError, '时刻'):
                        await bs.verify_form(self.page, TEXT, WHEN, ui_timezone='UTC', timeout=1)

    async def test_set_and_verify_same_instant_after_blur_and_timezone_conversion(self):
        cases = (
            ('2026-09-30T23:00:00+08:00', 'Asia/Shanghai', 'Sep 30, 2026', '11', '00', 'PM'),
            ('2026-10-01T00:15:00+08:00', 'Asia/Shanghai', 'Oct 1, 2026', '12', '15', 'AM'),
            ('2026-10-01T00:15:00+08:00', 'America/Los_Angeles', 'Sep 30, 2026', '9', '15', 'AM'),
            ('2026-12-31T18:00:00+00:00', 'Asia/Shanghai', 'Jan 1, 2027', '2', '00', 'AM'),
            ('2026-09-30T12:00:00+08:00', 'Asia/Shanghai', 'Sep 30, 2026', '12', '00', 'PM'),
        )
        with patch.object(bs, 'assert_page_usable', AsyncMock()):
            for moment, zone, day, hour, minute, meridiem in cases:
                with self.subTest(moment=moment, zone=zone):
                    await self.schedule_editor()
                    when = datetime.fromisoformat(moment)
                    readback = await bs.set_schedule(self.page, when, ui_timezone=zone,
                        verify_device=False, target_channels=('facebook',), timeout=1)
                    self.assertIn(day, readback)
                    self.assertEqual(await self.page.get_by_role('textbox', name='Date picker').input_value(), day)
                    self.assertEqual(await self.page.get_by_role('spinbutton').evaluate_all('items=>items.map(i=>i.value)'),
                                     [hour, minute, meridiem])
                    self.assertFalse(await self.page.get_by_role('spinbutton', name='meridiem').evaluate('el=>el===document.activeElement'))
                    await bs.verify_form(self.page, TEXT, when, ui_timezone=zone, timeout=1)

    async def test_native_time_values_do_not_require_duplicate_container_text(self):
        await self.schedule_editor(native_inputs=True)
        when = datetime.fromisoformat('2026-09-30T23:00:00+08:00')
        with patch.object(bs, 'assert_page_usable', AsyncMock()):
            await bs.set_schedule(self.page, when, ui_timezone='Asia/Shanghai', verify_device=False, timeout=1)
            await bs.verify_form(self.page, TEXT, when, ui_timezone='Asia/Shanghai', timeout=1)
            await self.page.get_by_role('spinbutton', name='minutes').fill('0')
            await bs.verify_form(self.page, TEXT, when, ui_timezone='Asia/Shanghai', timeout=1)
            await self.page.get_by_role('spinbutton', name='meridiem').fill('AM')
            with self.assertRaisesRegex(bs.PublishStepError, '时刻'):
                await bs.verify_form(self.page, TEXT, when, ui_timezone='Asia/Shanghai', timeout=1)

    async def test_editable_time_segments_normalize_spaces_padding_and_case(self):
        await self.page.set_content(HTML)
        await self.page.get_by_role('spinbutton').evaluate_all('''items => items.forEach((old, index) => {
            const field = document.createElement('div');
            field.contentEditable = 'true'; field.setAttribute('role', 'spinbutton');
            if (old.hasAttribute('aria-label')) field.setAttribute('aria-label', old.getAttribute('aria-label'));
            field.textContent = ['\\u200e12', ' 30 ', ' pm '][index]; old.replaceWith(field);
        })''')
        with patch.object(bs, 'assert_page_usable', AsyncMock()):
            await bs.verify_form(self.page, TEXT, WHEN, ui_timezone='UTC', timeout=1)
            for rendered in ('11:30 PM', 'Time: 11:30 PM', '12:30 PM / 11:30 PM'):
                with self.subTest(rendered=rendered):
                    await self.page.locator('[role="application"] span').evaluate('(el, text)=>el.textContent=text', rendered)
                    with self.assertRaisesRegex(bs.PublishStepError, '时刻'):
                        await bs.verify_form(self.page, TEXT, WHEN, ui_timezone='UTC', timeout=1)
            await self.page.locator('[role="application"] span').evaluate('el=>el.textContent="12:30 PM"')
            await self.page.get_by_role('spinbutton', name='meridiem').fill('AM')
            with self.assertRaisesRegex(bs.PublishStepError, '时刻'):
                await bs.verify_form(self.page, TEXT, WHEN, ui_timezone='UTC', timeout=1)

    async def test_date_reversion_on_blur_is_caught_during_initial_fill(self):
        await self.schedule_editor(wrong_date_on_blur=True)
        with patch.object(bs, 'assert_page_usable', AsyncMock()):
            with self.assertRaisesRegex(bs.PublishStepError, '日期'):
                await bs.set_schedule(self.page, datetime.fromisoformat('2026-09-30T23:00:00+08:00'),
                    ui_timezone='Asia/Shanghai', verify_device=False, timeout=1)

    async def test_final_date_formats_and_diagnostics(self):
        when = datetime.fromisoformat('2026-09-30T23:00:00+08:00')
        await self.page.set_content(HTML)
        await self.page.get_by_role('spinbutton').first.fill('11')
        await self.page.get_by_role('spinbutton', name='minutes').fill('00')
        await self.page.locator('[role="application"] span').evaluate('el=>el.textContent="11 : 00 PM"')
        dates = self.page.get_by_role('textbox', name='Date picker')
        with patch.object(bs, 'assert_page_usable', AsyncMock()):
            for shown in ('9/30/2026', '09/30/2026', 'Sep 30, 2026', 'September 30, 2026',
                          '2026-09-30', '\u200eSep\u00a030,\u202f2026\u200b'):
                with self.subTest(shown=shown):
                    await dates.fill(shown)
                    await bs.verify_form(self.page, TEXT, when, ui_timezone='Asia/Shanghai', timeout=1)
            for shown, reason in (('Oct 1, 2026', '不一致'), ('Sep 30, 2027', '不一致'),
                                  ('30/09/2026', '无法识别'), ('unknown', '无法识别'),
                                  ('Sep 31, 2026', '无法识别'), ('9/30/2026 changed', '无法识别')):
                with self.subTest(shown=shown):
                    await dates.fill(shown)
                    with self.assertRaises(bs.PublishStepError) as raised:
                        await bs.verify_form(self.page, TEXT, when, ui_timezone='Asia/Shanghai', timeout=1)
                    for detail in ('2026-09-30', 'Asia/Shanghai', shown, reason, '未提交'):
                        self.assertIn(detail, str(raised.exception))


class FixtureCleanupTests(unittest.TestCase):
    def test_setup_failure_releases_every_resource_already_acquired(self):
        for phase in ('launch', 'context', 'page'):
            with self.subTest(phase=phase):
                released = []
                context = SimpleNamespace(new_page=AsyncMock(side_effect=RuntimeError('fixture page failed')),
                    close=AsyncMock(side_effect=lambda: released.append('context')))
                browser = SimpleNamespace(new_context=AsyncMock(return_value=context),
                    close=AsyncMock(side_effect=lambda: released.append('browser')))
                pw = SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)),
                    stop=AsyncMock(side_effect=lambda: released.append('driver')))
                if phase == 'launch':
                    pw.chromium.launch.side_effect = RuntimeError('fixture launch failed')
                elif phase == 'context':
                    browser.new_context.side_effect = RuntimeError('fixture context failed')
                manager = SimpleNamespace(start=AsyncMock(return_value=pw))
                result = unittest.TestResult()
                with patch(f'{__name__}.async_playwright', return_value=manager):
                    case = FinalFormTests('test_unchanged_form_arms_durably_and_submits_once')
                    case.run(result)
                self.assertEqual(len(result.errors), 1)
                self.assertIn(f'fixture {phase} failed', result.errors[0][1])
                expected = {'launch': ['driver'], 'context': ['browser', 'driver'],
                            'page': ['context', 'browser', 'driver']}[phase]
                self.assertEqual(released, expected)
                self.assertFalse(case.directory.exists())


if __name__ == '__main__':
    unittest.main()
