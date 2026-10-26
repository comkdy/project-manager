"""Closed Python chart language. Never compile/exec/eval user Python.

The worker interprets these AST nodes and only invokes explicit chart/data
capabilities. Chart/data capabilities are provided by the host; imports are forbidden.
"""
import ast

MAX_CONTROL_BYTES = 1024 * 1024
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_TRACES = 64
TIMEOUT = 60
LOAD_TIMEOUT = 120
QUEUE_TIMEOUT = 15
BATCH_ROWS = 2048
MEMORY_BYTES = 1024 * 1024 * 1024

GO = frozenset('Figure Scatter Scattergl Bar Box Violin Histogram Histogram2d Histogram2dContour Contour Heatmap Pie Sunburst Treemap Icicle Funnel Funnelarea Waterfall Indicator Sankey Table Scatterpolar Barpolar Scatterternary Scatter3d Surface Mesh3d Parcoords Parcats Splom'.split())
PX = frozenset('scatter line area bar box violin strip histogram pie sunburst treemap icicle funnel funnel_area scatter_matrix parallel_coordinates parallel_categories scatter_polar line_polar bar_polar scatter_ternary line_ternary scatter_3d line_3d density_contour density_heatmap timeline'.split())
FIGURE_METHODS = frozenset('add_trace add_traces update_layout update_traces update_xaxes update_yaxes update_annotations update_shapes update_scenes update_polars update_ternaries add_annotation add_shape add_hline add_vline add_hrect add_vrect'.split())
DATA_METHODS = frozenset('copy head tail dropna fillna sort_values sort_index reset_index rename drop duplicated drop_duplicates groupby agg aggregate mean median min max sum count size std var quantile nunique unique value_counts round abs clip isin between isna notna tolist to_list astype pivot pivot_table melt nlargest nsmallest cumsum cummax cummin diff pct_change'.split())
AGGREGATES = frozenset('mean median min max sum count size std var nunique first last'.split())
ALLOWED = (ast.Module, ast.Assign, ast.Expr, ast.For, ast.If,
           ast.Name, ast.Load, ast.Store, ast.Constant, ast.List, ast.Tuple,
           ast.Dict, ast.Subscript, ast.Slice, ast.Attribute, ast.Call, ast.keyword,
           ast.BinOp, ast.UnaryOp, ast.Compare, ast.IfExp, ast.BoolOp,
           ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.BitAnd, ast.BitOr,
           ast.USub, ast.UAdd, ast.Invert, ast.Not, ast.And, ast.Or,
           ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn)


class ScriptError(ValueError):
    pass


def parse_script(script):
    if not isinstance(script, str) or not script.strip() or len(script) > 20000:
        raise ScriptError('스크립트는 1~20,000자여야 합니다.')
    try:
        tree = ast.parse(script)
    except (SyntaxError, ValueError, RecursionError) as error:
        raise ScriptError(f'Python 문법을 확인하세요. {getattr(error, "lineno", "")}행') from None
    nodes = list(ast.walk(tree))
    if len(nodes) > 3000:
        raise ScriptError('스크립트가 너무 복잡합니다.')
    for node in nodes:
        line = getattr(node, 'lineno', 1)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise ScriptError(f'{line}행: import는 사용할 수 없습니다. 제공된 df, go, px, pd, make_subplots를 직접 사용하세요.')
        if not isinstance(node, ALLOWED):
            raise ScriptError(f'{line}행: 차트 편집에서 지원하지 않는 문법입니다: {type(node).__name__}')
        if isinstance(node, ast.Name) and node.id.startswith('_') or isinstance(node, ast.Attribute) and node.attr.startswith('_'):
            raise ScriptError(f'{line}행: 내부 객체에는 접근할 수 없습니다.')
        if isinstance(node, ast.Constant) and (isinstance(node.value, (bytes, complex)) or
                isinstance(node.value, str) and len(node.value) > 4000 or
                type(node.value) is int and abs(node.value) > 10**12):
            raise ScriptError(f'{line}행: 상수 값이 너무 크거나 지원하지 않는 형식입니다.')
        if isinstance(node, ast.keyword) and node.arg is None:
            raise ScriptError(f'{line}행: 명시적인 키워드 인자를 사용하세요.')
        if isinstance(node, ast.Dict) and None in node.keys:
            raise ScriptError(f'{line}행: 사전 확장은 지원하지 않습니다.')
    return tree
