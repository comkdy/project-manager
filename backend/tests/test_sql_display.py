import sqlglot
from sqlglot import exp

from app.raw_sql import form_to_sql
from app.schemas import DatasetSpec
from app.source_discovery import default_sql
from app.sql_display import generated_sql


def test_generated_sql_omits_only_unnecessary_identifier_quotes():
    original = '''SELECT "s"."lot_code", "s"."MixedCase", "s"."select", "s"."Batch ID",
                         'keep "lot_code" and O''Brien' AS "literal"
                  FROM "public"."Measurements" AS "s"'''
    tree = sqlglot.parse_one(original, read="postgres")
    sql = generated_sql(tree)
    assert 's.lot_code' in sql
    assert 's."MixedCase"' in sql and 's."select"' in sql and 's."Batch ID"' in sql
    assert 'public."Measurements" AS s' in sql
    assert "'keep \"lot_code\" and O''Brien'" in sql
    # Compare semantics after identifier quoting is normalized, not text.
    def normalized(query):
        for node in query.find_all(exp.Identifier):
            node.set("quoted", True)
        return query
    assert normalized(sqlglot.parse_one(sql, read="postgres")) == normalized(tree.copy())


def test_default_and_simple_queries_share_readable_identifiers():
    source = {"id":"test", "schema_name":"public", "table_name":"samples", "project_column":None,
              "primary_key":"lot_code", "columns":[{"name":"lot_code","type":"text"},{"name":"wf_id","type":"integer"}]}
    assert default_sql(source) == "SELECT\n  lot_code,\n  wf_id\nFROM public.samples"
    sql = form_to_sql(DatasetSpec(source_id="test"), source)
    assert "s.lot_code" in sql and "s.wf_id" in sql and "public.samples AS s" in sql
    assert '"' not in sql
