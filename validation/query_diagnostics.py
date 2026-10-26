"""Benchmark-only timing hooks. No SQL text, bindings or credentials in output."""
from collections import defaultdict
from contextvars import ContextVar
from functools import wraps
from statistics import mean
import threading
import time

from sqlalchemy import event

current = ContextVar('benchmark_timings', default=None)
chart_context = ContextVar('benchmark_chart', default=None)


class Diagnostics:
    def __init__(self, app):
        self.records=[]
        self.active=0
        self.peak=0
        self.statements={}
        self.lock=threading.Lock()
        from app import query, main, auth, db
        self.app=app
        import fastapi.routing
        from starlette.responses import JSONResponse
        self.wrap(fastapi.routing, 'jsonable_encoder', 'response_conversion')
        self.wrap(JSONResponse, 'render', 'response_json')
        self.wrap(main, 'load_active_source', 'binding')
        self.wrap(auth, 'local_user', 'auth')
        self.wrap(query, 'load_dataset', 'dataset')
        self.wrap(query, 'validate_dataset', 'definition')
        self.wrap(query, 'validate_card', 'validation')
        self.wrap(query, 'execute_rows', 'rows')
        compute=query._compute_chart
        execute=query.execute_rows
        def compute_chart(card,context,compiled):
            token=chart_context.set((card.id,card.kind,'four_weeks' if context.period_start else 'full'))
            try:return compute(card,context,compiled)
            finally:chart_context.reset(token)
        def execute_chart(statement,params=None):
            key=chart_context.get()
            if key:
                with self.lock:self.statements[key]=(statement,dict(params or {}))
            return execute(statement,params)
        query._compute_chart=compute_chart
        query.execute_rows=execute_chart
        self.wrap(main, 'chart_query', 'chart')
        # After optimization the API may use its own serialized response path.
        if hasattr(main, 'chart_response'): self.wrap(main,'chart_response','chart')
        for label,engine in [('metadata',db.engine),('source',db.source_engine)]:
            if engine is None:continue
            @event.listens_for(engine, 'before_cursor_execute')
            def before(connection,cursor,statement,parameters,context,many):
                context._benchmark_started=time.perf_counter()
            def after(connection,cursor,statement,parameters,context,many,label=label):
                record=current.get()
                if record is not None:
                    record[label+'_sql_ms']+=(time.perf_counter()-context._benchmark_started)*1000
                    record[label+'_sql_count']+=1
            event.listen(engine, 'after_cursor_execute',after)
        # Avoid inserting diagnostic HTTP middleware into production code.
        original=app
        self.asgi=TimingMiddleware(original,self)

    def wrap(self,module,name,label):
        original=getattr(module,name)
        @wraps(original)
        def wrapped(*args,**kwargs):
            record=current.get();started=time.perf_counter()
            try:return original(*args,**kwargs)
            finally:
                if record is not None:record[label+'_ms']+=(time.perf_counter()-started)*1000
        setattr(module,name,wrapped)

    def reset(self):
        with self.lock:
            self.records.clear()
            self.peak=0

    def summary(self):
        with self.lock:records=list(self.records)
        def p95(values):
            values=sorted(values)
            return round(values[int((len(values)-1)*.95)],2)
        fields=sorted({key for row in records for key in row})
        return {'requests':len(records),'peak_active_requests':self.peak,'mean':{k:round(mean(r.get(k,0) for r in records),2) for k in fields},
                'p95':{k:p95([r.get(k,0) for r in records]) for k in fields}}

    def explain(self):
        from app.query import source_connection
        from sqlalchemy import text
        results=[]
        with source_connection() as connection:
            for (card,kind,period),(statement,params) in self.statements.items():
                plan=connection.execute(text('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) '+statement),params).scalar_one()[0]
                results.append({'card':card,'kind':kind,'period':period,'execution_ms':plan['Execution Time'],'plan':plan['Plan']})
        return results


class TimingMiddleware:
    def __init__(self,app,diagnostics):self.app=app;self.diagnostics=diagnostics
    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or scope['path']!='/api/cards/query':
            return await self.app(scope,receive,send)
        record=defaultdict(float);token=current.set(record);started=time.perf_counter()
        with self.diagnostics.lock:
            self.diagnostics.active+=1
            self.diagnostics.peak=max(self.diagnostics.peak,self.diagnostics.active)
        sent=dict(scope['headers']).get(b'x-benchmark-start')
        if sent:record['arrival_wait_ms']=(started-float(sent))*1000
        async def measured_send(message):
            if message['type']=='http.response.start':record['headers_ms']=(time.perf_counter()-started)*1000
            await send(message)
        try:await self.app(scope,receive,measured_send)
        finally:
            record['total_ms']=(time.perf_counter()-started)*1000
            with self.diagnostics.lock:
                self.diagnostics.records.append(dict(record))
                self.diagnostics.active-=1
            current.reset(token)
