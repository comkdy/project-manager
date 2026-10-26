from app.dataset_reuse import query_key, reusable_dataset


def ds(id,sql,project_id=None):
    return {'id':id,'project_id':project_id,'spec':{'source_id':'source','raw_sql':sql}}


def test_matches_formatting_but_preserves_sql_meaning_and_scope():
    sql='SELECT lot_id, AVG(value) AS v FROM public.facts WHERE lot_cd = \'A\' GROUP BY lot_id'
    candidate=ds('owned',sql,1)
    definition={'spec':{'source_id':'source','raw_sql':sql.lower().replace("'a'","'A'")+' -- comment'}}
    assert reusable_dataset(definition,[ds('shared',sql),candidate],1)==candidate
    assert reusable_dataset(definition,[ds('shared',sql)],1) is None
    assert reusable_dataset(definition,[candidate],2) is None
    for altered in (sql.replace("'A'","'B'"),sql.replace('AVG','MAX'),sql+' LIMIT 10'):
        assert query_key({'source_id':'source','raw_sql':altered})!=query_key(candidate['spec'])
    assert query_key({'source_id':'other','raw_sql':sql})!=query_key(candidate['spec'])


def test_does_not_equate_differently_quoted_columns_or_broken_queries():
    assert query_key({'source_id':'s','raw_sql':'SELECT "LOT" FROM t'})!=query_key({'source_id':'s','raw_sql':'SELECT lot FROM t'})
    assert reusable_dataset({'spec':{'source_id':'s'}},[ds('x','SELECT 1')],None) is None
