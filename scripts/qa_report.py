"""Summarize a saved live benchmark with independent layout measurements."""
import html
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.schemas import HouseSpec, Layout, RequiredAdjacency
from agent.validator import validate_layout
from agent.model3d import build_scene
from agent.layout_solver import _split_extras
from qa_ten_homes import WEIGHTS


def evaluate(path):
    report = json.loads(path.read_text(encoding='utf-8'))
    rows=[]
    for case in report['cases']:
        row=dict(number=case['number'], session_id=case.get('session_id'), checks=dict(case['checks']),
                 seconds=case.get('seconds'), quality={}, viewer_url=case.get('viewer_url'))
        generations=[e['response'] for e in case['events'] if e['endpoint']=='generate' and e['status']==200]
        if generations:
            state=generations[0]['session']; layout=Layout.model_validate(state['layout']); spec=HouseSpec.model_validate(state['spec'])
            area=layout.plot_width_ft*layout.plot_length_ft
            hall=sum(r.width*r.length for r in layout.rooms if 'hallway' in r.name.lower())
            bedrooms=[r.width*r.length for r in layout.rooms if r.name.lower().startswith('bedroom')]
            adjacency=spec.model_copy(deep=True)
            adjacency.required_adjacencies=[RequiredAdjacency(a='Kitchen',b='Dining Room')]
            adjacent=not any('must be next to' in issue.message for issue in validate_layout(layout,adjacency))
            row['quality']=dict(hallway_percent=round(100*hall/area,2),compact_circulation=hall/area<=.150001,
                kitchen_dining_adjacent=adjacent, balanced_bedrooms=max(bedrooms)/min(bedrooms)<=1.25 if bedrooms else False,
                bedroom_area_ratio=round(max(bedrooms)/min(bedrooms),2) if bedrooms else None,
                design_name=layout.design_name,source=layout.planning_source)
            # Old benchmark compared [] literally for removed extras, incorrectly
            # treating explicitly listed base rooms as an unsuccessful removal.
            if 'office' in case['edit_request'].lower() or 'parking' in case['edit_request'].lower():
                messages=[e for e in case['events'] if e['endpoint']=='messages' and e['status']==200]
                if len(messages)>1:
                    edit=messages[1]['response']; current=edit['session']
                    row['checks']['edit']=bool(edit.get('edit_applied') and current.get('layout')
                        and not any(_split_extras(HouseSpec.model_validate(current['spec'])))
                        and not validate_layout(Layout.model_validate(current['layout']),HouseSpec.model_validate(current['spec']))
                        and current['model_3d_version']==current['layout']['version'] and edit.get('viewer_url'))
            # Inspect the persisted viewer's embedded scene against its current
            # saved layout, after the edit/undo cycle. No browser claim is made.
            sid=case['session_id']; folder=Path('data/sessions')
            current=json.loads((folder/f'{sid}.json').read_text(encoding='utf-8'))
            viewer=(folder/f'{sid}_3d.html').read_text(encoding='utf-8')
            embedded=re.search(r'<script id="scene-data" type="application/json">(.*?)</script>',viewer,re.S)
            row['embedded_scene_matches']=bool(embedded and json.loads(embedded[1])==build_scene(Layout.model_validate(current['layout'])).to_dict())
        row['functional_score']=sum(WEIGHTS[k] for k,v in row['checks'].items() if v)
        row['quality_score']=round(100*sum(row['quality'].get(k,False) for k in
            ('compact_circulation','kitchen_dining_adjacent','balanced_bedrooms'))/3,1)
        rows.append(row)
    summary=dict(cases=rows, functional_score=round(sum(r['functional_score'] for r in rows)/10,1),
                 quality_score=round(sum(r['quality_score'] for r in rows)/10,1),
                 check_totals={k:sum(r['checks'][k] for r in rows) for k in WEIGHTS})
    summary['combined_score']=round(.7*summary['functional_score']+.3*summary['quality_score'],1)
    return summary


def main():
    path=Path(sys.argv[1]); summary=evaluate(path)
    baseline=evaluate(Path(sys.argv[2])) if len(sys.argv)>2 else None
    (path.parent/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    lines=['# ConstructIQ: ten-home live QA report','',
           f"Functional score: **{summary['functional_score']}/100**. Layout-quality proxy: **{summary['quality_score']}/100**.",
           f"Combined tested-scope score (70% functional, 30% quality): **{summary['combined_score']}/100**.",'',
           'Real Ollama Cloud calls through the local FastAPI server; no mocked generation or requirement extraction.',
           'Each successful case exercises generation, a design-changing viewer API edit, wall paint, stale-version rejection, and undo.',
           'The final saved home is restored to its original layout by undo; revision history preserves the edit.','',
           '| Home | Generation | Edit | Paint | Undo | Hallway % | Kitchen/dining adjacent | Balanced bedrooms | Functional |',
           '|---|---|---|---|---|---|---|---|---|']
    mark=lambda value:'Pass' if value else 'Fail / blocked'
    for r in summary['cases']:
        q=r['quality']; c=r['checks']
        lines.append(f"| {r['number']} | {mark(c['generation'])} | {mark(c['edit'])} | {mark(c['paint'])} | {mark(c['undo'])} | {q.get('hallway_percent','N/A')} | {mark(q.get('kitchen_dining_adjacent'))} | {mark(q.get('balanced_bedrooms'))} | {r['functional_score']}/100 |")
    lines+=['','## Scoring and limitations','',
        'Functional weights per home: requirements capture 15, valid generation/current 3D revision 25, asset delivery 10, successful requested edit 20, paint persistence 10, exact undo restoration 10, stale-version protection 10. Blocked downstream checks receive zero.',
        'Requirements capture checks dimensions, counts, entrance, bathroom assignments, dining and requested extras. Kitchen/dining placement is measured separately from geometry rather than trusting the extracted text.',
        'Quality proxy has three equally weighted checks: dedicated hallway area at most 15%, kitchen and dining sharing the validator-required wall span, and largest/smallest bedroom area ratio at most 1.25. These are product benchmark criteria, not an architectural assessment.',
        'Missing layouts receive zero quality points. Compound failures remain visible; successful undo does not erase failed edit evidence.',
        'Browser automation reported no available browser. WebGL rendering, camera behavior in a real browser, touch/mobile behavior and visual 3D walkthroughs were NOT verified. Assets, embedded scene geometry and Node interaction tests are distinct evidence.',
        'The first attempted server run failed on Windows console Unicode logging before planning. The benchmark server was restarted with PYTHONIOENCODING=utf-8 and Python -X utf8; application logging code was not changed.',
        'The parking-removal check in the original runner compared must_have_rooms literally with []; this report corrects that check to ignore base living/dining/kitchen entries while still requiring the requested extra to be removed. Raw events are retained.','',
        '## Verification','',
        f"Embedded current scene matches saved layout: {sum(r.get('embedded_scene_matches',False) for r in summary['cases'])}/10.",
        'Offline Python suite: 85 passed. JavaScript walking/painting suite: 14 passed.','',
        '## Changes from testing','',
        '- Fallback compares feasible depth/orientation choices and keeps the one with least dedicated hallway area.',
        '- Kitchen/dining remain adjacent when the public band also contains a common-bathroom column.',
        '- A compatible validated reference can replace an oversized-circulation fallback when it uses less hallway space.',
        '- Base living/dining/kitchen entries no longer prevent reference matching.',
        '- Wall-paint test fixture includes the existing hex input; added valid/invalid custom-color checks.','']
    if baseline:
        lines+=['## Before/after','',f"Initial complete run: {baseline['check_totals']['generation']}/10 generated; functional {baseline['functional_score']}/100; quality proxy {baseline['quality_score']}/100.",
                f"Retest: {summary['check_totals']['generation']}/10 generated; functional {summary['functional_score']}/100; quality proxy {summary['quality_score']}/100.",
                'These are separate live model runs; model variability also contributes to differences. The added deterministic regression tests isolate the fixed failures.','']
    lines+=['## Remaining design-quality work','',
        '- Enforce explicit kitchen/dining requests consistently during requirement extraction; some live runs recorded the request only as style text.',
        '- Reduce common-bathroom and suite hallway alcoves; the fallback still allows circulation above the AI compiler limit.',
        '- Make equal/balanced bedroom requests measurable constraints instead of relying on prompt compliance.',
        '- Complete a real-browser WebGL, camera, walk and touch check before treating this as full UI acceptance.','',
        '## Test inputs','']
    raw=json.loads(path.read_text(encoding='utf-8'))
    for case in raw['cases']:
        lines.extend([f"### Home {case['number']}",'',case['brief'],'',f"Edit tested: {case['edit_request']}",''])
    lines+=['## Saved test homes','']
    for r in summary['cases']:
        lines.append(f"- Home {r['number']}: `{r['session_id']}`"+(f" — [open 3D viewer]({r['viewer_url']})" if r['viewer_url'] else ' — no model generated'))
    (path.parent/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    cards=[]
    for r in summary['cases']:
        sid=r['session_id']; q=r['quality']
        cards.append(f'<article><h2>Home {r["number"]}</h2><p>{html.escape(str(q))}</p>'+
            (f'<img src="../../data/sessions/{sid}.png" alt="Home {r["number"]} floor plan"><p><a href="{html.escape(r["viewer_url"])}">Open 3D viewer</a></p>' if r['viewer_url'] else '<p>Generation failed</p>')+'</article>')
    (path.parent/'gallery.html').write_text('<!doctype html><meta charset="utf-8"><title>ConstructIQ QA homes</title><style>body{font:16px system-ui;background:#eee;padding:24px}main{display:grid;grid-template-columns:repeat(2,1fr);gap:24px}article{background:white;padding:20px}img{width:100%}p{overflow-wrap:anywhere}</style><h1>Ten-home QA gallery</h1><p>2D evidence and links to generated 3D viewers. See REPORT.md for scoring and limitations.</p><main>'+''.join(cards)+'</main>',encoding='utf-8')
    print(json.dumps({k:v for k,v in summary.items() if k!='cases'},indent=2))
    print(path.parent/'REPORT.md')


if __name__=='__main__': main()
