"""Read-only forensic payload audit; writes only to validation, never campaign storage.

Run from the repository root with python3. No inference or third-party imports.
Historical checksums must NOT be inferred from hashes computed by this audit.
"""
import json, hashlib
from pathlib import Path
from collections import Counter
root=Path.cwd()
store=root/'artifacts/final-experiments-v3-h4-final-40/H4/final-h4-v3/b0ead5efc335c75750d79451cff03dc21dd11363f281bf3fa40b1ab967848a94'
plan={r['run_id']:r for r in json.loads((store/'execution-plan.json').read_text())['runs']}
rows=[]
for path in sorted((store/'recovery').rglob('*.json')):
 v=json.loads(path.read_text()); rid=v.get('identity',{}).get('run_id'); r=plan.get(rid)
 if r is None: raise ValueError(str(path))
 i,p,c=v['input'],v['provenance'],v['configuration']
 checks=[('status.state == SUCCESS',v['status']['state']=='SUCCESS'),('identity.run_id',rid==path.stem),('store.configuration_hash',store.name==r['configuration_hash']),('input.source_manifest_hash',i.get('source_manifest_hash')==r['source_manifest_hash']),('input.model_hash',i.get('model_hash')==r['model_hash']),('provenance.model_specification_hash',p.get('model_specification_hash')==r['model_hash']),('provenance.trained_model_artifact_hash',isinstance(p.get('trained_model_artifact_hash'),str) and len(p['trained_model_artifact_hash'])==64)]
 first=next((name for name,ok in checks if not ok),None)
 identity_checks={k:v['identity'].get(k)==r[k] for k in v['identity']}
 identity_checks.update({k:i.get(k)==r[pk] for k,pk in {'dataset':'source_dataset','system':'source_system','incident_id':'incident_id','seed':'seed','feature_view_hash':'feature_view_hash','source_manifest_hash':'source_manifest_hash'}.items()})
 identity_checks.update({k:c.get(k)==r[k] for k in c})
 if v['status']['state']=='SUCCESS':
  identity_checks.update({k:p.get(k)==r[pk] for k,pk in {'train_ids':'train_incident_ids','validation_ids':'validation_incident_ids','test_ids':'test_incident_ids'}.items()})
 rows.append({'run_id':rid,'state':v['status']['state'],'reusable':False,'first_payload_rejection_predicate':first,'reason': 'expected '+str(r['model_hash'])+', observed '+repr(p.get('model_specification_hash')) if first=='provenance.model_specification_hash' else 'expected SUCCESS, observed '+v['status']['state'],'path':str(path.relative_to(root)),'observed_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'checksum_status':'UNVERIFIABLE_ORIGINAL_INDEX_NOT_PRESERVED','scientific_identity_status':'PASS' if all(identity_checks.values()) else 'FAIL','model_identity_status':'SPECIFICATION_INPUT_MATCHES_TRAINED_ARTIFACT_UNRECORDED' if v['status']['state']=='SUCCESS' else 'NOT_APPLICABLE_NO_INFERENCE','provenance_status':'MISSING_MODEL_SPECIFICATION_AND_TRAINED_ARTIFACT' if v['status']['state']=='SUCCESS' else 'EMPTY_DETERMINISTIC_SKIP','predicate_results':dict(checks),'scientific_identity_checks':identity_checks})
active={p.stem for p in (store/'runs').glob('*.json') if p.name!='index.json'}
old={r['run_id'] for r in rows}; counts=Counter(r['state'] for r in rows)
report={'schema_version':'h4-resume-forensic-audit-v1','planned_total':len(plan),'planned_applicable':sum(r['eligibility'] for r in plan.values()),'planned_skips':sum(not r['eligibility'] for r in plan.values()),'pre_oom_unique':len(old),'pre_oom_success':counts['SUCCESS'],'pre_oom_skip':counts['SKIPPED_MODALITY_UNAVAILABLE'],'bad_resume_active_count':len(active),'bad_resume_overlap_count':len(active&old),'global_unique_before_repair':len(active|old),'scientific_remaining':len(set(plan)-(active|old)),'applicable_remaining':sum(r['eligibility'] for k,r in plan.items() if k not in old|active),'skip_remaining':sum(not r['eligibility'] for k,r in plan.items() if k not in old|active),'H4_SUCCESS_REUSABLE_BEFORE_FIX':0,'H4_SUCCESS_REJECTED_BEFORE_FIX':counts['SUCCESS'],'H4_RESUME_REJECTION_REASONS':dict(sorted(Counter(r['first_payload_rejection_predicate'] for r in rows).items())),'historical_checksum_caveat':'First PAYLOAD rejection is proven. The earlier checksum predicate cannot be reconstructed because prepare_selective_resume overwrote runs/index.json without saving it. Current hashes are inventory, not historical checksum evidence. No SUCCESS is certified reusable.','rows':rows}
out=root/'artifacts/validation/h4-resume-repair/h4-resume-repair-audit.json'
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
print(json.dumps({k:v for k,v in report.items() if k!='rows'},indent=2))
