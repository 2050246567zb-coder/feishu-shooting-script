"""Validate a shot plan and publish it to a new user-owned Feishu Base.

Uses the installed lark-cli. No network calls occur for the validate command.
"""
import argparse
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
COLUMNS = ['镜号', '台词／文案', '镜头描述', '画面参考', '拍摄地点', '拍摄备注', '已拍摄']
TYPES = ['text', 'text', 'text', 'attachment', 'select', 'text', 'checkbox']


class WorkflowError(Exception):
    pass


class CliError(WorkflowError):
    def __init__(self, error):
        self.error = error
        super().__init__(json.dumps(error, ensure_ascii=False))


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name+'.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, path)


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def load_plan(path):
    path = Path(path).resolve()
    raw = read_json(path)
    if not isinstance(raw, dict) or not isinstance(raw.get('title'), str) or not raw['title'].strip():
        raise WorkflowError('Plan must contain a non-empty title and a shots array')
    unknown = set(raw)-{'title', 'shots', 'expected_shots', 'source_files', 'mapping_notes'}
    if unknown:
        raise WorkflowError('Unknown plan keys: '+', '.join(sorted(unknown)))
    if not isinstance(raw.get('shots'), list) or not raw['shots']:
        raise WorkflowError('shots must be a non-empty array')
    if 'expected_shots' in raw and (type(raw['expected_shots']) is not int or raw['expected_shots'] != len(raw['shots'])):
        raise WorkflowError('expected_shots does not match the number of shots')
    shots = []
    allowed = {'id', 'dialogue', 'description', 'location', 'location_basis', 'notes', 'reference_files', 'source_refs'}
    for position, item in enumerate(raw['shots'], 1):
        if not isinstance(item, dict) or set(item)-allowed:
            raise WorkflowError(f'Invalid keys or shot object at position {position}')
        shot = {key: item.get(key, default) for key, default in (
            ('id', ''), ('dialogue', ''), ('description', ''), ('location', '待定'), ('location_basis', ''), ('notes', ''))}
        if not all(isinstance(value, str) for value in shot.values()):
            raise WorkflowError(f'Shot {position}: text fields must be strings')
        if not shot['id'].strip() or '\n' in shot['id'] or '\x00' in ''.join(shot.values()):
            raise WorkflowError(f'Shot {position}: invalid ID or NUL in text')
        if not (shot['dialogue'].strip() or shot['description'].strip()):
            raise WorkflowError(f"Shot {shot['id']}: dialogue and description cannot both be empty")
        if not shot['location'].strip():
            shot['location'] = '待定'
        if shot['location'] != '待定' and not shot['location_basis'].strip():
            raise WorkflowError(f"Shot {shot['id']}: a specific location needs location_basis from the source or user")
        references = item.get('reference_files', [])
        if not isinstance(references, list) or not all(isinstance(p, str) and p for p in references):
            raise WorkflowError(f"Shot {shot['id']}: reference_files must be a path array")
        files, seen = [], set()
        for reference in references:
            resolved = Path(reference)
            resolved = (path.parent/resolved).resolve() if not resolved.is_absolute() else resolved.resolve()
            if not resolved.is_file():
                raise WorkflowError(f"Shot {shot['id']}: reference file does not exist: {resolved}")
            if resolved in seen:
                raise WorkflowError(f"Shot {shot['id']}: duplicate reference file")
            seen.add(resolved)
            files.append({'path': str(resolved), 'name': resolved.name, 'size': resolved.stat().st_size, 'sha256': file_hash(resolved)})
        shot['reference_files'] = files
        shot['source_refs'] = item.get('source_refs', [])
        if not isinstance(shot['source_refs'], list) or not all(isinstance(s, str) for s in shot['source_refs']):
            raise WorkflowError(f"Shot {shot['id']}: source_refs must be an array of strings")
        shots.append(shot)
    ids = [shot['id'] for shot in shots]
    if len(set(ids)) != len(ids):
        raise WorkflowError('Duplicate shot IDs in plan')
    if ids != sorted(ids):
        raise WorkflowError('Shot IDs must sort in script order; use zero-padded numbers such as 001, 002, 010')
    canonical = {'title': raw['title'], 'shots': copy.deepcopy(shots)}
    for shot in canonical['shots']:
        for reference in shot['reference_files']:
            reference.pop('path')
    fingerprint = hashlib.sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
    return {'title': raw['title'], 'shots': shots, 'fingerprint': fingerprint}


def schema_for(plan):
    template = read_json(SKILL_DIR/'assets/template.json')
    fields = copy.deepcopy(template['fields'])
    locations = list(dict.fromkeys(['待定']+[s['location'] for s in plan['shots']]))
    fields[4]['options'] = [{'name': name, 'hue': 'Gray' if name == '待定' else 'Blue', 'lightness': 'Lighter'} for name in locations]
    return template, fields


def cli_entry(explicit=None):
    candidate = explicit or shutil.which('lark-cli') or shutil.which('lark-cli.cmd')
    if not candidate:
        raise WorkflowError('lark-cli not found; configure the Feishu CLI first')
    path = Path(candidate).resolve()
    if path.suffix.lower() in ('.cmd', '.bat', '.ps1'):
        entry = path.parent/'node_modules/@larksuite/cli/scripts/run.js'
        node = shutil.which('node')
        if entry.is_file() and node:
            return [node, str(entry)]
        raise WorkflowError('Cannot safely resolve this CLI shim; provide a real executable or the npm run.js entry')
    if path.suffix.lower() == '.js':
        node = shutil.which('node')
        if not node:
            raise WorkflowError('Node.js is required for this CLI entry')
        return [node, str(path)]
    return [str(path)]


class RealCli:
    def __init__(self, cwd, entry=None):
        self.cwd, self.entry = Path(cwd), cli_entry(entry)
        self.last_write = 0.0

    def call(self, args, mutate=False, user=True):
        if mutate:
            delay = 2.1-(time.monotonic()-self.last_write)
            if delay > 0:
                time.sleep(delay)
            self.last_write = time.monotonic()
        command = self.entry+list(args)+(['--as', 'user'] if user else [])
        env = {**os.environ, 'LARKSUITE_CLI_NO_UPDATE_NOTIFIER': '1', 'LARKSUITE_CLI_NO_SKILLS_NOTIFIER': '1'}
        try:
            result = subprocess.run(command, cwd=self.cwd, env=env, capture_output=True, text=True,
                                    encoding='utf-8', errors='replace', timeout=90, shell=False)
        except subprocess.TimeoutExpired as exc:
            raise CliError({'type': 'timeout', 'message': 'CLI outcome unknown; inspect saved task state before retrying'}) from exc
        try:
            response = json.loads(result.stdout.lstrip('\ufeff')) if result.stdout.strip() else None
        except json.JSONDecodeError:
            response = None
        if result.returncode or (isinstance(response, dict) and response.get('ok') is False):
            try:
                failure = json.loads(result.stderr.lstrip('\ufeff'))
            except json.JSONDecodeError:
                failure = response or {'error': {'type': 'cli', 'message': result.stderr.strip()[:800] or 'CLI failed'}}
            raise CliError(failure.get('error', failure))
        if not isinstance(response, dict):
            raise CliError({'type': 'invalid_response', 'message': 'CLI did not return JSON; check the installed CLI command contract'})
        return response


class Publisher:
    def __init__(self, plan, work_dir, cli, template_token=None, table_name='镜头清单', time_zone='Asia/Shanghai'):
        self.plan, self.work = plan, Path(work_dir).resolve()
        self.work.mkdir(parents=True, exist_ok=True)
        self.cli, self.template_token, self.table_name, self.time_zone = cli, template_token, table_name, time_zone
        self.state_path = self.work/'state.json'
        self.state = read_json(self.state_path) if self.state_path.exists() else {'fingerprint': plan['fingerprint'], 'uploads': {}}
        if self.state.get('fingerprint') != plan['fingerprint']:
            raise WorkflowError('This task directory belongs to a different plan; do not overwrite or resume it with changed source')
        if 'template_token' in self.state and self.state['template_token'] != template_token:
            raise WorkflowError('Template selection changed for this task; reuse the original template option')
        self.state['template_token'] = template_token
        self.read_count, self.warnings = 0, []

    def save(self):
        write_json(self.state_path, self.state)

    def base(self, command, extra=(), mutate=False):
        return self.cli.call(['base', command, '--base-token', self.state['base_token'],
                              '--table-id', self.state['table_id'], *extra], mutate=mutate)

    def auth(self):
        status = self.cli.call(['auth', 'status', '--json', '--verify'], user=False)
        user = status.get('identities', {}).get('user', {})
        if not user.get('available') or user.get('status') != 'ready' or user.get('tokenStatus') == 'expired':
            raise WorkflowError('auth_required: personal user authorization is missing or expired; follow references/setup.md')

    def ensure_target(self):
        if not self.state.get('base_token'):
            if self.state.get('creation_pending'):
                raise WorkflowError('Base creation outcome is unknown. Inspect the user cloud drive and recover the created Base; do not create again blindly')
            self.state['creation_pending'] = True
            self.save()
            try:
                if self.template_token:
                    response = self.cli.call(['base', '+base-copy', '--base-token', self.template_token,
                                              '--name', self.plan['title'], '--without-content'], mutate=True)
                else:
                    _, fields = schema_for(self.plan)
                    response = self.cli.call(['base', '+base-create', '--name', self.plan['title'], '--table-name', self.table_name,
                                              '--time-zone', self.time_zone, '--fields', json.dumps(fields, ensure_ascii=False)], mutate=True)
            except CliError as exc:
                if exc.error.get('type') in ('authorization', 'validation', 'confirmation', 'argument'):
                    self.state['creation_pending'] = False
                    self.save()
                raise
            base = response.get('data', {}).get('base', {})
            if not base.get('base_token'):
                raise WorkflowError('Creation returned no Base Token; keep pending state and inspect the result')
            self.state.update(base_token=base['base_token'], base_url=base.get('url'), creation_pending=False)
            self.save()
        if not self.state.get('table_id'):
            response = self.cli.call(['base', '+table-list', '--base-token', self.state['base_token']])
            matches = [t for t in response['data']['tables'] if t['name'] == self.table_name]
            if len(matches) != 1:
                raise WorkflowError('Cannot uniquely identify the shooting table in the created Base; preserve the copy and select the correct table')
            self.state['table_id'] = matches[0]['id']
            self.save()
        if not self.state.get('base_url'):
            info = self.cli.call(['base', '+base-get', '--base-token', self.state['base_token']])['data']
            self.state['base_url'] = info.get('base', info).get('url')
            self.save()
            if not self.state['base_url']:
                raise WorkflowError('The Base exists but no result URL was returned; recover its URL without creating again')

    def ensure_fields(self):
        result = self.base('+field-list')
        names = [f['name'] for f in result['data']['fields']]
        if any(names.count(name) != 1 for name in COLUMNS):
            raise WorkflowError('The template does not contain one compatible instance of each of the seven required columns')
        fields = {f['name']: f for f in result['data']['fields']}
        if any(fields[name]['type'] != kind for name, kind in zip(COLUMNS, TYPES)) or fields['拍摄地点'].get('multiple', False):
            raise WorkflowError('Template field types are incompatible; no automatic type conversion was attempted')
        location = fields['拍摄地点']
        desired = list(dict.fromkeys(['待定']+[s['location'] for s in self.plan['shots']]))
        existing = {o['name'] for o in location.get('options', [])}
        missing = [name for name in desired if name not in existing]
        if missing:
            if self.records():
                raise WorkflowError('Location options are missing on a non-empty resumed table; do not silently change an active production schema')
            location = self.base('+field-get', ['--field-id', location['id']])['data']['field']
            if location.get('dynamic_options_source'):
                raise WorkflowError('Dynamic location options need explicit handling; do not replace the template option source')
            body = {k: v for k, v in location.items() if k in ('name', 'type', 'multiple', 'options', 'default_value', 'description', 'style')}
            body.setdefault('options', []).extend({'name': name, 'hue': 'Blue', 'lightness': 'Lighter'} for name in missing)
            encoded = json.dumps(body, ensure_ascii=False)
            self.base('+field-update', ['--field-id', location['id'], '--json', encoded, '--dry-run'])
            self.base('+field-update', ['--field-id', location['id'], '--json', encoded, '--yes'], mutate=True)
        self.fields = {name: fields[name]['id'] for name in COLUMNS}

    def records(self):
        self.read_count += 1
        offset, rows = 0, []
        while True:
            name = f'records-{self.read_count}-{offset}.ndjson'
            projection = []
            for field in COLUMNS:
                projection += ['--field-id', field]
            manifest = self.base('+record-list', [*projection, '--format', 'ndjson', '--limit', '2000', '--offset', str(offset),
                                                  '--output', name, '--overwrite'])
            page = [json.loads(line) for line in (self.work/name).read_text(encoding='utf-8-sig').splitlines() if line]
            rows.extend(page)
            if not manifest.get('has_more'):
                break
            if not page:
                raise WorkflowError('Record pagination returned no rows while has_more=true')
            offset += len(page)
        return rows

    def index(self, rows):
        indexed = {}
        expected = {s['id']: s for s in self.plan['shots']}
        for row in rows:
            shot_id = row.get('镜号')
            if shot_id not in expected:
                raise WorkflowError('Unexpected existing rows in this task table; do not mix scripts or overwrite user data')
            if shot_id in indexed:
                raise WorkflowError('Duplicate shot IDs found online; do not insert more records')
            for target, source in [('台词／文案', 'dialogue'), ('镜头描述', 'description')]:
                if (row.get(target) or '') != expected[shot_id][source]:
                    raise WorkflowError(f'Shot {shot_id} differs online in {target}; preserve the edit and resolve it explicitly')
            indexed[shot_id] = row
        return indexed

    def config_write(self, command, extra):
        try:
            return self.base(command, extra, mutate=True)
        except CliError as exc:
            if exc.error.get('code') != 800004135 and 'limited' not in str(exc.error.get('message', '')).lower():
                raise
            time.sleep(2.1)
            return self.base(command, extra, mutate=True)

    def configure_views(self):
        if self.state.get('views_configured'):
            return
        template, _ = schema_for(self.plan)
        views = self.base('+view-list')['data']['views']
        for spec in template['views']:
            matches = [v for v in views if v['name'] == spec['name']]
            if len(matches) > 1:
                raise WorkflowError('Duplicate view names in template; choose the correct view explicitly')
            if not matches and spec['name'] == '完整脚本' and not self.template_token and len(views) == 1:
                self.config_write('+view-rename', ['--view-id', views[0]['id'], '--name', spec['name']])
                views[0]['name'] = spec['name']
            elif not matches:
                try:
                    self.base('+view-create', ['--json', json.dumps({'name': spec['name'], 'type': 'grid'}, ensure_ascii=False)], mutate=True)
                except CliError:
                    recovered = self.base('+view-list')['data']['views']
                    if len([v for v in recovered if v['name'] == spec['name']]) != 1:
                        raise
            views = self.base('+view-list')['data']['views']
            current = [v for v in views if v['name'] == spec['name']]
            if len(current) != 1 or current[0]['type'] != 'grid':
                raise WorkflowError('The expected grid view is not uniquely available')
            view_id = current[0]['id']
            extra = ['--view-id', view_id]
            visible = {'visible_fields': [self.fields[name] for name in COLUMNS]}
            self.config_write('+view-set-visible-fields', extra+['--json', json.dumps(visible, ensure_ascii=False)])
            for key in ('sort', 'group', 'filter'):
                body = copy.deepcopy(spec[key])
                for item in body.get(key+'_config', []):
                    item['field'] = self.fields[item['field']]
                for condition in body.get('conditions', []):
                    condition[0] = self.fields[condition[0]]
                self.config_write('+view-set-'+key, extra+['--json', json.dumps(body, ensure_ascii=False)])
        self.verify_views(template['views'])
        self.state['views_configured'] = True
        self.save()

    def verify_views(self, specs):
        reverse = {value: key for key, value in self.fields.items()}
        def name(value):
            return reverse.get(value, value)
        for spec in specs:
            extra = ['--view-id', spec['name']]
            visible = self.base('+view-get-visible-fields', extra)['data']['visible_fields']
            if [name(v) for v in visible] != COLUMNS:
                raise WorkflowError(f"View {spec['name']}: visible column order was not verified")
            for key in ('sort', 'group'):
                actual = self.base('+view-get-'+key, extra)['data'][key] or []
                wanted = spec[key][key+'_config']
                normalized = [{'field': name(v['field']), 'desc': bool(v.get('desc', False))} for v in actual]
                if normalized != wanted:
                    raise WorkflowError(f"View {spec['name']}: {key} configuration was not verified")
            actual = self.base('+view-get-filter', extra)['data'].get('filter') or {}
            actual_conditions = copy.deepcopy(actual.get('conditions', []))
            for condition in actual_conditions:
                condition[0] = name(condition[0])
            wanted = spec['filter']
            if actual_conditions != wanted.get('conditions', []) or (actual_conditions and actual.get('logic', 'and') != wanted.get('logic', 'and')):
                raise WorkflowError(f"View {spec['name']}: filter configuration was not verified")

    def payload(self, shot):
        row = {self.fields['镜号']: shot['id'], self.fields['拍摄地点']: [shot['location']], self.fields['已拍摄']: False}
        for target, source in [('台词／文案', 'dialogue'), ('镜头描述', 'description'), ('拍摄备注', 'notes')]:
            if shot[source] != '':
                row[self.fields[target]] = shot[source]
        return row

    def import_records(self):
        indexed = self.index(self.records())
        resumed = set(indexed)
        missing = [s for s in self.plan['shots'] if s['id'] not in indexed]
        created = set()
        for offset in range(0, len(missing), 200):
            batch = missing[offset:offset+200]
            filename = f'batch-{offset:05d}.json'
            write_json(self.work/filename, {'create_records': [self.payload(s) for s in batch]})
            self.state['pending_records'] = [s['id'] for s in batch]
            self.save()
            error = None
            try:
                response = self.base('+record-batch-create', ['--json', '@'+filename], mutate=True)
                if response.get('data', {}).get('ignored_fields'):
                    raise WorkflowError('Some fields were ignored; inspect the online result before continuing')
            except CliError as exc:
                error = exc
            indexed = self.index(self.records())
            if not all(s['id'] in indexed for s in batch):
                time.sleep(2.1)
                indexed = self.index(self.records())
            if not all(s['id'] in indexed for s in batch):
                raise error or WorkflowError('Batch outcome incomplete; resume the same task after checking online records')
            for shot in batch:
                live = indexed[shot['id']]
                if live.get('拍摄地点') != [shot['location']] or (live.get('拍摄备注') or '') != shot['notes'] or live.get('已拍摄') is not False:
                    raise WorkflowError(f"New shot {shot['id']} did not match its location, notes or unshot status")
                created.add(shot['id'])
            self.state.pop('pending_records', None)
            self.save()
        return indexed, created, resumed

    def upload_references(self, indexed):
        count = 0
        for shot in self.plan['shots']:
            for number, reference in enumerate(shot['reference_files']):
                key = f"{shot['id']}/{number}/{reference['sha256']}"
                cell = indexed[shot['id']].get('画面参考') or []
                existing = self.state['uploads'].get(key)
                tokens = {a['file_token'] for a in cell}
                if existing and existing.get('status') == 'done':
                    if existing['file_token'] not in tokens:
                        self.warnings.append(f"Shot {shot['id']}: a previously uploaded reference was removed; it was not re-added")
                    else:
                        count += 1
                    continue
                if existing:
                    candidates = [a for a in cell if a['file_token'] not in existing['before_tokens']
                                  and a.get('name') == reference['name'] and a.get('size') == reference['size']]
                    if len(candidates) != 1:
                        raise WorkflowError(f"Shot {shot['id']}: previous attachment outcome is uncertain; inspect the cell rather than uploading again")
                    self.state['uploads'][key].update(status='done', file_token=candidates[0]['file_token'])
                    self.save()
                    count += 1
                    continue
                staged = Path('attachments')/reference['sha256'][:16]/reference['name']
                (self.work/staged).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(reference['path'], self.work/staged)
                self.state['uploads'][key] = {'status': 'pending', 'before_tokens': sorted(tokens)}
                self.save()
                error = None
                try:
                    self.base('+record-upload-attachment', ['--record-id', indexed[shot['id']]['record_id'],
                              '--field-id', self.fields['画面参考'], '--file', staged.as_posix()], mutate=True)
                except CliError as exc:
                    error = exc
                indexed = self.index(self.records())
                cell = indexed[shot['id']].get('画面参考') or []
                candidates = [a for a in cell if a['file_token'] not in tokens
                              and a.get('name') == reference['name'] and a.get('size') == reference['size']]
                if len(candidates) != 1:
                    raise error or WorkflowError(f"Shot {shot['id']}: attachment result not uniquely verified; keep pending state")
                self.state['uploads'][key].update(status='done', file_token=candidates[0]['file_token'])
                self.save()
                count += 1
        return count

    def run(self):
        self.auth()
        self.ensure_target()
        self.ensure_fields()
        self.configure_views()
        indexed, created, resumed = self.import_records()
        references = self.upload_references(indexed)
        final = self.index(self.records())
        if len(final) != len(self.plan['shots']):
            raise WorkflowError('Final online shot count does not match the plan')
        report = {'status': 'verified', 'base_url': self.state.get('base_url'), 'base_token': self.state['base_token'],
                  'table_id': self.state['table_id'], 'shots': len(final), 'created_this_run': len(created),
                  'resumed_existing_rows': len(resumed), 'verified_reference_files': references,
                  'checked_shots': sum(row.get('已拍摄') is True for row in final.values()),
                  'layout_status': 'template_inherited_needs_visual_check' if self.template_token else 'not_adjusted_by_cli',
                  'source_mapping_verified_by_script': False, 'warnings': self.warnings}
        self.state['verified'] = True
        self.save()
        write_json(self.work/'report.json', report)
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    validate = sub.add_parser('validate', help='Local validation only; no Feishu calls')
    validate.add_argument('plan')
    publish = sub.add_parser('publish', help='Create a new Base or resume this task, then import')
    publish.add_argument('plan')
    publish.add_argument('--work-dir', required=True)
    publish.add_argument('--template-base-token')
    publish.add_argument('--table-name', default='镜头清单')
    publish.add_argument('--time-zone', default='Asia/Shanghai')
    publish.add_argument('--lark-cli', help='Real executable or installed npm run.js entry')
    args = parser.parse_args()
    try:
        plan = load_plan(args.plan)
        if args.command == 'validate':
            result = {'status': 'valid', 'shots': len(plan['shots']), 'reference_files': sum(len(s['reference_files']) for s in plan['shots']),
                      'fingerprint': plan['fingerprint'], 'source_mapping_verified_by_script': False}
        else:
            work = Path(args.work_dir).resolve()
            work.mkdir(parents=True, exist_ok=True)
            result = Publisher(plan, work, RealCli(work, args.lark_cli), args.template_base_token, args.table_name, args.time_zone).run()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (WorkflowError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({'status': 'stopped', 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
