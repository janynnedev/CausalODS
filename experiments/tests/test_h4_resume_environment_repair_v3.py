from copy import deepcopy
from types import SimpleNamespace

import pytest

from experiments.final_campaigns_v3 import core
from experiments.multimodal_v3.core import CampaignStoreV3


def environment():
    return {
        'schema_version': 'final-campaign-environment-v3',
        'generated_at': '2026-09-06T00:00:00Z',
        'python': '3.12.3 (old build) [GCC 13.3.0]',
        'git': {'git_commit': 'a' * 40, 'dirty': True, 'dirty_entries': 1},
        'packages': {name: '1.0' for name in
                     ('numpy', 'scikit-learn', 'xgboost', 'joblib', 'pytest')},
        'os': 'posix', 'architecture': 'x86_64', 'platform': 'Linux',
        'contracts': {'feature_registry_sha256': 'b' * 64},
    }


def test_creation_bytes_survive_resume_time_and_operational_metadata(tmp_path, monkeypatch):
    store = CampaignStoreV3(tmp_path, 'campaign')
    original = environment()
    monkeypatch.setattr(core, 'environment_report', lambda root: deepcopy(original))
    core.preserve_creation_environment(store, tmp_path)
    before = (store.path / 'environment.json').read_bytes()
    store.write_sha256s()
    current = deepcopy(original)
    current.update(generated_at='2026-09-11T00:00:00Z',
                   python='3.12.3 (new build) [GCC 13.3.0]')
    current['git'].update(dirty=False, dirty_entries=0)
    monkeypatch.setattr(core, 'environment_report', lambda root: current)
    calls = []
    monkeypatch.setattr(core, 'require_clean_final', lambda *a, **kw: calls.append(kw))
    core.preserve_creation_environment(store, tmp_path)
    assert (store.path / 'environment.json').read_bytes() == before
    assert calls == [{'mode': 'final', 'allow_dirty_development': False}]
    assert len(list((store.path.parent / 'resume-environments' / store.path.name).glob('*.json'))) == 1
    store.verify_sha256s()


@pytest.mark.parametrize('field', ['commit', 'runtime', 'dependency', 'contracts'])
def test_scientific_environment_drift_fails_closed(tmp_path, monkeypatch, field):
    store = CampaignStoreV3(tmp_path, 'campaign')
    original = environment()
    store.write_json('environment.json', original, immutable=True)
    before = (store.path / 'environment.json').read_bytes()
    current = deepcopy(original)
    if field == 'commit':
        current['git']['git_commit'] = 'c' * 40
    elif field == 'runtime':
        current['python'] = '3.13.0 (build)'
    elif field == 'dependency':
        current['packages']['xgboost'] = '2.0'
    else:
        current['contracts']['feature_registry_sha256'] = 'd' * 64
    monkeypatch.setattr(core, 'environment_report', lambda root: current)
    monkeypatch.setattr(core, 'require_clean_final', lambda *a, **kw: None)
    with pytest.raises(ValueError, match='RESUME_ENVIRONMENT_INCOMPATIBLE'):
        core.preserve_creation_environment(store, tmp_path)
    assert (store.path / 'environment.json').read_bytes() == before
    assert not (store.path / 'resume-environments').exists()


def test_final_dirty_worktree_is_not_accepted(tmp_path, monkeypatch):
    store = CampaignStoreV3(tmp_path, 'campaign')
    store.write_json('environment.json', environment(), immutable=True)
    monkeypatch.setattr(core, 'environment_report', lambda root: environment())
    monkeypatch.setattr(core, 'git_worktree_status', lambda root: {'dirty': True})
    with pytest.raises(RuntimeError, match='GIT_WORKTREE_CLEAN_REQUIRED'):
        core.preserve_creation_environment(store, tmp_path)


def test_legacy_recovery_stops_before_any_mutation_or_inference(tmp_path):
    store = CampaignStoreV3(tmp_path, 'campaign')
    store.write_json('recovery/old/run.json', {'status': {'state': 'SUCCESS'}})
    store.write_json('runs/index.json', {})
    before = {p.relative_to(store.path): p.read_bytes()
              for p in store.path.rglob('*') if p.is_file()}
    with pytest.raises(ValueError, match='H4_RECOVERY_REQUIRES_VERIFIED_CANONICAL_REPAIR'):
        core.prepare_selective_resume(store, [SimpleNamespace(run_id='run', hypothesis='H4')])
    assert before == {p.relative_to(store.path): p.read_bytes()
                      for p in store.path.rglob('*') if p.is_file()}
