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
