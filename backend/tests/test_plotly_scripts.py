import json
import subprocess
import sys
import time

import pytest
from fastapi import HTTPException

from app.plotly_policy import ScriptError, parse_script
from app.plotly_scripts import run_script

ROWS = [{'group':'A','x':1,'y':2},{'group':'A','x':2,'y':5},{'group':'B','x':3,'y':4}]
COLS = [{'name':k,'type':'text'} for k in ROWS[0]]


@pytest.mark.parametrize('script', [
    'import os', 'from pathlib import Path', 'while True: pass', 'fig = df.__class__',
    'fig = [x for x in df]', 'def render(): pass', 'fig = lambda: 1', 'fig = 2 ** 999999',
])
def test_rejects_non_chart_language(script):
    with pytest.raises(ScriptError): parse_script(script)


@pytest.mark.parametrize('statement', [
    'import pandas as pd', 'import plotly.express as px',
    'import plotly.graph_objects as go', 'from plotly.subplots import make_subplots',
    'import pandas as alias', 'from plotly.subplots import make_subplots as alias',
    'if False:\n    import pandas as pd',
])
def test_every_import_is_rejected_even_preloaded_modules(statement):
    with pytest.raises(ScriptError, match='import는 사용할 수 없습니다'):
        parse_script(statement + '\nfig = go.Figure()')
    with pytest.raises(HTTPException, match='import는 사용할 수 없습니다'):
        run_script(statement + '\nfig = go.Figure()', ROWS, COLS)


@pytest.mark.parametrize('script', [
    'open(".env").read()', 'fig=go.Figure(); fig.write_html("x.html")',
    'df.to_pickle("x")', 'df.groupby("group").agg("to_csv")',
    'df.pivot_table("y", "group", None, "to_pickle")',
    'pd.crosstab(df["group"],df["x"],values=df["y"],aggfunc="to_csv")',
    'pd.read_csv("https://example.com")', 'df.eval("x+1")',
    'fig=go.Figure(); fig.update_layout(images=[dict(source="https://example.com/x")])',
    'fig=go.Figure(go.Scattergeo(lon=[1],lat=[1]))',
])
def test_worker_has_no_io_eval_or_remote_render_capabilities(script):
    with pytest.raises(HTTPException) as error: run_script(script, ROWS, COLS)
    assert error.value.status_code==422


def test_subplots_dataframe_transform_theme_and_fresh_process():
    script = '''grouped = df.groupby("group", as_index=False)["y"].sum()
fig = make_subplots(rows=1, cols=2, specs=[[{"type":"xy"},{"type":"domain"}]])
fig.add_trace(go.Bar(x=grouped["group"], y=grouped["y"]), row=1, col=1)
fig.add_trace(go.Pie(labels=grouped["group"], values=grouped["y"]), row=1, col=2)
fig.update_layout(font_color="red", width=9000)
private_variable = 123
'''
    result=run_script(script, ROWS, COLS)
    assert [t['type'] for t in result['data']]==['bar','pie']
    assert result['data'][1]['labels']==['A','B']
    assert result['layout']['font']['color']=='red' and 'width' not in result['layout']
    other=run_script('fig=px.scatter(df,x="x",y="y")', ROWS, COLS)
    assert other['layout']['template']['layout']['font']['color']=='#506778'
    with pytest.raises(HTTPException,match='private_variable'): run_script('fig=go.Figure(); fig.update_layout(title=str(private_variable))', ROWS,COLS)


def test_script_data_requirements_and_null_safe_division():
    script = '''require(len(df) > 0, "측정값이 필요합니다.")
df["ratio"] = df["y"] / df["x"].where(df["x"] != 0)
fig = go.Figure(go.Bar(x=df["x"].tolist(), y=df["ratio"].tolist()))
'''
    result = run_script(script, [{'x':0, 'y':3}, {'x':2, 'y':6}], COLS)
    assert result['data'][0]['y'] == [None, 3]
    with pytest.raises(HTTPException, match='측정값이 필요합니다'):
        run_script(script, [], COLS)
    with pytest.raises(HTTPException, match='where에는'):
        run_script('df.where(df["x"] > 0, inplace=True)', ROWS, COLS)


def test_direct_df_changes_are_local_to_one_card_execution():
    changed = run_script('df["y"] = df["y"] * 10\nfig=go.Figure(go.Bar(x=df["x"].tolist(),y=df["y"].tolist()))', ROWS, COLS)
    assert changed['data'][0]['y'] == [20, 50, 40]
    fresh = run_script('fig=go.Figure(go.Bar(x=df["x"].tolist(),y=df["y"].tolist()))', ROWS, COLS)
    assert fresh['data'][0]['y'] == [2, 5, 4]
    assert [row['y'] for row in ROWS] == [2, 5, 4]


def test_html_is_text_and_output_never_contains_executable_markup():
    result=run_script('fig=px.scatter(df,x="x",y="y"); fig.update_layout(title="<a href=https://example.com>click</a><b>ok</b>")',ROWS,COLS)
    assert '<a ' not in result['layout']['title']['text'] and '<b>ok</b>' in result['layout']['title']['text']


def test_converted_card_preserves_grouped_result_and_axes():
    card={'kind':'bar','title':'test','color':'group','options':{'palette':'warm','x_label':'Lot','y_label':'Mean'},'trellis':None}
    rows=[{'x':'A','y':5,'color':'G1'},{'x':'B','y':6,'color':'G2'}]
    result=run_script('fig.update_traces(opacity=0.7)',rows,[{'name':k} for k in rows[0]],card)
    assert len(result['data'])==2 and result['data'][0]['y']==[5]
    assert result['data'][0]['opacity']==0.7 and result['layout']['xaxis']['title']['text']=='Lot'


def test_extended_traces_support_sankey_links_and_scatter_matrix():
    result = run_script('fig=go.Figure(go.Sankey(node=dict(label=["A","B"]), link=dict(source=[0],target=[1],value=[3])))', ROWS, COLS)
    assert result['data'][0]['link']['source'] == [0]
    matrix = run_script('fig=px.scatter_matrix(df, dimensions=["x","y"], color="group")', ROWS, COLS)
    assert all(trace['type'] == 'splom' for trace in matrix['data'])


def test_converted_scatter_preserves_continuous_color():
    card={'kind':'scatter','title':'Colors','color':'value','options':{}}
    rows=[{'x':i,'y':i*2,'color':i*.1} for i in range(100)]
    result=run_script('fig.update_layout(hovermode="closest")',rows,[{'name':k} for k in rows[0]],card)
    assert len(result['data']) == 1
    assert result['data'][0]['marker']['showscale'] is True
    assert result['data'][0]['marker']['color'] == [r['color'] for r in rows]


def test_worker_timeout_is_bounded_and_slot_released(monkeypatch):
    from app import plotly_scripts as runner
    monkeypatch.setattr(runner,'TIMEOUT',0.001)
    started=time.monotonic()
    with pytest.raises(HTTPException,match='제한'): run_script('fig=go.Figure()',ROWS,COLS)
    assert time.monotonic()-started<4
    monkeypatch.setattr(runner,'TIMEOUT',12)
    assert run_script('fig=px.pie(df,names="group",values="y")',ROWS,COLS)['data']


def test_memory_exhaustion_does_not_damage_next_card():
    with pytest.raises(HTTPException) as error:
        run_script('for n in range(3):\n    df=pd.concat([df]*1000)\nfig=px.scatter(df,x="x",y="y")', ROWS, COLS)
    assert error.value.status_code == 422
    assert run_script('fig=px.scatter(df,x="x",y="y")', ROWS, COLS)['data']
