import json
import sqlite3


def test_workspace_contains_exact_descendant_and_rejects_similar_prefix():
    from server.ide_history import workspace_contains
    root = '/workspace/project'
    assert workspace_contains(root, '/workspace/project')
    assert workspace_contains(root, '/workspace/project/src')
    assert workspace_contains(root, '/workspace/project/src/nested')
    assert not workspace_contains(root, '/workspace/project-old')
    assert not workspace_contains(root, '/workspace')
    assert not workspace_contains(root, '')
    assert not workspace_contains(root, 'src')


def test_sanitize_history_result_filters_paths_limits_and_content():
    from server.ide_history import sanitize_history_result
    value = {
        'workspace': '/workspace/project',
        'records': [
            {'thread_id': 't1', 'display_title': 'Root', 'updated_at': 20, 'cwd': '/workspace/project', 'content': 'secret'},
            {'thread_id': 't2', 'display_title': 'Child', 'updated_at': 10, 'cwd': '/workspace/project/src'},
            {'thread_id': 'bad', 'display_title': 'Other', 'updated_at': 30, 'cwd': '/workspace/project-old'},
            {'thread_id': 'none', 'display_title': 'No path', 'updated_at': 40, 'cwd': None},
        ],
        'token': 'secret',
    }
    result = sanitize_history_result(value, '/workspace/project')
    assert result['workspace'] == '/workspace/project'
    assert [row['thread_id'] for row in result['records']] == ['t1', 't2']
    assert set(result['records'][0]) == {'thread_id', 'display_title', 'updated_at', 'cwd'}
    assert 'content' not in json.dumps(result)
    assert 'token' not in json.dumps(result)


def test_sanitize_history_result_bounds_title_and_count():
    from server.ide_history import MAX_HISTORY_RECORDS, MAX_TITLE_LENGTH, sanitize_history_result
    records = [
        {'thread_id': str(i), 'display_title': 'x' * (MAX_TITLE_LENGTH + 20), 'updated_at': i, 'cwd': '/workspace/project'}
        for i in range(MAX_HISTORY_RECORDS + 10)
    ]
    result = sanitize_history_result({'records': records}, '/workspace/project')
    assert len(result['records']) == MAX_HISTORY_RECORDS
    assert all(len(row['display_title']) <= MAX_TITLE_LENGTH for row in result['records'])


def test_history_script_is_self_contained_and_does_not_print_content():
    from server.ide_history import history_script
    script = history_script('/workspace/project')
    compile(script, 'history', 'exec')
    assert 'local_thread_catalog' in script
    assert 'session_meta' in script
    assert 'auth.json' not in script
    assert 'item_json' not in script


def test_history_script_reads_codex_ui_thread_index_by_project_path(tmp_path, monkeypatch):
    home = tmp_path / '.codex'
    home.mkdir()
    db = home / 'state_5.sqlite'
    with sqlite3.connect(db) as connection:
        connection.execute(
            'create table threads (id text, cwd text, title text, updated_at integer)'
        )
        connection.executemany(
            'insert into threads values (?, ?, ?, ?)',
            [
                ('new-task', '/workspace/project', 'New task', 30),
                ('child-task', '/workspace/project/src', 'Child task', 20),
                ('similar-prefix', '/workspace/project-old', 'Wrong project', 40),
            ],
        )

    from server.ide_history import history_script

    monkeypatch.setenv('CODEX_HOME', str(home))
    namespace = {}
    exec(history_script('/workspace/project'), namespace)
    result = namespace['RESULT']
    assert [row['thread_id'] for row in result['records']] == ['new-task', 'child-task']
    assert result['records'][0]['display_title'] == 'New task'
    assert result['records'][1]['cwd'] == '/workspace/project/src'


def test_sanitize_uses_container_canonical_workspace_path():
    from server.ide_history import sanitize_history_result
    result = sanitize_history_result({
        'workspace': '/mnt/project',
        'records': [{'thread_id': 't1', 'display_title': 'Project', 'updated_at': 1, 'cwd': '/mnt/project/src'}],
    }, '/workspace')
    assert result['workspace'] == '/mnt/project'
    assert [row['thread_id'] for row in result['records']] == ['t1']


def _history_index(tmp_path, monkeypatch, rows):
    home = tmp_path / '.codex'
    home.mkdir()
    with sqlite3.connect(home / 'state_5.sqlite') as db:
        db.execute('create table threads (id text, cwd text, title text, updated_at integer)')
        db.executemany('insert into threads values (?, ?, ?, ?)', rows)
    monkeypatch.setenv('CODEX_HOME', str(home))
    return home


def test_empty_valid_index_does_not_scan_rollouts(tmp_path, monkeypatch):
    import glob
    from server.ide_history import history_script
    _history_index(tmp_path, monkeypatch, [('other', '/other', 'Other', 10)])
    scans = []
    monkeypatch.setattr(glob, 'iglob', lambda *a, **kw: scans.append(a) or iter(()))
    ns = {}
    exec(history_script('/workspace/project'), ns)
    assert ns['RESULT']['records'] == []
    assert scans == []


def test_history_resolves_repeated_paths_once(tmp_path, monkeypatch):
    import os
    from server.ide_history import history_script
    _history_index(tmp_path, monkeypatch, [(str(i), '/workspace/project', 'Task', i) for i in range(250)])
    original = os.path.realpath
    calls = []
    def tracked(path):
        calls.append(path)
        return original(path)
    monkeypatch.setattr(os.path, 'realpath', tracked)
    ns = {}
    exec(history_script('/workspace/project'), ns)
    assert len(ns['RESULT']['records']) == 200
    assert calls.count('/workspace/project') == 1


def test_project_filter_precedes_global_history_limit(tmp_path, monkeypatch):
    from server.ide_history import history_script
    _history_index(tmp_path, monkeypatch,
        [('mine', '/workspace/project', 'Older project task', 1)] +
        [(str(i), '/other', 'Other', i + 2) for i in range(5100)])
    ns = {}
    exec(history_script('/workspace/project'), ns)
    assert [r['thread_id'] for r in ns['RESULT']['records']] == ['mine']


def test_missing_index_keeps_jsonl_metadata_fallback(tmp_path, monkeypatch):
    from server.ide_history import history_script
    home = tmp_path / '.codex'
    sessions = home / 'sessions'
    sessions.mkdir(parents=True)
    (sessions / 'rollout-test.jsonl').write_text(json.dumps({'type': 'session_meta', 'payload': {
        'id': 'fallback', 'cwd': '/workspace/project', 'timestamp': '2026-10-07'}}) + '\n')
    monkeypatch.setenv('CODEX_HOME', str(home))
    ns = {}
    exec(history_script('/workspace/project'), ns)
    assert ns['RESULT']['records'][0]['thread_id'] == 'fallback'


def test_cached_paths_preserve_symlink_and_directory_boundaries(tmp_path, monkeypatch):
    from server.ide_history import history_script
    project = tmp_path / 'project'
    project.mkdir()
    alias = tmp_path / 'alias'
    alias.symlink_to(project, target_is_directory=True)
    _history_index(tmp_path, monkeypatch, [
        ('alias', str(alias / 'src'), 'Alias child', 3),
        ('root', str(project), 'Root', 2),
        ('similar', str(project) + '-old', 'Other', 4),
    ])
    ns = {}
    exec(history_script(str(alias)), ns)
    assert ns['RESULT']['workspace'] == str(project.resolve())
    assert [row['thread_id'] for row in ns['RESULT']['records']] == ['alias', 'root']


def test_legacy_index_remains_supported(tmp_path, monkeypatch):
    from server.ide_history import history_script
    home = tmp_path / '.codex'
    (home / 'sqlite').mkdir(parents=True)
    with sqlite3.connect(home / 'sqlite' / 'codex-dev.db') as db:
        db.execute('create table local_thread_catalog (thread_id text, cwd text, display_title text, source_updated_at integer)')
        db.execute('insert into local_thread_catalog values (?, ?, ?, ?)', ('old', '/workspace/project', 'Old', 1))
    monkeypatch.setenv('CODEX_HOME', str(home))
    ns = {}
    exec(history_script('/workspace/project'), ns)
    assert ns['RESULT']['records'][0]['thread_id'] == 'old'


def test_history_byte_budget_truncates_escaped_metadata(tmp_path, monkeypatch):
    from server.ide_history import history_script, sanitize_history_result
    cwd = '/workspace/project/' + '/'.join(['项目' * 100] * 18)
    _history_index(tmp_path, monkeypatch,
                   [(str(i), cwd, '讨论' * 90, i) for i in range(200)])
    ns = {}
    exec(history_script('/workspace/project'), ns)
    result = ns['RESULT']
    assert len(json.dumps(result).encode()) <= 512 * 1024
    assert 0 < len(result['records']) < 200
    assert result['truncated'] is True
    assert result['records'][0]['thread_id'] == '199'
    assert sanitize_history_result(result, '/workspace/project')['truncated'] is True


def test_workspace_contains_normalizes_parent_segments():
    from server.ide_history import workspace_contains
    assert not workspace_contains('/workspace/project', '/workspace/project/../other')
    assert workspace_contains('/workspace/project/src/..', '/workspace/project/file')
    assert not workspace_contains('/workspace/project', '/workspace/project/\x00')


def test_index_record_limit_reports_truncation(tmp_path, monkeypatch):
    from server.ide_history import history_script
    _history_index(tmp_path, monkeypatch,
                   [(str(i), '/workspace/project', 'Task', i) for i in range(201)])
    ns = {}
    exec(history_script('/workspace/project'), ns)
    assert len(ns['RESULT']['records']) == 200
    assert ns['RESULT']['truncated'] is True
    assert ns['RESULT']['records'][0]['thread_id'] == '200'


def test_readable_empty_sessions_directory_is_trusted(tmp_path, monkeypatch):
    from server.ide_history import history_script
    home = tmp_path / '.codex'
    (home / 'sessions').mkdir(parents=True)
    monkeypatch.setenv('CODEX_HOME', str(home))
    ns = {}
    exec(history_script('/workspace/project'), ns)
    assert ns['RESULT'] == {'workspace': '/workspace/project', 'records': [],
                            'error': None, 'truncated': False}


def test_fallback_file_scan_limit_reports_truncation(tmp_path, monkeypatch):
    from server.ide_history import history_script
    home = tmp_path / '.codex'
    sessions = home / 'sessions'
    sessions.mkdir(parents=True)
    for i in range(1001):
        (sessions / f'rollout-{i:04}.jsonl').write_text(json.dumps({
            'type': 'session_meta', 'payload': {'id': str(i), 'cwd': '/other'}}) + '\n')
    monkeypatch.setenv('CODEX_HOME', str(home))
    ns = {}
    exec(history_script('/workspace/project'), ns)
    assert ns['RESULT']['records'] == []
    assert ns['RESULT']['truncated'] is True


def test_fallback_record_limit_reports_truncation(tmp_path, monkeypatch):
    from server.ide_history import history_script
    home = tmp_path / '.codex'
    sessions = home / 'sessions'
    sessions.mkdir(parents=True)
    for i in range(201):
        (sessions / f'rollout-{i:04}.jsonl').write_text(json.dumps({
            'type': 'session_meta', 'payload': {
                'id': str(i), 'cwd': '/workspace/project', 'timestamp': i}}) + '\n')
    monkeypatch.setenv('CODEX_HOME', str(home))
    ns = {}
    exec(history_script('/workspace/project'), ns)
    assert len(ns['RESULT']['records']) == 200
    assert ns['RESULT']['records'][0]['thread_id'] == '200'
    assert ns['RESULT']['truncated'] is True


def test_corrupt_index_can_fall_back_to_readable_rollout(tmp_path, monkeypatch):
    from server.ide_history import history_script
    home = tmp_path / '.codex'
    sessions = home / 'sessions'
    sessions.mkdir(parents=True)
    (home / 'state_5.sqlite').write_bytes(b'not sqlite')
    (sessions / 'rollout-valid.jsonl').write_text(json.dumps({
        'type': 'session_meta', 'payload': {'id': 'valid', 'cwd': '/workspace/project'}}) + '\n')
    monkeypatch.setenv('CODEX_HOME', str(home))
    ns = {}
    exec(history_script('/workspace/project'), ns)
    assert [row['thread_id'] for row in ns['RESULT']['records']] == ['valid']
    assert ns['RESULT']['error'] is None
