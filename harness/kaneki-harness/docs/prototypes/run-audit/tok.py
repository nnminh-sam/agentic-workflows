import json,glob,os,sys,collections
P=r'C:\Users\minhnn4\.claude\projects\D--workspace-HAISE-wp-kaneki'
S=['17e83121-fd3f-461d-b776-2f9b6f4879e8','a3c4afd6-bec4-40c4-9a99-24d2e1ad251c','38462dbc-8597-4316-b184-da198b41a460']
def scan(f):
    seen={}; first=last=None
    for l in open(f,encoding='utf-8'):
        try:d=json.loads(l)
        except:continue
        ts=d.get('timestamp')
        if ts: first=first or ts; last=ts
        if d.get('type')!='assistant':continue
        m=d.get('message',{}); u=m.get('usage')
        if not u:continue
        key=m.get('id') or d.get('requestId') or d.get('uuid')
        # keep last (final usage) per message id
        seen[key]=(m.get('model'),u)
    return seen,first,last
grand=collections.Counter(); bymodel=collections.defaultdict(collections.Counter)
rows=[]
for s in S:
    files=[(os.path.join(P,s+'.jsonl'),'main')]+[(f,'sub') for f in glob.glob(os.path.join(P,s,'subagents','*.jsonl'))]
    for f,kind in files:
        seen,a,b=scan(f)
        c=collections.Counter()
        for mid,(model,u) in seen.items():
            for k in ('input_tokens','cache_creation_input_tokens','cache_read_input_tokens','output_tokens'):
                v=u.get(k) or 0; c[k]+=v; bymodel[model][k]+=v
            c['msgs']+=1; bymodel[model]['msgs']+=1
        models=collections.Counter(m for m,_ in seen.values())
        rows.append((s[:8],kind,os.path.basename(f)[:40],a,b,dict(c),dict(models)))
        grand+=c
for r in rows:
    c=r[5]; tot=sum(c.get(k,0) for k in ('input_tokens','cache_creation_input_tokens','cache_read_input_tokens','output_tokens'))
    print(r[0],r[1],r[2],r[3][11:19] if r[3] else '',r[4][11:19] if r[4] else '',c.get('msgs',0),'in',c.get('input_tokens',0),'cw',c.get('cache_creation_input_tokens',0),'cr',c.get('cache_read_input_tokens',0),'out',c.get('output_tokens',0),'TOT',tot,r[6])
print('GRAND',dict(grand), sum(v for k,v in grand.items() if k!='msgs'))
for m,c in bymodel.items(): print(m,dict(c), sum(v for k,v in c.items() if k!='msgs'))
