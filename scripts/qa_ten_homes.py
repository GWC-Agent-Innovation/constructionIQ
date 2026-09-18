"""Live API benchmark. Creates QA projects; never edits pre-existing projects."""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.schemas import HouseSpec, Layout
from agent.validator import validate_layout
from agent.layout_solver import _split_extras

CASES = [
    (30,30,1,1,'south',[],[], 'Remove the separate dining room and combine dining with living.', {'include_dining_room':False}),
    (26,30,2,1,'west',[],[], 'Remove bedroom 2.', {'num_bedrooms':1}),
    (40,40,2,2,'south',[1],[], 'Remove the common bathroom.', {'num_bathrooms':1}),
    (40,40,2,3,'north',[1,2],[], 'Remove the common bathroom.', {'num_bathrooms':2}),
    (40,50,3,2,'east',[1],[], 'Remove bedroom 3.', {'num_bedrooms':2}),
    (45,45,3,3,'west',[1,2],[], 'Remove the common bathroom.', {'num_bathrooms':2}),
    (30,40,2,2,'south',[],[], 'Remove bedroom 2.', {'num_bedrooms':1}),
    (40,40,1,1,'east',[],['office'], 'Remove the office from this design.', {'must_have_rooms':[]}),
    (40,50,2,2,'north',[1],['parking'], 'Remove parking from this design.', {'must_have_rooms':[]}),
    (50,50,3,3,'south',[1,2],['pooja room'], 'Remove bedroom 3.', {'num_bedrooms':2}),
]
WEIGHTS = dict(requirements=15, generation=25, assets=10, edit=20, paint=10, undo=10, stale_protection=10)


def quality(layout):
    rooms = layout['rooms']
    area = layout['plot_width_ft'] * layout['plot_length_ft']
    halls = sum(r['width']*r['length'] for r in rooms if 'hallway' in r['name'].lower())
    living = sum(r['width']*r['length'] for r in rooms if r['name'].lower() == 'living room')
    return dict(hallway_percent=round(100*halls/area,2), hallway_smaller_than_living=halls < living,
                circulation_pass=halls/area <= .15+1e-6, design_name=layout['design_name'], source=layout['planning_source'])


def main():
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    out = Path('reports') / f'qa-ten-homes-{stamp}'
    out.mkdir(parents=True)
    report = dict(started=stamp, mode='Live Ollama via local API; no mocks', weights=WEIGHTS, cases=[])
    def save():
        report['functional_score'] = round(sum(sum(WEIGHTS[k] for k,v in r['checks'].items() if v) for r in report['cases']) / len(CASES),1)
        (out/'results.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    def valid(state):
        return bool(state.get('layout')) and not validate_layout(Layout.model_validate(state['layout']),HouseSpec.model_validate(state['spec']))
    with httpx.Client(base_url='http://127.0.0.1:8000',timeout=300) as client:
        client.get('/health').raise_for_status()
        for index,(w,d,beds,baths,facing,attached,extras,edit,expected_edit) in enumerate(CASES,1):
            start=time.monotonic()
            brief=(f'Design a single-floor home, plot {w} feet wide and {d} feet long, {facing}-facing entrance. '
                   f'Exactly {beds} bedrooms and {baths} bathrooms total. '
                   +(f'Attach one bathroom each to bedrooms {attached}; all remaining bathrooms are common. ' if attached else 'All bathrooms are common, no attached bathrooms. ')
                   +'Include living room, separate dining room, kitchen'+(', '+', '.join(extras) if extras else '')+'. '
                   +('No parking. ' if 'parking' not in extras else '')
                   +'Keep hallway space compact, bedrooms balanced, and kitchen next to dining. Record these requirements and prepare to generate.')
            row=dict(number=index,brief=brief,edit_request=edit,checks={k:False for k in WEIGHTS},events=[])
            report['cases'].append(row)
            print(f'CASE {index}/10 START {w}x{d} {beds} beds {baths} baths {facing}',flush=True)
            try:
                response=client.post('/sessions',json={'project_name':f'QA {stamp} Home {index:02d}'})
                response.raise_for_status()
                sid=response.json()['session']['session_id']; row['session_id']=sid
                def post(endpoint,payload):
                    response=client.post(f'/sessions/{sid}/{endpoint}',json=payload)
                    row['events'].append(dict(endpoint=endpoint,status=response.status_code,response=response.json()))
                    save()
                    return response
                response=post('messages',{'message':brief})
                response.raise_for_status()
                s=response.json()['session']['spec']
                row['checks']['requirements']=(s['plot_width_ft']==w and s['plot_length_ft']==d and s['num_bedrooms']==beds
                    and s['num_bathrooms']==baths and s['entrance_facing']==facing and (s['num_floors'] or 1)==1
                    and sorted(s['attached_bathroom_bedrooms'] or [])==attached and s['include_dining_room']
                    and all(any(extra in name.lower() for name in s['must_have_rooms']) for extra in extras)
                    and ('parking' in extras or not any('parking' in name.lower() for name in s['must_have_rooms'])))
                response=post('generate',{'build_3d':True})
                if response.status_code != 200:
                    row['error']='Initial generation failed'; continue
                data=response.json(); baseline=data['session']
                row['checks']['generation']=valid(baseline) and baseline['model_3d_version']==baseline['layout']['version']
                row['quality']=quality(baseline['layout'])
                row['viewer_url']='http://127.0.0.1:8000'+data['viewer_url'] if data.get('viewer_url') else None
                downloads={name:client.get(url) for name,url in data['assets'].items()}
                row['checks']['assets']=(len(downloads)==3 and all(r.status_code==200 and len(r.content)>100 for r in downloads.values())
                    and any(r.content.startswith(b'\x89PNG') for r in downloads.values())
                    and any('three' in r.text.lower() for n,r in downloads.items() if n.endswith('.html')))
                room=baseline['layout']['rooms'][0]['id']; version=baseline['layout']['version']
                paint=post('wall-paint',dict(layout_version=version,room_id=room,wall='north',color='#89ABCD'))
                baseline=client.get(f'/sessions/{sid}').json()['session']
                row['checks']['paint']=(paint.status_code==200 and baseline['layout']['wall_colors'].get(room+':north')=='#89abcd')
                response=post('messages',{'message':edit,'auto_generate':True})
                data=response.json(); current=data.get('session',baseline)
                applied=data.get('edit_applied',False)
                row['checks']['edit']=(response.status_code==200 and applied and valid(current)
                    and all((not any(_split_extras(HouseSpec.model_validate(current['spec']))))
                            if k=='must_have_rooms' and v==[] else current['spec'][k]==v
                            for k,v in expected_edit.items())
                    and current['layout']['version']>version and current['model_3d_version']==current['layout']['version']
                    and bool(data.get('viewer_url')) and client.get(data['viewer_url']).status_code==200)
                if not applied:
                    row['failed_edit_preserved_design']=(current['spec']==baseline['spec'] and current['layout']==baseline['layout'])
                else:
                    row['edited_quality']=quality(current['layout'])
                stale=post('wall-paint',dict(layout_version=version if applied else version+999,room_id=room,wall='north',color='#112233'))
                row['checks']['stale_protection']=stale.status_code==409
                if applied:
                    response=post('messages',{'message':'undo last edit','auto_generate':True})
                    data=response.json(); restored=data.get('session',{})
                    row['checks']['undo']=(response.status_code==200 and data.get('edit_applied',False) and valid(restored)
                        and restored['spec']==baseline['spec'] and restored['layout']['rooms']==baseline['layout']['rooms']
                        and restored['layout']['wall_colors']==baseline['layout']['wall_colors'])
                    if data.get('viewer_url'): row['viewer_url']='http://127.0.0.1:8000'+data['viewer_url']
            except Exception as exc:
                row['error']=f'{type(exc).__name__}: {exc}'
            finally:
                row['seconds']=round(time.monotonic()-start,1)
                save()
                print(f'CASE {index}/10 DONE checks={row["checks"]} quality={row.get("quality")} error={row.get("error")} seconds={row["seconds"]}',flush=True)
    print(f'REPORT {out}/results.json SCORE {report["functional_score"]}/100',flush=True)


if __name__=='__main__': main()
