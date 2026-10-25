import lzma, json, sys, difflib
ROOT='/data/fritz/learn-claude-code/research/18_harness_wait/data/cells'
cell, sess, a, b = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
recs={}
with lzma.open(f'{ROOT}/{cell}/runs/{sess}/{sess}.requests.jsonl.xz','rt') as f:
    for line in f:
        x=json.loads(line)
        if x.get('purpose')=='lead' and x['call_index'] in (a,b): recs[x['call_index']]=x
A,B=recs[a],recs[b]
print('turns', A['turn_id'], B['turn_id'], '| n msgs', len(A['messages']), len(B['messages']), '| system equal', A['system']==B['system'], '| tools equal', A['tools']==B['tools'])
def flat(m):
    c=m.get('content')
    return json.dumps(c, ensure_ascii=False) if not isinstance(c,str) else c
for i,(ma,mb) in enumerate(zip(A['messages'],B['messages'])):
    if ma!=mb:
        ta,tb=flat(ma),flat(mb)
        k=next((j for j in range(min(len(ta),len(tb))) if ta[j]!=tb[j]), min(len(ta),len(tb)))
        print(f'first differing message #{i} role {ma.get("role")}/{mb.get("role")}, lens {len(ta)} -> {len(tb)} chars, first diff at char {k}')
        print('OLD:', repr(ta[max(0,k-150):k+250]))
        print('NEW:', repr(tb[max(0,k-150):k+250]))
        nd=sum(1 for x,y in zip(A['messages'],B['messages']) if x!=y)
        print('messages differing among the common', min(len(A['messages']),len(B['messages'])), ':', nd)
        break
else:
    print('common messages identical; B appends', len(B['messages'])-len(A['messages']))
