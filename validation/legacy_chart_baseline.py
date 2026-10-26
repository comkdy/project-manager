"""Pre-optimization chart path for paired local benchmarks only.

Restores the former validation/read/deep-copy/JSON-response behavior. The SQL
computation is deliberately shared: no query, rows, filters or limits changed.
Never imported by the application or its startup scripts.
"""
import copy
import hashlib
import json
import threading
import time


class BaselineSwitch:
    def __init__(self):
        from app import query, main
        self.query=query
        self.active=False
        self.cache={}
        self.lock=threading.Lock()
        current_response=main.chart_response
        current_load=query.load_dataset
        current_clear=query.clear_cache
        current_period=query.apply_period
        def response(card,context):
            return self.chart(card,context) if self.active else current_response(card,context)
        def load(*args,**kwargs):
            return self.load(*args,**kwargs) if self.active else current_load(*args,**kwargs)
        def clear():
            current_clear()
            with self.lock:self.cache.clear()
        def period(compiled,card,context):
            result,metadata=current_period(compiled,card,context)
            if self.active and result.get('source_period'):
                from app.dashboard_period import filter_source,period_columns
                sources=[compiled['source'],*(s for _,s in compiled['relations'])]
                source=next(s for s in sources if s['id']==metadata['source_id'])
                column=next(c for c in period_columns(compiled)['columns']
                            if c['source_id']==metadata['source_id'] and c['column']==metadata['column'])
                start=context.period_start if card.period.use_dashboard else card.period.start
                authored=query.form_to_sql(compiled['spec'],compiled['source'],context.project_id,compiled['relations'])
                return {**compiled,'direct_sql':filter_source(authored,source,column,start)},metadata
            return result,metadata
        main.chart_response=response
        query.load_dataset=load
        query.clear_cache=clear
        query.apply_period=period

    def load(self,dataset_id,version=None):
        q=self.query
        with q.SessionLocal() as db:
            dataset=(db.scalar(q.select(q.DatasetVersion).where(q.DatasetVersion.id==dataset_id)
                               .order_by(q.DatasetVersion.version.desc()).limit(1)) if version is None
                     else db.get(q.DatasetVersion,(dataset_id,version)))
            if not dataset:raise q.HTTPException(404,'Dataset not found')
            if not q.TableVisibility.load(db).allows_spec(dataset.spec):
                raise q.HTTPException(404,'Hidden source')
            return dataset

    def chart(self,card,context):
        q=self.query
        compiled=q.validate_card(card)
        card=card.model_copy(update={'dataset_version':compiled['dataset_version']})
        context=q.scoped_context(compiled['project_id'],context)
        key=hashlib.sha256(json.dumps([q.database.workspace_token(),card.model_dump(),
            context.model_dump(exclude={'refresh','cursor'})],sort_keys=True).encode()).hexdigest()
        if not context.refresh:
            with self.lock:
                existing=self.cache.get(key)
                if existing and time.monotonic()-existing[0]<q.settings.cache_ttl_seconds:
                    result=copy.deepcopy(existing[1]);result['meta']['cached']=True
                    return result
        result=q._compute_chart(card,context,compiled)
        size=len(json.dumps(result,ensure_ascii=False).encode('utf-8'))
        with self.lock:
            for expired in [k for k,v in self.cache.items() if time.monotonic()-v[0]>=q.settings.cache_ttl_seconds]:
                self.cache.pop(expired,None)
            while self.cache and (len(self.cache)>=128 or sum(v[2] for v in self.cache.values())+size>16*1024*1024):
                self.cache.pop(min(self.cache,key=lambda k:self.cache[k][0]),None)
            if size<=16*1024*1024:self.cache[key]=(time.monotonic(),copy.deepcopy(result),size)
        return result
