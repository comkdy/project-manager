"""Disposable local PostgreSQL benchmark; never mounts/replaces the user's DB.

3.19M measurements over 20 weeks; real FastAPI HTTP, metadata/auth/query paths,
5/30 simultaneous clients, seven cards per client. Report excludes credentials.
"""
import asyncio
import argparse
import csv
import io
import json
import multiprocessing
import os
from pathlib import Path
import socket
import statistics
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from contextlib import AsyncExitStack

import httpx
import psycopg
from psycopg import sql
from sqlalchemy.engine import URL

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'backend'))


def percentile(values,p):
    values=sorted(values)
    return round(values[min(len(values)-1,int((len(values)-1)*p))],2) if values else None


def seed(connection):
    from demo import seed as fixtures
    today=datetime.now(timezone(timedelta(hours=9)))
    monday=today.replace(hour=0,minute=0,second=0,microsecond=0)-timedelta(days=today.weekday())
    original_lot=fixtures.lot_row
    def lot_row(i):
        row=list(original_lot(i))
        row[-1]=monday-timedelta(weeks=19-(i-1)//3)
        return tuple(row)
    fixtures.lot_row=lot_row
    with connection.cursor() as cursor:
        cursor.execute((ROOT/'demo/schema.sql').read_text())
        rows={'projects':iter(fixtures.PROJECTS),
              'equipment':iter((i,f'EQ-{i}',f'Tool {i}','process') for i in range(1,8)),
              'lots':(lot_row(i) for i in range(1,61))}
        counts={}
        for table in ['projects','equipment','lots','wafers','inline_measurements','wat_measurements','die_results']:
            stream=rows.get(table) if table in rows else fixtures.rows_for(table,20261021,60,1400)
            count=0
            with cursor.copy(sql.SQL('COPY {} FROM STDIN WITH (FORMAT CSV)').format(sql.Identifier(table))) as copy:
                buffer=io.StringIO();writer=csv.writer(buffer,lineterminator='\n')
                for row in stream:
                    writer.writerow(row);count+=1
                    if count%5000==0:
                        copy.write(buffer.getvalue());buffer.seek(0);buffer.truncate(0)
                if buffer.tell():copy.write(buffer.getvalue())
            counts[table]=count
            print(f'Fixture {table}: {count:,}',flush=True)
        cursor.execute((ROOT/'demo/indexes.sql').read_text())
        cursor.execute('GRANT SELECT ON ALL TABLES IN SCHEMA public TO wafer_source')
    connection.commit()
    connection.autocommit=True
    connection.execute('ANALYZE')
    start=monday-timedelta(weeks=3)
    recent={table:connection.execute(sql.SQL('SELECT count(*) FROM {} WHERE measured_at >= %s').format(sql.Identifier(table)),(start,)).fetchone()[0]
            for table in ['wafers','inline_measurements','wat_measurements','die_results']}
    iso=start.isocalendar()
    return counts,recent,{'year':iso.year,'week':iso.week}


async def measure(base,cards,week,diagnostics=None,clear=None,per_user=False):
    if clear is None:
        from app.query import clear_cache
    else:
        clear_cache=clear
    cases=[]
    async with AsyncExitStack() as stack:
        clients=[await stack.enter_async_context(httpx.AsyncClient(base_url=base,timeout=120,
                 limits=httpx.Limits(max_connections=8 if per_user else 240,max_keepalive_connections=8 if per_user else 240)))
                 for _ in range(30 if per_user else 1)]
        async def request(client,card,context):
            started=time.perf_counter()
            try:
                response=await client.post('/api/cards/query',json={'card':card,'context':context},
                                          headers={'x-benchmark-start':str(started)} if diagnostics else None)
            except httpx.HTTPError as error:
                return {'ms':(time.perf_counter()-started)*1000,'status':0,'bytes':0,
                        'cached':False,'rows':0,'error':type(error).__name__}
            ms=(time.perf_counter()-started)*1000
            body=response.json() if 'application/json' in response.headers.get('content-type','') else {}
            return {'ms':ms,'status':response.status_code,'bytes':len(response.content),
                    'cached':body.get('meta',{}).get('cached',False),'rows':len(body.get('rows',[]))}
        async def user(context,index=0):
            start=time.perf_counter()
            results=await asyncio.gather(*(request(clients[index] if per_user else clients[0],card,context) for card in cards))
            return (time.perf_counter()-start)*1000,results
        scenarios=[(5,'full','cold'),(5,'four_weeks','cold'),(5,'four_weeks','warm'),
                   (30,'four_weeks','cold'),(30,'four_weeks','warm')]
        for users,period,cache in scenarios:
            context={'period_start':week} if period=='four_weeks' else {}
            if cache=='cold': clear_cache()
            else: await user(context)
            durations=[];all_requests=[]
            if diagnostics:diagnostics.reset()
            rounds=3
            wall=time.perf_counter()
            for _ in range(rounds):
                if cache=='cold': clear_cache()
                results=await asyncio.gather(*(user(context,i) for i in range(users)))
                for duration,requests in results:
                    durations.append(duration);all_requests.extend(requests)
            elapsed=time.perf_counter()-wall
            latencies=[r['ms'] for r in all_requests]
            result={'users':users,'period':period,'application_cache':cache,'rounds':rounds,
                    'requests':len(all_requests),'errors':sum(r['status']!=200 for r in all_requests),
                    'status_counts':{str(status):sum(r['status']==status for r in all_requests) for status in {r['status'] for r in all_requests}},
                    'transport_errors':{error:sum(r.get('error')==error for r in all_requests) for error in {r['error'] for r in all_requests if 'error' in r}},
                    'request_p50_ms':percentile(latencies,.5),'request_p95_ms':percentile(latencies,.95),
                    'dashboard_p50_ms':percentile(durations,.5),'dashboard_p95_ms':percentile(durations,.95),
                    'dashboard_max_ms':round(max(durations),2),'requests_per_second':round(len(all_requests)/elapsed,2),
                    'cache_hits':sum(r['cached'] for r in all_requests),'max_returned_rows':max(r['rows'] for r in all_requests),
                    'mean_dashboard_bytes':round(sum(r['bytes'] for r in all_requests)/len(durations))}
            cases.append(result);print(json.dumps(result),flush=True)
            if diagnostics:result['diagnostics']=diagnostics.summary()
    return cases


class RemoteControls:
    def __init__(self,pipe):self.pipe=pipe
    def call(self,command):
        self.pipe.send((command,None))
        return self.pipe.recv()
    def clear(self):self.call('clear')
    def reset(self):self.call('reset')
    def summary(self):return self.call('summary')


def client_process(pipe,base,cards,week,diagnostics,per_user):
    controls=RemoteControls(pipe)
    try:
        result=asyncio.run(measure(base,cards,week,controls if diagnostics else None,controls.clear,per_user))
        pipe.send(('result',result))
    except Exception as error:
        print('Load generator failed: '+type(error).__name__,flush=True)
        pipe.send(('error',type(error).__name__))
    finally:
        pipe.close()


def isolated_measure(base,cards,week,diagnostics,per_user=False):
    from app.query import clear_cache
    context=multiprocessing.get_context('spawn')
    parent,child=context.Pipe()
    process=context.Process(target=client_process,args=(child,base,cards,week,bool(diagnostics),per_user))
    process.start();child.close()
    try:
        while True:
            if not parent.poll(1):
                if not process.is_alive():raise RuntimeError('Load generator exited without results')
                continue
            command,value=parent.recv()
            if command=='result':return value
            if command=='error':raise RuntimeError('Isolated load generator failed: '+value)
            if command=='clear':clear_cache();parent.send(None)
            elif command=='reset':diagnostics.reset();parent.send(None)
            elif command=='summary':parent.send(diagnostics.summary())
            else:raise RuntimeError('Unexpected load generator command')
    finally:
        parent.close();process.join(timeout=5)
        if process.is_alive():process.terminate();process.join(timeout=5)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--label',default='latest')
    parser.add_argument('--diagnostics',action='store_true')
    parser.add_argument('--client-process',action='store_true',help='Keep the load generator outside the app Python process/GIL')
    parser.add_argument('--per-user-clients',action='store_true',help='Use an independent HTTP connection pool per user')
    parser.add_argument('--compare-baseline',action='store_true',help='Pair the previous chart response path with the optimized path on one fixture')
    args=parser.parse_args()
    if not args.label.replace('-','').replace('_','').isalnum():raise ValueError('Invalid report label')
    runtime=json.loads((ROOT/'.local/runtime.json').read_text(encoding='utf-8-sig'))
    if (runtime.get('marker')!='wafer-local-demo-v1' or runtime['port']!=55432
            or Path(runtime['data_dir']).resolve()!=(ROOT/'.local/pgdata').resolve()):
        raise RuntimeError('Only the workspace-managed loopback cluster is allowed')
    run_id=uuid.uuid4().hex[:12]
    dbname='wafer_dashboard_loadtest_'+run_id
    marker='project-manager-disposable-loadtest:'+run_id
    admin=psycopg.connect(host='127.0.0.1',port=55432,dbname='postgres',user='wafer_local_admin',password=runtime['admin_password'],autocommit=True)
    created=False;server=None;thread=None;report={}
    try:
        admin.execute(sql.SQL('CREATE DATABASE {} OWNER wafer_metadata').format(sql.Identifier(dbname)));created=True
        admin.execute(sql.SQL('COMMENT ON DATABASE {} IS {}').format(sql.Identifier(dbname),sql.Literal(marker)))
        admin.execute(sql.SQL('REVOKE ALL ON DATABASE {} FROM PUBLIC').format(sql.Identifier(dbname)))
        admin.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO wafer_source').format(sql.Identifier(dbname)))
        with psycopg.connect(host='127.0.0.1',port=55432,dbname=dbname,user='wafer_local_admin',password=runtime['admin_password']) as connection:
            connection.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
            counts,recent,week=seed(connection)
        # Secrets stay in this process environment and are never written to reports/files.
        os.environ['APP_DATABASE_URL']=URL.create('postgresql+psycopg',username='wafer_metadata',password=runtime['app_password'],host='127.0.0.1',port=55432,database=dbname).render_as_string(hide_password=False)
        os.environ['SOURCE_DATABASE_URL']=URL.create('postgresql+psycopg',username='wafer_source',password=runtime['source_password'],host='127.0.0.1',port=55432,database=dbname).render_as_string(hide_password=False)
        os.environ.update(DEMO_MODE='true',AUTH_MODE='local_admin',LLM_BASE_URL='',LLM_MODEL='')
        import uvicorn
        from app.main import app
        from app import db
        baseline=None
        if args.compare_baseline:
            from legacy_chart_baseline import BaselineSwitch
            baseline=BaselineSwitch()
        diagnostics=None
        if args.diagnostics:
            from query_diagnostics import Diagnostics
            diagnostics=Diagnostics(app)
        # Bind port 0 once; the OS chooses an isolated loopback HTTP port.
        listener=socket.socket();listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(diagnostics.asgi if diagnostics else app,log_level='error',access_log=False))
        thread=threading.Thread(target=server.run,kwargs={'sockets':[listener]},daemon=True);thread.start()
        deadline=time.monotonic()+60
        while not server.started and thread.is_alive() and time.monotonic()<deadline:time.sleep(.1)
        if not server.started:raise RuntimeError('Isolated API startup failed')
        base=f'http://127.0.0.1:{port}'
        with httpx.Client(base_url=base,timeout=60) as client:
            response=client.get('/api/auth/session');response.raise_for_status()
            assert response.json()['status']=='authenticated'
            response=client.get('/api/dashboards');response.raise_for_status()
            cards=response.json()['cards']
            assert len(cards)==7
            # This map displays a wafer in the recent range, not the oldest wafer.
            next(c for c in cards if c['kind']=='wafer')['filters']=[{'column':'wafer_id','op':'eq','value':1201}]
        report={'created_at':datetime.now(timezone.utc).isoformat(),'source_counts':counts,'recent_counts':recent,
                'period_start':week,'history_weeks':20,'cards_per_dashboard':len(cards),
                'scope':'Real loopback HTTP, single app worker, PostgreSQL source and metadata; browser rendering not included',
                'cache_note':'Cold clears application results each wave; PostgreSQL and OS caches are warm. Warm preloads the same board.',
                'label':args.label, 'client_process':args.client_process,'per_user_clients':args.per_user_clients}
        def run_cases():
            return (isolated_measure(base,cards,week,diagnostics,args.per_user_clients) if args.client_process
                    else asyncio.run(measure(base,cards,week,diagnostics,per_user=args.per_user_clients)))
        if baseline:
            baseline.active=True
            print('Paired baseline: previous validation/cache/response path',flush=True)
            report['baseline_cases']=run_cases()
            if diagnostics:report['baseline_query_plans']=diagnostics.explain()
            baseline.active=False
        report['cases']=run_cases()
        if diagnostics:report['query_plans']=diagnostics.explain()
        path=ROOT/'.local'/('dashboard-load-test.json' if args.label=='latest' else f'dashboard-load-test-{args.label}.json')
        path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    finally:
        if server:
            server.should_exit=True
            if thread:thread.join(timeout=15)
        if 'app.db' in sys.modules:
            db=sys.modules['app.db']
            if db.engine:db.engine.dispose()
            if db.source_engine:db.source_engine.dispose()
        if created:
            actual=admin.execute("SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=%s",(dbname,)).fetchone()
            if actual!=(marker,):raise RuntimeError('Refusing cleanup: disposable DB identity mismatch')
            admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(dbname)))
            print('Disposable load-test database removed. Existing DBs and app presets were not changed.',flush=True)
        admin.close()


if __name__=='__main__':
    try:main()
    except Exception as error:
        # Driver exceptions can contain connection details; only print a safe type.
        print('Load test failed: '+type(error).__name__,flush=True)
        import traceback
        print([(Path(frame.filename).name,frame.lineno) for frame in traceback.extract_tb(error.__traceback__)],flush=True)
        sys.exit(1)
