from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess
import sys
from uuid import uuid4

import pandas as pd
import pytest

from backend.core.bundle_io import resolve_run
from frontend.view import load_bundle
from backend.validate import ValidationError


def write_manifest(run, change):
    manifest=json.loads((run/'run.json').read_text())
    change(manifest)
    (run/'run.json').write_text(json.dumps(manifest))


def modify_metrics(run, change):
    frame=pd.read_parquet(run/'node_metrics.parquet')
    change(frame)
    frame.to_parquet(run/'node_metrics.parquet',index=False)
    write_manifest(run,lambda m:m['output_sha256'].__setitem__('node_metrics.parquet',sha256((run/'node_metrics.parquet').read_bytes()).hexdigest()))


def test_root_pointer_and_new_publication(sample):
    run, _, _=sample
    root=run.parent.parent
    assert load_bundle(root).manifest['run_id']==run.name
    new_id=str(uuid4()); new_run=root/'runs'/new_id
    shutil.copytree(run,new_run)
    write_manifest(new_run,lambda m:m.__setitem__('run_id',new_id))
    pointer={'schema_version':'1.0.0','run_id':new_id}
    (root/'current.tmp').write_text(json.dumps(pointer))
    (root/'current.tmp').replace(root/'current.json')
    assert load_bundle(root).manifest['run_id']==new_id
    assert (run/'run.json').exists()


@pytest.mark.parametrize('bad_id',['../outside','../../data','',123,'UPPER/UUID'])
def test_pointer_rejects_paths(sample,bad_id):
    run, _, _=sample
    (run.parent.parent/'current.json').write_text(json.dumps({'schema_version':'1.0.0','run_id':bad_id}))
    with pytest.raises(ValidationError,match='UUID'):
        load_bundle(run.parent.parent)


@pytest.mark.parametrize(('key','value','error'),[
    ('schema_version',1,'schema_version'),
    ('status','candidate','status'),
    ('run_id',str(uuid4()),'run_id'),
    ('validation',{'status':'failed','validator_version':'1.0.0'},'validation'),
    ('priority_weights',{'magnitude':1.},'priority_weights'),
    ('warnings','oops','warnings'),
])
def test_bad_manifest(sample,key,value,error):
    run, _, _=sample
    write_manifest(run,lambda m:m.__setitem__(key,value))
    with pytest.raises(ValidationError,match=error):
        load_bundle(run.parent.parent)


def test_count_mismatch_and_nonfinite_json(sample):
    run, _, _=sample
    write_manifest(run,lambda m:m['counts'].__setitem__('transactions',23))
    with pytest.raises(ValidationError,match='counts'):
        load_bundle(run)
    write_manifest(run,lambda m:m['stage_runtimes_seconds'].__setitem__('total',float('nan')))
    with pytest.raises(ValidationError,match='JSON'):
        load_bundle(run)


@pytest.mark.parametrize(('field','value','error'),[
    ('at_boundary',False,'at_boundary'),
    ('contribution_magnitude',0.,'contribution_magnitude'),
    ('role_margin',0.,'неоднозначность'),
    ('priority_seed_proximity',0.,'priority_seed_proximity'),
    ('pass_through',0.,'NULL'),
    ('component_id',4,'component_id'),
])
def test_metric_identities(sample,field,value,error):
    run, _, _=sample
    modify_metrics(run,lambda f:f.__setitem__(field,value))
    with pytest.raises(ValidationError,match=error):
        load_bundle(run)


def test_required_nullable_and_physical_types(sample):
    run, _, _=sample
    modify_metrics(run,lambda f:f.__setitem__('seed_distance',f.seed_distance.astype(float)))
    with pytest.raises(ValidationError,match='int64'):
        load_bundle(run)


def test_candidate_cli_before_publication(sample):
    run,data,_=sample
    staging=run.parent.parent/'.staging'/run.name
    shutil.copytree(run,staging)
    candidate=json.loads((staging/'run.json').read_text())
    candidate['status']='candidate'; candidate.pop('validation')
    (staging/'run.json').unlink()
    (staging/'candidate.json').write_text(json.dumps(candidate))
    before={p.name:p.read_bytes() for p in staging.iterdir()}
    cmd=[sys.executable,'-m','backend.validate','--data',str(data),'--out',str(staging),'--candidate','--expected-nodes','24']
    result=subprocess.run(cmd,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)=={'status':'passed','validator_version':'1.0.0'}
    assert json.loads(result.stderr)['n_nodes']==24
    assert before=={p.name:p.read_bytes() for p in staging.iterdir()}
    with pytest.raises(ValidationError,match='run.json'):
        load_bundle(staging)
    tx=pd.read_parquet(data/'transactions.parquet');tx.loc[0,'sum_kzt']+=1;tx.to_parquet(data/'transactions.parquet',index=False)
    result=subprocess.run(cmd,capture_output=True,text=True)
    assert result.returncode==1 and 'SHA256' in result.stderr and not result.stdout


def test_candidate_cannot_accept_publication_root(sample):
    run,_,_=sample
    with pytest.raises(ValidationError,match='staging'):
        load_bundle(run.parent.parent,candidate=True)


def test_cli_published_root(sample):
    run,data,_=sample
    result=subprocess.run([sys.executable,'-m','backend.validate','--data',str(data),'--out',str(run.parent.parent),'--expected-nodes','24'],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['status']=='passed'
