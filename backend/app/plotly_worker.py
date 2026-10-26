"""Fresh, credential-free chart worker. The input is interpreted, never executed.

Run as a standalone -I Python process. This module must not import app/db/config.
"""
import ast
import html
import json
import operator
import re
import sys

# -I intentionally excludes the current directory. Only our fixed, trusted
# module directory is added, never a path supplied by a script or dataset.
from pathlib import Path
if len(sys.argv) > 1:
    # This fixed interpreter library path is supplied by our parent runner,
    # never by user code or the JSON request. Windows uses base Python to
    # avoid a venv redirector child before the resource job is attached.
    sys.path.insert(0, sys.argv[1])
sys.path.insert(0, str(Path(__file__).resolve().parent))
from plotly_policy import (AGGREGATES, DATA_METHODS, FIGURE_METHODS, GO, PX,
                           MAX_CONTROL_BYTES, MAX_OUTPUT_BYTES, MAX_TRACES,
                           MEMORY_BYTES, TIMEOUT, ScriptError, parse_script)


def limits():
    if sys.platform != 'win32':
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
        resource.setrlimit(resource.RLIMIT_CPU, (TIMEOUT, TIMEOUT))
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))


class Capability:
    def __init__(self, call):
        self.call = call


class Module:
    def __init__(self, name):
        self.name = name


class Interpreter:
    def __init__(self, rows, columns, figure=None):
        import pandas as pd
        import numpy as np
        import plotly.graph_objects as go
        import plotly.express as px
        from plotly.subplots import make_subplots
        from pandas.core.groupby.generic import DataFrameGroupBy, SeriesGroupBy
        self.pd, self.np, self.go, self.px = pd, np, go, px
        self.data_types = (pd.DataFrame, pd.Series, DataFrameGroupBy, SeriesGroupBy)
        self.steps = 0
        def subplots(*args, **kw):
            if args or not 1 <= kw.get('rows', 1) <= 12 or not 1 <= kw.get('cols', 1) <= 8 or kw.get('rows', 1)*kw.get('cols', 1)>64:
                raise ScriptError('subplot은 최대 12행·8열, 총 64개까지 사용할 수 있습니다.')
            return make_subplots(**kw)
        def require(condition, message):
            if not isinstance(message, str):
                raise ScriptError('require의 안내는 문자열이어야 합니다.')
            if not condition:
                raise ScriptError(message[:900])
        self.env = {'df': rows if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows, columns=[c['name'] for c in columns]),
                    'go': Module('go'), 'px': Module('px'), 'pd': Module('pd'),
                    'make_subplots': Capability(subplots), 'require': Capability(require),
                    'fig': go.Figure(figure) if figure else go.Figure(),
                    'len': Capability(len), 'str': Capability(str), 'float': Capability(float),
                    'int': Capability(int), 'list': Capability(list), 'dict': Capability(dict),
                    'range': Capability(lambda *args: list(range(*args)) if len(range(*args)) <= 1000 else self.fail('range는 최대 1,000개입니다.'))}
        self.reserved = {'go', 'px', 'pd', 'make_subplots', 'require', 'len', 'str', 'float', 'int', 'list', 'dict', 'range'}

    def fail(self, message):
        raise ScriptError(message)

    def tick(self):
        self.steps += 1
        if self.steps > 10000:
            self.fail('연산 횟수 제한을 초과했습니다.')

    def assign(self, target, value):
        if isinstance(target, ast.Name) and target.id not in self.reserved:
            self.env[target.id] = value
        elif isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (tuple, list)) and len(target.elts) == len(value):
            for key, item in zip(target.elts, value):
                self.assign(key, item)
        elif isinstance(target, ast.Subscript):
            obj, key = self.expr(target.value), self.expr(target.slice)
            if not isinstance(obj, (self.pd.DataFrame, dict)) or not isinstance(key, str):
                self.fail('컬럼 이름 또는 사전 키에만 값을 할당할 수 있습니다.')
            obj[key] = value
        else:
            self.fail('이 할당 방식은 지원하지 않습니다.')

    def block(self, nodes):
        for node in nodes:
            self.tick()
            if isinstance(node, ast.Assign):
                value = self.expr(node.value)
                for target in node.targets:
                    self.assign(target, value)
            elif isinstance(node, ast.Expr):
                self.expr(node.value)
            elif isinstance(node, ast.For):
                items = self.expr(node.iter)
                if not isinstance(items, (list, tuple, self.np.ndarray, self.pd.Series, self.pd.Index)) or len(items) > 1000:
                    self.fail('for 반복에는 1,000개 이하의 값 목록을 사용하세요.')
                for item in items:
                    self.assign(node.target, item)
                    self.block(node.body)
                self.block(node.orelse)
            elif isinstance(node, ast.If):
                self.block(node.body if self.expr(node.test) else node.orelse)
            else:
                self.fail('지원하지 않는 문장입니다.')

    def attribute(self, obj, name):
        if name.startswith('_'):
            self.fail('내부 객체에는 접근할 수 없습니다.')
        if isinstance(obj, Module):
            module, allowed = {'go':(self.go, GO), 'px':(self.px, PX),
                               'pd':(self.pd, {'to_datetime', 'to_numeric', 'DataFrame', 'Series', 'concat', 'crosstab', 'cut'})}[obj.name]
            if name in allowed:
                if obj.name == 'pd' and name == 'crosstab':
                    def crosstab(*args, **kwargs):
                        aggfunc = kwargs.get('aggfunc', args[5] if len(args)>5 else None)
                        if aggfunc is not None and (not isinstance(aggfunc,str) or aggfunc not in AGGREGATES):
                            self.fail('지원하지 않는 집계입니다.')
                        return self.pd.crosstab(*args, **kwargs)
                    return Capability(crosstab)
                return Capability(getattr(module, name))
        if isinstance(obj, self.go.Figure) and name in FIGURE_METHODS:
            return Capability(getattr(obj, name))
        if isinstance(obj, (self.pd.DataFrame, self.pd.Series)) and name == 'where':
            def where(*args, **kwargs):
                if len(args) > 2 or set(kwargs) - {'cond', 'other'}:
                    self.fail('where에는 조건과 대체 값만 사용할 수 있습니다.')
                return obj.where(*args, **kwargs)
            return Capability(where)
        if isinstance(obj, self.data_types) and name in DATA_METHODS:
            def call(*args, **kwargs):
                if name in {'agg', 'aggregate'}:
                    def check(value):
                        if isinstance(value, dict): return all(check(v) for v in value.values())
                        if isinstance(value, (list, tuple)): return all(check(v) for v in value)
                        return isinstance(value, str) and value in AGGREGATES
                    if not args or not check(args[0]) or kwargs:
                        self.fail('agg에는 mean, sum, min, max, count 등 지원하는 집계 이름을 사용하세요.')
                if name == 'astype' and (not args or args[0] not in ('float', 'int', 'str', 'bool', 'float64', 'int64', 'string')):
                    self.fail('astype에는 기본 숫자·문자·논리 자료형만 사용할 수 있습니다.')
                if name == 'pivot_table':
                    aggfunc = kwargs.get('aggfunc', args[3] if len(args)>3 else 'mean')
                    if not isinstance(aggfunc, str) or aggfunc not in AGGREGATES:
                        self.fail('지원하지 않는 집계입니다.')
                return getattr(obj, name)(*args, **kwargs)
            return Capability(call)
        if isinstance(obj, self.pd.DataFrame) and name in obj.columns:
            return obj[name]
        if isinstance(obj, (self.pd.DataFrame, self.pd.Series)) and name in {'columns', 'index', 'shape', 'empty'}:
            return getattr(obj, name)
        if isinstance(obj, self.np.ndarray) and name == 'tolist':
            return Capability(obj.tolist)
        self.fail(f'사용할 수 없는 속성 또는 함수입니다: {name}')

    def expr(self, node):
        self.tick()
        if isinstance(node, ast.Constant): return node.value
        if isinstance(node, ast.Name):
            if node.id not in self.env: self.fail(f'정의되지 않은 이름입니다: {node.id}')
            return self.env[node.id]
        if isinstance(node, ast.List): return [self.expr(x) for x in node.elts]
        if isinstance(node, ast.Tuple): return tuple(self.expr(x) for x in node.elts)
        if isinstance(node, ast.Dict): return {self.expr(k):self.expr(v) for k,v in zip(node.keys,node.values)}
        if isinstance(node, ast.Slice): return slice(*(self.expr(x) if x is not None else None for x in (node.lower,node.upper,node.step)))
        if isinstance(node, ast.Subscript):
            obj, key = self.expr(node.value), self.expr(node.slice)
            if not isinstance(obj, (*self.data_types, list, tuple, dict, str, self.np.ndarray, self.pd.Index)):
                self.fail('지원하지 않는 값 참조입니다.')
            return obj[key]
        if isinstance(node, ast.Attribute): return self.attribute(self.expr(node.value), node.attr)
        if isinstance(node, ast.Call):
            function = self.expr(node.func)
            if not isinstance(function, Capability): self.fail('허용된 차트·데이터 함수만 호출할 수 있습니다.')
            args = [self.expr(x) for x in node.args]
            kwargs = {kw.arg:self.expr(kw.value) for kw in node.keywords}
            return function.call(*args, **kwargs)
        if isinstance(node, ast.BinOp):
            left, right = self.expr(node.left), self.expr(node.right)
            # Prevent huge sequence allocations before entering a library call.
            if isinstance(node.op, ast.Mult) and isinstance(left, (str,list,tuple)) and isinstance(right,int) and len(left)*abs(right)>100000:
                self.fail('생성할 데이터가 너무 큽니다.')
            if isinstance(node.op, ast.Mult) and isinstance(right, (str,list,tuple)) and isinstance(left,int) and len(right)*abs(left)>100000:
                self.fail('생성할 데이터가 너무 큽니다.')
            op = {ast.Add:operator.add,ast.Sub:operator.sub,ast.Mult:operator.mul,ast.Div:operator.truediv,
                  ast.Mod:operator.mod,ast.BitAnd:operator.and_,ast.BitOr:operator.or_}[type(node.op)]
            return op(left, right)
        if isinstance(node, ast.UnaryOp):
            return {ast.USub:operator.neg,ast.UAdd:operator.pos,ast.Invert:operator.invert,ast.Not:operator.not_}[type(node.op)](self.expr(node.operand))
        if isinstance(node, ast.Compare):
            if len(node.ops) != 1: self.fail('비교는 하나씩 작성하고 & 또는 |로 결합하세요.')
            left, right = self.expr(node.left), self.expr(node.comparators[0])
            return {ast.Eq:operator.eq,ast.NotEq:operator.ne,ast.Lt:operator.lt,ast.LtE:operator.le,
                    ast.Gt:operator.gt,ast.GtE:operator.ge,ast.In:lambda a,b:a in b,ast.NotIn:lambda a,b:a not in b}[type(node.ops[0])](left,right)
        if isinstance(node, ast.IfExp): return self.expr(node.body if self.expr(node.test) else node.orelse)
        if isinstance(node, ast.BoolOp):
            for item in node.values:
                result = self.expr(item)
                if isinstance(node.op,ast.And) and not result or isinstance(node.op,ast.Or) and result: break
            return result
        self.fail('지원하지 않는 표현식입니다.')


def safe_figure(figure):
    if len(figure.get('data', [])) > MAX_TRACES or figure.get('frames'):
        raise ScriptError('차트는 최대 64개 trace까지 지원하며 애니메이션은 지원하지 않습니다.')
    allowed_types = {name.lower() for name in GO if name != 'Figure'}
    for trace in figure.get('data', []):
        if trace.get('type', 'scatter') not in allowed_types:
            raise ScriptError('외부 지도·이미지 또는 지원하지 않는 차트 유형입니다.')
    def visit(value, depth=0):
        if depth > 40: raise ScriptError('그림 구조가 너무 복잡합니다.')
        if isinstance(value, dict):
            if any(k in {'images','mapbox','map','geo','sourceattribution'} or k.endswith('src') for k in value):
                raise ScriptError('외부 이미지·지도·원격 데이터 참조는 사용할 수 없습니다.')
            return {k:visit(v,depth+1) for k,v in value.items()}
        if isinstance(value, list): return [visit(v,depth+1) for v in value]
        if isinstance(value, str):
            # Keep Plotly's harmless text formatting, never links, images or HTML attributes.
            parts = re.split(r'(</?(?:b|i|em|sup|sub|extra)>|<br\s*/?>)', value)
            return ''.join(part if re.fullmatch(r'</?(?:b|i|em|sup|sub|extra)>|<br\s*/?>',part) else html.escape(part,quote=False) for part in parts)
        return value
    return visit(figure)


def load_frame(payload):
    """Trusted transport only; scripts never receive file APIs or a file path."""
    import pandas as pd
    if payload.get('protocol') != 'column_batches_v1':
        raise ScriptError('차트 데이터 전달 형식을 확인할 수 없습니다.')
    names = [column['name'] for column in payload['columns']]
    chunks, count, text_pool = [], 0, {}
    # Fixed filename inside the per-execution temporary folder, not a user path.
    with Path('rows.jsonl').open('rb') as source:
        for line in source:
            values = json.loads(line)
            if len(values) != len(names) or len({len(v) for v in values}) != 1:
                raise ScriptError('차트 데이터 컬럼이 일치하지 않습니다.')
            # Reuse repeated short labels without changing dtype/values or
            # categorical groupby semantics. Bound the pool for unique text.
            for column in values:
                for index, value in enumerate(column):
                    if isinstance(value, str) and len(value) <= 128:
                        if value in text_pool:
                            column[index] = text_pool[value]
                        elif len(text_pool) < 4096:
                            text_pool[value] = value
            chunk = pd.DataFrame(dict(zip(names, values)))
            count += len(chunk)
            chunks.append(chunk)
            del values, chunk, line
    if count != payload['input_rows']:
        raise ScriptError('전체 데이터가 전달되지 않았습니다. 일부 데이터로 실행하지 않았습니다.')
    if not chunks:
        return pd.DataFrame(columns=names)
    return pd.concat(chunks, ignore_index=True, copy=False) if len(chunks) > 1 else chunks[0]


def peak_memory_bytes():
    if sys.platform != 'win32':
        import resource
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return value if sys.platform == 'darwin' else value * 1024
    import ctypes as c
    from ctypes import wintypes as w
    class Counters(c.Structure):
        _fields_ = [('cb', w.DWORD), ('faults', w.DWORD),
                    *[(name, c.c_size_t) for name in ('peak_working', 'working', 'peak_paged', 'paged',
                      'peak_nonpaged', 'nonpaged', 'pagefile', 'peak_pagefile')]]
    kernel, psapi = c.WinDLL('kernel32'), c.WinDLL('psapi')
    kernel.GetCurrentProcess.restype = w.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [w.HANDLE, c.POINTER(Counters), w.DWORD]
    stats = Counters()
    stats.cb = c.sizeof(stats)
    return stats.peak_working if psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), c.byref(stats), stats.cb) else 0


def render(payload):
    import plotly.io as pio
    tree = parse_script(payload['script'])
    # Apply the app theme as defaults; explicit script layout edits override it.
    theme = {'layout': {'colorway':['#079997','#386b9e','#92b9bc','#e7b25d','#ad7a9b','#8d969f'],
             'font':{'family':'Inter, Arial, sans-serif','size':11,'color':'#506778'},
             'paper_bgcolor':'#ffffff','plot_bgcolor':'#ffffff',
             'margin':{'l':52,'r':20,'t':24,'b':48},
             'xaxis':{'gridcolor':'#edf1f4','zeroline':False,'automargin':True},
             'yaxis':{'gridcolor':'#edf1f4','zeroline':False,'automargin':True}}}
    pio.templates['project_manager'] = theme
    pio.templates.default = 'project_manager'
    engine = Interpreter(load_frame(payload), payload['columns'])
    from plotly_codegen import defines_figure, simple_script
    # Older saved modifier-only scripts retain their behavior. New scripts
    # construct fig explicitly, so no hidden chart is built before executing them.
    if payload.get('base_card') and not defines_figure(payload['script']):
        frame = engine.env['df']
        numeric_color = 'color' in frame and engine.pd.api.types.is_numeric_dtype(frame['color'])
        engine.block(parse_script(simple_script(payload['base_card'], numeric_color=numeric_color)).body)
    engine.env['fig'].update_layout(template='project_manager')
    engine.block(tree.body)
    fig = engine.env.get('fig')
    if not isinstance(fig, engine.go.Figure): raise ScriptError('최종 Plotly Figure를 fig 변수에 넣으세요.')
    if not fig.layout.template: fig.update_layout(template='project_manager')
    result = safe_figure(json.loads(pio.to_json(fig, validate=True)))
    result['layout'].pop('width',None)
    result['layout'].pop('height',None)
    result['layout']['autosize'] = True
    return result


if __name__ == '__main__':
    limits()
    try:
        raw = sys.stdin.buffer.read(MAX_CONTROL_BYTES + 1)
        if len(raw) > MAX_CONTROL_BYTES: raise ScriptError('스크립트와 컬럼 정의가 너무 큽니다.')
        payload = json.loads(raw)
        result = {'figure':render(payload), 'input_rows':payload['input_rows'], 'peak_memory_bytes':peak_memory_bytes()}
        encoded = json.dumps(result,ensure_ascii=False,separators=(',',':')).encode('utf-8')
        if len(encoded) > MAX_OUTPUT_BYTES:
            raise ScriptError('차트 표시 결과가 4MB를 넘습니다. df의 데이터는 생략하지 않았습니다. 스크립트에서 집계 후 표시하세요.')
        sys.stdout.buffer.write(encoded)
    except Exception as error:
        message = str(error)[:900] if isinstance(error,(ScriptError,KeyError,ValueError,TypeError)) else '스크립트 처리에 실패했습니다. 지원 문법과 데이터 크기를 확인하세요.'
        sys.stdout.buffer.write(json.dumps({'error':message},ensure_ascii=False).encode('utf-8'))
