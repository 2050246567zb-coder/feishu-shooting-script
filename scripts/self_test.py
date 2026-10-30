"""Offline regression tests. Uses a fake CLI; never contacts Feishu."""
import copy
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import extract_docx
import feishu_shots as flow
import install_skill


class FakeCli:
    def __init__(self, cwd):
        self.cwd, self.fields, self.rows, self.views = Path(cwd), [], [], []
        self.config, self.batch_sizes = {}, []
        self.create_count, self.upload_count = 0, 0
        self.partial_failure, self.failure_after_write = None, False
        self.fail_creation = False

    def call(self, args, mutate=False, user=True):
        if args[:2] == ['auth', 'status']:
            return {'identities': {'user': {'available': True, 'status': 'ready', 'tokenStatus': 'valid'}}}
        command, rest = args[1], args[2:]
        def value(flag, default=None):
            return rest[rest.index(flag)+1] if flag in rest else default
        def ok(data):
            return {'ok': True, 'identity': 'user', 'data': data}
        if command in ('+base-create', '+base-copy'):
            self.create_count += 1
            fields = json.loads(value('--fields')) if command == '+base-create' else flow.read_json(flow.SKILL_DIR/'assets/template.json')['fields']
            self.fields = [{**copy.deepcopy(f), 'id': f'field_{i}'} for i, f in enumerate(fields)]
            self.views = [{'id': 'view_0', 'name': '表格', 'type': 'grid'}]
            if self.fail_creation:
                raise flow.CliError({'type': 'timeout', 'message': 'unknown creation outcome'})
            return ok({'base': {'base_token': 'fake_base', 'url': 'https://example.invalid/base/fake_base'}})
        if command == '+table-list':
            return ok({'tables': [{'id': 'fake_table', 'name': '镜头清单', 'records_count': len(self.rows)}]})
        if command == '+field-list':
            return ok({'fields': copy.deepcopy(self.fields)})
        if command == '+field-get':
            return ok({'field': copy.deepcopy(next(f for f in self.fields if f['id'] == value('--field-id')))})
        if command == '+field-update':
            if '--dry-run' not in rest:
                field_id = value('--field-id')
                updated = {**json.loads(value('--json')), 'id': field_id}
                self.fields = [updated if f['id'] == field_id else f for f in self.fields]
            return ok({'updated': True})
        if command == '+view-list':
            return ok({'views': copy.deepcopy(self.views)})
        if command == '+view-rename':
            next(v for v in self.views if v['id'] == value('--view-id'))['name'] = value('--name')
            return ok({'updated': True})
        if command == '+view-create':
            spec = json.loads(value('--json'))
            self.views.append({**spec, 'id': f'view_{len(self.views)}'})
            return ok({'created': True})
        if command.startswith('+view-set-') or command.startswith('+view-get-'):
            view_ref = value('--view-id')
            view = next(v for v in self.views if view_ref in (v['id'], v['name']))
            key = command.split('-', 2)[2]
            config_key = (view['id'], key)
            if command.startswith('+view-set-'):
                self.config[config_key] = json.loads(value('--json'))
                return ok({'updated': True})
            current = copy.deepcopy(self.config[config_key])
            if key == 'visible-fields':
                return ok(current)
            if key in ('sort', 'group'):
                return ok({key: current[key+'_config']})
            return ok({'filter': current})
        if command == '+record-list':
            offset, limit = int(value('--offset', 0)), int(value('--limit', 2000))
            rows = self.rows[offset:offset+limit]
            (self.cwd/value('--output')).write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in rows), encoding='utf-8')
            return {'records_count': len(rows), 'has_more': offset+limit < len(self.rows)}
        if command == '+record-batch-create':
            payload = flow.read_json(self.cwd/value('--json')[1:])['create_records']
            self.batch_sizes.append(len(payload))
            reverse = {f['id']: f['name'] for f in self.fields}
            count = self.partial_failure if self.partial_failure is not None else len(payload)
            for record in payload[:count]:
                if not all(key in reverse for key in record):
                    raise AssertionError('Record writes must use real field IDs')
                row = {reverse[key]: val for key, val in record.items()}
                row.update(record_id=f'record_{len(self.rows)}', 画面参考=[])
                self.rows.append(row)
            if self.partial_failure is not None:
                self.partial_failure = None
                raise flow.CliError({'type': 'timeout', 'message': 'partially written'})
            if self.failure_after_write:
                self.failure_after_write = False
                raise flow.CliError({'type': 'timeout', 'message': 'response lost after write'})
            return ok({'record_id_list': [r['record_id'] for r in self.rows[-len(payload):]]})
        if command == '+record-upload-attachment':
            self.upload_count += 1
            file = self.cwd/value('--file')
            row = next(r for r in self.rows if r['record_id'] == value('--record-id'))
            row['画面参考'].append({'file_token': f'file_{self.upload_count}', 'name': file.name, 'size': file.stat().st_size})
            return ok({'uploaded': True})
        raise AssertionError(f'Unexpected CLI operation: {args}')


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.work = self.root/'job'
        self.work.mkdir()
        self.cli = FakeCli(self.work)
        self.sleep = patch('feishu_shots.time.sleep')
        self.sleep.start()

    def tearDown(self):
        self.sleep.stop()
        self.temp.cleanup()

    def plan(self, count=2, **changes):
        raw = {'title': '虚拟测试拍摄协作脚本', 'expected_shots': count, 'shots': [
            {'id': f'{i:04d}', 'dialogue': f'原文台词{i}。“所以……”', 'description': f'镜头{i}画面', 'location': '待定'}
            for i in range(1, count+1)]}
        raw.update(changes)
        flow.write_json(self.root/'normalized.json', raw)
        return flow.load_plan(self.root/'normalized.json')

    def publisher(self, plan, template=None):
        return flow.Publisher(plan, self.work, self.cli, template)

    def test_end_to_end_new_base_and_views(self):
        report = self.publisher(self.plan()).run()
        self.assertEqual(report['shots'], 2)
        self.assertEqual(report['created_this_run'], 2)
        self.assertEqual(self.cli.create_count, 1)
        self.assertEqual(len(self.cli.views), 3)
        self.assertTrue(all(r['已拍摄'] is False for r in self.cli.rows))
        self.assertEqual([f['name'] for f in self.cli.fields], flow.COLUMNS)
        pending = next(v for v in self.cli.views if v['name'] == '待拍镜头')
        self.assertEqual(self.cli.config[(pending['id'], 'filter')]['conditions'], [['field_6', '==', False]])

    def test_resume_preserves_checkbox_location_notes_and_reference(self):
        plan = self.plan()
        self.publisher(plan).run()
        row = self.cli.rows[0]
        row.update(已拍摄=True, 拍摄地点=['用户现场地点'], 拍摄备注='现场补充')
        row['画面参考'].append({'file_token': 'manual', 'name': '现场图.png', 'size': 10})
        report = self.publisher(plan).run()
        self.assertEqual(len(self.cli.rows), 2)
        self.assertEqual(self.cli.create_count, 1)
        self.assertEqual(report['created_this_run'], 0)
        self.assertEqual(report['checked_shots'], 1)
        self.assertEqual(row['拍摄备注'], '现场补充')
        self.assertEqual(row['画面参考'][0]['file_token'], 'manual')

    def test_batch_limit_200(self):
        report = self.publisher(self.plan(401)).run()
        self.assertEqual(report['shots'], 401)
        self.assertEqual(self.cli.batch_sizes, [200, 200, 1])

    def test_lost_response_after_write_is_reconciled(self):
        self.cli.failure_after_write = True
        self.publisher(self.plan()).run()
        self.assertEqual(self.cli.batch_sizes, [2])
        self.assertEqual(len(self.cli.rows), 2)

    def test_partial_batch_resume_only_inserts_missing(self):
        plan = self.plan(5)
        self.cli.partial_failure = 2
        with self.assertRaises(flow.CliError):
            self.publisher(plan).run()
        self.assertEqual(len(self.cli.rows), 2)
        self.publisher(plan).run()
        self.assertEqual(self.cli.batch_sizes, [5, 3])
        self.assertEqual(len(self.cli.rows), 5)

    def test_unknown_creation_does_not_create_twice(self):
        plan = self.plan()
        self.cli.fail_creation = True
        with self.assertRaises(flow.CliError):
            self.publisher(plan).run()
        with self.assertRaisesRegex(flow.WorkflowError, 'creation outcome is unknown'):
            self.publisher(plan).run()
        self.assertEqual(self.cli.create_count, 1)

    def test_rejected_creation_can_resume_after_permission_fix(self):
        plan = self.plan()
        original = self.cli.call
        def rejected(args, **kwargs):
            if args[1] == '+base-create':
                raise flow.CliError({'type': 'authorization', 'message': 'missing scope'})
            return original(args, **kwargs)
        self.cli.call = rejected
        with self.assertRaises(flow.CliError):
            self.publisher(plan).run()
        self.assertFalse(flow.read_json(self.work/'state.json')['creation_pending'])
        self.cli.call = original
        self.publisher(plan).run()
        self.assertEqual(self.cli.create_count, 1)

    def test_conflicting_online_text_is_preserved(self):
        plan = self.plan()
        self.publisher(plan).run()
        self.cli.rows[0]['台词／文案'] = '用户修改后的台词'
        with self.assertRaisesRegex(flow.WorkflowError, 'differs online'):
            self.publisher(plan).run()
        self.assertEqual(self.cli.rows[0]['台词／文案'], '用户修改后的台词')

    def test_duplicate_online_id_stops(self):
        plan = self.plan()
        self.publisher(plan).run()
        self.cli.rows.append(copy.deepcopy(self.cli.rows[0]))
        with self.assertRaisesRegex(flow.WorkflowError, 'Duplicate shot IDs'):
            self.publisher(plan).run()

    def test_changed_plan_cannot_reuse_job(self):
        self.publisher(self.plan()).run()
        with self.assertRaisesRegex(flow.WorkflowError, 'different plan'):
            self.publisher(self.plan(3))

    def test_template_copy_adds_explicit_location(self):
        shots = [{'id': '001', 'dialogue': '你好', 'description': '在会议室拍摄', 'location': '会议室', 'location_basis': '原文明确为会议室'}]
        plan = self.plan(1, shots=shots)
        self.publisher(plan, template='provided_user_template').run()
        self.assertIn('会议室', [o['name'] for o in self.cli.fields[4]['options']])
        self.assertEqual(self.cli.rows[0]['拍摄地点'], ['会议室'])

    def test_attachment_upload_and_resume_do_not_duplicate(self):
        image = self.root/'参考图.png'
        image.write_bytes(b'fake-reference-image')
        plan = self.plan(1, shots=[{'id': '001', 'description': '纯画面镜头', 'reference_files': [str(image)]}])
        report = self.publisher(plan).run()
        self.assertEqual(report['verified_reference_files'], 1)
        self.publisher(plan).run()
        self.assertEqual(self.cli.upload_count, 1)

    def test_auth_missing_creates_nothing(self):
        original = self.cli.call
        def no_auth(args, **kwargs):
            return {'identities': {'user': {'available': False, 'status': 'missing'}}} if args[:2] == ['auth', 'status'] else original(args, **kwargs)
        self.cli.call = no_auth
        with self.assertRaisesRegex(flow.WorkflowError, 'auth_required'):
            self.publisher(self.plan()).run()
        self.assertEqual(self.cli.create_count, 0)

    def test_invalid_plans_fail_before_writes(self):
        for shots in (
            [{'id': '001', 'dialogue': ''}],
            [{'id': '001', 'dialogue': 'a'}, {'id': '001', 'dialogue': 'b'}],
            [{'id': '2', 'dialogue': 'a'}, {'id': '10', 'dialogue': 'b'}],
            [{'id': '001', 'dialogue': 'a', 'location': '凭空猜的地点'}],
            [{'id': '001', 'dialogue': 'a', 'reference_files': ['does-not-exist.png']}],
        ):
            with self.subTest(shots=shots), self.assertRaises(flow.WorkflowError):
                self.plan(len(shots), shots=shots)
        self.assertEqual(self.cli.create_count, 0)

    def test_source_count_mismatch_stops(self):
        with self.assertRaisesRegex(flow.WorkflowError, 'expected_shots'):
            self.plan(2, expected_shots=99)

    def test_failed_view_verification_stops_before_records(self):
        original = self.cli.call
        def wrong_visible(args, **kwargs):
            response = original(args, **kwargs)
            if args[1] == '+view-get-visible-fields':
                response['data']['visible_fields'] = list(reversed(response['data']['visible_fields']))
            return response
        self.cli.call = wrong_visible
        with self.assertRaisesRegex(flow.WorkflowError, 'column order was not verified'):
            self.publisher(self.plan()).run()
        self.assertEqual(self.cli.rows, [])

    def test_docx_merge_revision_image_and_no_semantic_guessing(self):
        document = '''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:body>
        <w:p><w:r><w:t>视频脚本</w:t></w:r><w:del><w:r><w:delText>旧版本</w:delText></w:r></w:del><w:ins><w:r><w:t>新版</w:t></w:r></w:ins></w:p>
        <w:tbl><w:tr><w:tc><w:tcPr><w:vMerge w:val="restart"/></w:tcPr><w:p><w:r><w:t>整段30s</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>所以……</w:t></w:r><w:r><w:drawing><a:blip r:embed="rId1"/></w:drawing></w:r></w:p></w:tc></w:tr>
        <w:tr><w:tc><w:tcPr><w:vMerge/></w:tcPr><w:p/></w:tc><w:tc><w:p><w:r><w:t>下一镜头</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>'''
        source = self.root/'source.docx'
        with zipfile.ZipFile(source, 'w') as archive:
            archive.writestr('word/document.xml', document)
            archive.writestr('word/_rels/document.xml.rels', '<Relationships><Relationship Id="rId1" Type="test/image" Target="media/image.png"/></Relationships>')
            archive.writestr('word/media/image.png', b'fake-image')
        output = self.root/'extracted'
        extract_docx.extract(source, output)
        data = flow.read_json(output/'extracted.json')
        self.assertEqual(data['blocks'][0]['text'], '视频脚本新版')
        table = data['blocks'][1]
        self.assertEqual(table['rows'][1]['cells'][0]['text'], '')
        self.assertEqual(table['rows'][1]['cells'][0]['merged_from'], {'block': 1, 'row': 0, 'cell': 0})
        self.assertEqual(table['rows'][0]['cells'][1]['text'], '所以……')
        self.assertTrue((output/table['rows'][0]['cells'][1]['images'][0]).is_file())

    def test_installer_and_replacement_backup(self):
        destination = self.root/'installation/skills'
        first = install_skill.install(destination)
        target = Path(first['path'])
        self.assertTrue((target/'SKILL.md').is_file())
        self.assertFalse(list(target.rglob('*.pyc')))
        (target/'old.marker').write_text('old installation', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            install_skill.install(destination)
        replaced = install_skill.install(destination, replace=True)
        self.assertTrue((Path(replaced['backup'])/'old.marker').is_file())
        self.assertFalse((target/'old.marker').exists())

    def test_installer_rejects_destination_within_itself(self):
        with self.assertRaisesRegex(ValueError, 'inside the source'):
            install_skill.install(flow.SKILL_DIR/'test-destination', dry_run=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
