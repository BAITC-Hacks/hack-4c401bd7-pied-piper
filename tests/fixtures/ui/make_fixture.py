"""Generate synthetic, versioned contract data; never uses actual client profiles."""
from __future__ import annotations
import argparse
from hashlib import sha256
import importlib.metadata
import json
from pathlib import Path
import platform
import sys
from uuid import uuid4

import networkx as nx
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from backend.core.contracts import OUTPUT_SCHEMAS, INPUT_SCHEMAS, ROLES, SUBSTANTIVE_ROLES, PRIORITY_WEIGHTS


def make_fixture(output: Path, data: Path | None = None):
    data = data or output.parent/'fixture_data'
    data.mkdir(parents=True, exist_ok=True)
    run_id = str(uuid4())
    run = output/'runs'/run_id
    run.mkdir(parents=True)
    gids = [str(9007199254741000+i) for i in range(24)]
    nodes = pd.DataFrame({'gid':pd.Series(gids,dtype='int64'), 'depth':[0,1,2,3]+[1]*18+[4,0], 'is_seed':[True]+[False]*22+[True]})
    edges = pd.DataFrame([{'src':int(gids[0]),'dst':int(gid),'sum_kzt':float((i+1)*10000),'n_tx':1,'depth':1}
                         for i,gid in enumerate([gids[1]]+gids[4:22])] +
                        [{'src':int(gids[a]),'dst':int(gids[b]),'sum_kzt':10000.,'n_tx':1,'depth':d}
                         for a,b,d in [(1,2,2),(2,3,3),(3,22,4)]])
    edges['depth'] = edges.depth.astype('int8')
    edges = edges.sort_values(['src','dst']).reset_index(drop=True)
    tx = edges[['src','dst']].copy()
    tx['date'] = '2026-07-15'
    tx['sum_kzt'] = edges.sum_kzt
    for name,frame in [('nodes',nodes),('edges',edges),('transactions',tx)]:
        frame.to_parquet(data/f'{name}.parquet',index=False)
    f = nodes.copy()
    f['cluster_id'] = [0]*23+[1]
    f['component_id'] = f.cluster_id
    for field,endpoint,source,operation in [('in_deg','dst','src','nunique'),('out_deg','src','dst','nunique'),
                                          ('in_kzt','dst','sum_kzt','sum'),('out_kzt','src','sum_kzt','sum'),
                                          ('in_tx','dst','n_tx','sum'),('out_tx','src','n_tx','sum')]:
        f[field] = f.gid.map(edges.groupby(endpoint)[source].agg(operation)).fillna(0)
    graph = nx.DiGraph(); graph.add_nodes_from(nodes.gid); graph.add_edges_from(zip(edges.src,edges.dst))
    weighted = graph.copy()
    for e in edges.itertuples():
        weighted[e.src][e.dst]['weight'] = e.sum_kzt
    f['pagerank'] = f.gid.map(nx.pagerank(weighted,weight='weight'))
    f['betweenness'] = f.gid.map(nx.betweenness_centrality(graph,k=24,seed=42))
    f['pass_through'] = f.out_kzt/f.in_kzt.where(f.in_kzt.gt(0))
    f['at_boundary'] = f.depth.eq(4)
    f['truncated_by_depth'] = f.at_boundary & f.out_deg.eq(0)
    f['ratio_usable'] = f.in_kzt.gt(0)&~f.is_seed&~f.at_boundary
    f['seed_distance'] = pd.Series(f.depth,dtype='Int64')
    f['cross_cluster_degree'] = 0
    f['bridge_fraction'] = 0.
    f['bridge'] = 0.
    f['role'] = ['distributor']+['transit']*3+['terminal']*18+['peripheral']*2
    f['role_score'] = [.8]*22+[.5,.5]
    f['evidence'] = [f'Синтетический пример {i}: вход {int(r.in_deg)}, выход {int(r.out_deg)}.' for i,r in enumerate(f.itertuples())]
    for role in ROLES:
        f[f'score_{role}'] = f.role.eq(role).astype(float)*(1. if role=='peripheral' else .8)
    for role in SUBSTANTIVE_ROLES:
        f[f'eligible_{role}'] = f.role.eq(role)
        f[f'ineligible_reason_{role}'] = pd.Series([None if r==role else 'Синтетический fixture: роль не выбрана.' for r in f.role],dtype='object')
    f['alternative_role'] = pd.Series([None]*24,dtype='object')
    f['alternative_score'] = 0.
    f['role_margin'] = f.role.ne('peripheral').astype(float)*.8
    scales, norm = {}, {}
    for key in ('in_deg','out_deg','in_kzt','out_kzt','in_tx','out_tx','betweenness','pagerank','cross_cluster_degree','turnover_kzt'):
        values = f.in_kzt+f.out_kzt if key=='turnover_kzt' else f[key]
        log = np.log1p(values.astype(float))
        scales[key] = float(log[log.gt(0)].quantile(.95)) if log.gt(0).any() else 0.
        norm[key] = (log/scales[key]).clip(0,1) if scales[key] else log*0
    f['priority_structure'] = .6*norm['betweenness']+.25*norm['pagerank']+.15*f.bridge
    f['priority_seed_proximity'] = 1/(1+f.depth)
    f['priority_magnitude'] = norm['turnover_kzt']
    f['priority_role_support'] = f.role_score.where(f.role.ne('peripheral'),0)
    for key,weight in PRIORITY_WEIGHTS.items():
        f[f'contribution_{key}'] = weight*f[f'priority_{key}']
    f['priority_score'] = f[[f'contribution_{k}' for k in PRIORITY_WEIGHTS]].sum(axis=1)
    # Fixture expresses protocol/algebra, not real role eligibility or AML results.
    for field in OUTPUT_SCHEMAS['node_metrics.parquet']:
        if field.dtype!='string':
            f[field.name] = f[field.name].astype('Int64' if field.name=='seed_distance' else field.dtype)
    f = f[[field.name for field in OUTPUT_SCHEMAS['node_metrics.parquet']]]
    roles = f[[field.name for field in OUTPUT_SCHEMAS['nodes_roles.csv']]].copy()
    ranked = roles.sort_values(['priority_score','gid'],ascending=[False,True])
    top = ranked.head(20)[['gid','role','priority_score']].copy()
    top.insert(0,'rank',range(1,21)); top['why'] = 'Синтетическая очередь для проверки UI; не результат AML-анализа.'
    clusters = pd.DataFrame([{'cluster_id':cid,'n_nodes':len(group),'n_seed':int(group.is_seed.sum()),
                             'sum_kzt_internal':float(edges.sum_kzt.sum()) if cid==0 else 0.,
                             'top_gids':'|'.join(ranked[ranked.cluster_id.eq(cid)].head(5).gid.astype(str)),
                             'hypothesis':'Синтетическое сообщество для проверки интерфейса.'}
                            for cid,group in f.groupby('cluster_id')])
    for name,frame in [('nodes_roles.csv',roles),('clusters.csv',clusters),('top_nodes.csv',top)]:
        frame.to_csv(run/name,index=False)
    f.to_parquet(run/'node_metrics.parquet',index=False); edges.to_parquet(run/'edges.parquet',index=False)
    manifest = {'schema_version':'1.0.0','run_id':run_id,'status':'complete','data_kind':'synthetic',
                'counts':{'nodes':24,'edges':22,'transactions':22,'seeds':2,'components':2,'clusters':2},
                'input_sha256':{name:sha256((data/name).read_bytes()).hexdigest() for name in INPUT_SCHEMAS},
                'output_sha256':{name:sha256((run/name).read_bytes()).hexdigest() for name in OUTPUT_SCHEMAS},
                'versions':{'python':platform.python_version(),'methodology':'synthetic-fixture',
                            **{name:importlib.metadata.version(name) for name in ('numpy','pandas','pyarrow','networkx','scipy')}},
                'seed':42,'thresholds':{'in_degree_min':2,'out_degree_min':2,'terminal_in_kzt_min':10000.,'transit_balance_min':1.,'coordinator_betweenness_min':None},
                'normalization_scales':scales,'priority_weights':PRIORITY_WEIGHTS,
                'stage_runtimes_seconds':{key:0. for key in ('load','features','roles','export','validation','total')},
                'role_distribution':{r:int(f.role.eq(r).sum()) for r in ROLES},
                'warnings':['Синтетический fixture; не классификация реальных клиентов.'],
                'validation':{'status':'passed','validator_version':'fixture-1.0.0'}}
    (run/'run.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n', encoding='utf-8')
    temp = output/'current.tmp'
    temp.write_text(json.dumps({'schema_version':'1.0.0','run_id':run_id})+'\n', encoding='utf-8')
    temp.replace(output/'current.json')
    return gids


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=Path('demo_outputs'))
    parser.add_argument('--data',type=Path,default=Path('demo_data'))
    args=parser.parse_args(); make_fixture(args.out,args.data)
    print(f'SYNTHETIC v1.0.0: {args.out}/current.json; input: {args.data}')
