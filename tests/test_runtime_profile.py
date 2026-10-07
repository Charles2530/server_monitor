import json
import pytest

from server import runtime_profile as profile


def test_m4_mini_profile_is_balanced_and_keeps_cloud_request_cap():
    result = profile.choose_profile({
        'model': 'Mac16,10',
        'machine': 'arm64',
        'ncpu': 10,
        'memory_gb': 24,
    })
    assert result['name'] == 'm4-balanced'
    assert result['collector_concurrency'] == 6
    assert result['metric_concurrency'] == 4
    assert result['queue_concurrency'] == 6
    assert result['terminal_max_sessions'] == 8
    assert result['target_cache_seconds'] == 8
    assert result['web_process_type'] == 'Interactive'


def test_only_detected_mini_receives_new_poll_tuning():
    mini = profile.choose_profile({'model': 'Mac16,10', 'ncpu': 10, 'memory_gb': 24})
    assert mini['state_poll_seconds'] == 30
    assert mini['busy_state_poll_seconds'] == 10
    assert mini['error_state_poll_seconds'] == 15
    for model, cores, memory in [('Mac16,1', 10, 24), ('Mac16,1', 14, 48), ('unknown', 10, 24)]:
        other = profile.choose_profile({'model': model, 'ncpu': cores, 'memory_gb': memory})
        assert other.get('state_poll_seconds', 10) == 10
        assert other.get('web_process_type', 'Background') == 'Background'


def test_measured_m4_air_preserves_queue_capacity_and_uses_standard_scheduling():
    result = profile.choose_profile({
        'model': 'Mac16,12',
        'machine': 'arm64',
        'ncpu': 10,
        'memory_gb': 16,
    })
    assert result['name'] == 'm4-balanced'
    assert result['collector_concurrency'] == 6
    assert result['metric_concurrency'] == 4
    assert result['queue_concurrency'] == 3
    assert result['terminal_max_sessions'] == 8
    assert result['target_cache_seconds'] == 8
    assert result['web_process_type'] == 'Standard'
    assert result.get('state_poll_seconds', 10) == 10


@pytest.mark.parametrize('change', [
    {'model':'Mac16,13'}, {'machine':'x86_64'}, {'ncpu':8}, {'memory_gb':24},
])
def test_unmeasured_air_configurations_keep_existing_defaults(change):
    hardware = {'model':'Mac16,12','machine':'arm64','ncpu':10,'memory_gb':16}
    result = profile.choose_profile({**hardware, **change})
    assert result.get('web_process_type', 'Background') == 'Background'
    assert result['queue_concurrency'] == 6


@pytest.mark.parametrize('value', ['Background', 'Standard', 'Interactive'])
def test_valid_explicit_web_scheduling_profiles(value):
    settings = profile.choose_profile({'model':'Mac16,10', 'ncpu':10, 'memory_gb':24})
    settings['web_process_type'] = value
    assert profile._valid_profile(settings)


@pytest.mark.parametrize('value', ['Adaptive', 'interactive', '', None, 1, True, []])
def test_invalid_web_scheduling_profiles_are_rejected(value):
    settings = profile.choose_profile({'model':'Mac16,10', 'ncpu':10, 'memory_gb':24})
    settings['web_process_type'] = value
    assert not profile._valid_profile(settings)


@pytest.mark.parametrize('field,value', [('collector_concurrency','6'), ('metric_concurrency',True),
    ('queue_concurrency',6.5), ('state_poll_seconds',0), ('state_poll_seconds','30')])
def test_runtime_profile_rejects_bad_types_and_optional_polling_limits(field, value):
    settings = profile.choose_profile({'model': 'Mac16,10', 'ncpu': 10, 'memory_gb': 24})
    settings[field] = value
    assert not profile._valid_profile(settings)


def test_metric_concurrency_leaves_two_inventory_slots():
    settings = profile.choose_profile({'ncpu': 10, 'memory_gb': 24})
    settings.update(collector_concurrency=4, metric_concurrency=4)
    assert not profile._valid_profile(settings)


def test_higher_memory_m4_pro_uses_more_local_capacity_without_more_cloud_pressure():
    result = profile.choose_profile({
        'model': 'Mac16,1',
        'machine': 'arm64',
        'ncpu': 14,
        'memory_gb': 48,
    })
    assert result['name'] == 'm4-performance'
    assert result['collector_concurrency'] == 6
    assert result['metric_concurrency'] == 4
    assert result['queue_concurrency'] == 8
    assert result['terminal_max_sessions'] == 12
    assert result['target_cache_seconds'] == 6


def test_profile_file_is_validated_and_falls_back_to_safe_defaults(tmp_path, monkeypatch):
    path = tmp_path / 'runtime-profile.json'
    path.write_text(json.dumps({'name': 'bad', 'collector_concurrency': 999}))
    monkeypatch.setattr(profile, 'PROFILE_PATH', path)
    monkeypatch.setattr(profile, 'detect_hardware', lambda: {
        'model':'Mac16,10', 'machine':'arm64', 'ncpu':10, 'memory_gb':24,
    })
    profile.get_runtime_profile.cache_clear()
    result = profile.get_runtime_profile()
    assert result['name'] == 'm4-balanced'
    assert result['collector_concurrency'] == 6
