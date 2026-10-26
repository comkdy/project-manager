"""Readable, data-driven Python for simple cards; no captured measurement rows."""
import ast
import math
from pprint import pformat

PALETTES = {
    'teal': ['#079997', '#386b9e', '#92b9bc', '#e7b25d', '#ad7a9b', '#8d969f'],
    'blues': ['#174b70', '#247a9d', '#40a2bd', '#80cad8'],
    'warm': ['#ba6b45', '#e8a05c', '#efcc88', '#8c6584'],
    'viridis': ['#440154', '#414487', '#2a788e', '#22a884', '#7ad151', '#fde725'],
}
LEGACY_SCRIPT = '# df: 간편 편집기의 조회 결과 (x, y, color 등)\n# fig: 현재 카드의 Plotly 그림\n# 예: fig.update_traces(marker_size=7)\nfig.update_layout(hovermode="closest")'


def defines_figure(script):
    try:
        return any(isinstance(n, ast.Name) and n.id == 'fig' and isinstance(n.ctx, ast.Store)
                   for n in ast.walk(ast.parse(script)))
    except (SyntaxError, ValueError, RecursionError):
        return True  # Preserve syntax errors for the editor instead of replacing them.


def value_label(card):
    if card.get('value_expression') is not None:
        return card.get('value_expression_name') or '직접 계산식'
    if card.get('aggregate') == 'yield':
        return '수율 (%)'
    if card['kind'] == 'histogram' or card.get('aggregate') == 'count' or (card['kind'] == 'kpi' and not card.get('y')):
        return '개수'
    return (card.get('options') or {}).get('y_label') or card.get('y') or ''


def input_columns(card):
    """Describe the SQL projection delivered to Python; no second dataset."""
    aggregate = card.get('aggregate', 'none')
    # Axis display labels are not dataset column identifiers.
    value = value_label({**card, 'options': {}})
    if aggregate in {'avg', 'sum', 'min', 'max'}:
        value = f'{aggregate.upper()}({value})'
    if card['kind'] == 'kpi':
        return [{'name': 'value', 'description': value}]
    result = [{'name': 'x', 'description': card.get('x') or 'X'},
              {'name': 'y', 'description': card.get('y') if card['kind'] == 'wafer' else value}]
    if card['kind'] == 'wafer':
        measure = card.get('value_expression_name') or card.get('color') or value
        if aggregate in {'avg', 'sum', 'min', 'max'} and card.get('color'):
            measure = f'{aggregate.upper()}({measure})'
        result.append({'name': 'value', 'description': measure})
    elif card.get('color'):
        result.append({'name': 'color', 'description': card['color']})
    if card.get('trellis'):
        result.append({'name': 'trellis', 'description': card['trellis']})
    return result


def simple_script(card, *, numeric_color=False):
    """Plot source for legacy SQL projections, using the supplied df directly."""
    kind, options = card['kind'], card.get('options') or {}
    numeric_color = numeric_color and kind == 'scatter'
    palette_name = options.get('palette', 'teal')
    palette = PALETTES.get(palette_name, PALETTES['teal'])
    scale_colors = {'teal': ['#edf7f5', '#087f80'],
                    'blues': ['#eff7fc', *reversed(PALETTES['blues'])],
                    'warm': ['#fff5df', '#e8a05c', '#ba6b45', '#8c6584']}.get(palette_name, palette)
    scale = [[i / (len(scale_colors)-1), c] for i, c in enumerate(scale_colors)]
    grouped = bool(card.get('color')) and not numeric_color and kind != 'wafer'
    trellis = bool(card.get('trellis'))
    lines = ['# df: 선택한 데이터셋에 카드의 필터·집계를 적용한 조회 결과입니다.',
              '# 원본 측정행을 코드에 복사하지 않으며, 새로 조회할 때 df도 갱신됩니다.']
    for column in input_columns(card):
        # repr escapes newlines so a dataset label cannot inject a code line.
        lines.append(f"# {column['name']} ← {column['description']!r}")
    lines += ['', '# 차트 구성']
    if kind == 'kpi':
        number = {'valueformat': ',.2f'}
        if card.get('aggregate') == 'yield' or options.get('value_format') == 'percent':
            number['suffix'] = '%'
        lines += ['value = None if df.empty else df["value"].tolist()[0]',
                  f'fig = go.Figure(go.Indicator(mode="number", value=value, number={number!r}))']
    else:
        if trellis:
            lines += ['panels = df["trellis"].drop_duplicates().sort_values(na_position="last")',
                      'panel_values = panels.tolist()', 'panel_missing = panels.isna().tolist()',
                      'panel_titles = panels.fillna("(빈 값)").astype("str").tolist()',
                      'if len(panel_values) == 0:', '    panel_values = [None]',
                      '    panel_missing = [True]', '    panel_titles = [""]',
                      f'panel_columns = {options.get("trellis_columns") or "2 if len(panel_values) > 1 else 1"}',
                      'panel_rows = int((len(panel_values) + panel_columns - 1) / panel_columns)',
                      'fig = make_subplots(rows=panel_rows, cols=panel_columns,',
                      '                    subplot_titles=panel_titles, shared_xaxes="all", shared_yaxes="all")']
        else:
            lines += ['fig = go.Figure()', 'panel_values = [None]', 'panel_missing = [True]']
        if grouped:
            lines += ['df["color"] = df["color"].fillna("").astype("str")',
                      'groups = df["color"].drop_duplicates().tolist()',
                      'primary_groups = groups[:39]',
                      'if len(groups) > 39:', '    groups = primary_groups + ["기타 범주"]']
        else:
            lines += ['groups = [""]']
        lines += [f'palette = {palette!r}', 'seen_groups = []', '', 'for panel_index in range(len(panel_values)):']
        if trellis:
            lines += ['    if panel_missing[panel_index]:', '        panel_data = df[df["trellis"].isna()]',
                      '    else:', '        panel_data = df[df["trellis"] == panel_values[panel_index]]']
        else:
            lines += ['    panel_data = df']
        lines += ['    for group_index in range(len(groups)):', '        group = groups[group_index]']
        if grouped:
            lines += ['        if group_index == 39:', '            part = panel_data[~panel_data["color"].isin(primary_groups)]',
                      '        else:', '            part = panel_data[panel_data["color"] == group]']
        else:
            lines += ['        part = panel_data']
        lines += ['        if not part.empty:']
        name = 'group' if grouped else repr(value_label(card) or card.get('title', ''))
        common = f'name={name}, legendgroup=str(group_index), showlegend={"group_index not in seen_groups and len(groups) > 1" if grouped else "False"}'
        marker = 'dict(color=palette[group_index % len(palette)], size=6, opacity=0.7, line=dict(width=0))'
        if numeric_color or kind == 'wafer':
            color_column = 'value' if kind == 'wafer' else 'color'
            lines += [f'            marker = dict(color=part[{color_column!r}].tolist(), colorscale={scale!r},',
                      f'                          cmin=df[{color_column!r}].min(), cmax=df[{color_column!r}].max(),',
                      f'                          showscale=panel_index == 0, colorbar=dict(thickness=9, len=0.7, title=dict(text={card.get("value_expression_name") or card.get("color") or "값"!r})),',
                      '                          symbol="square", size=10)' if kind == 'wafer' else '                          size=6, opacity=0.7, line=dict(width=0))']
            marker = 'marker'
        if kind in {'bar', 'histogram'}:
            trace = f'go.Bar(x=part["x"].tolist(), y=part["y"].tolist(), marker_color=palette[group_index % len(palette)], {common})'
        elif kind == 'box':
            trace = f'go.Box(x=part["x"].tolist(), y=part["y"].tolist(), boxpoints=False, marker_color=palette[group_index % len(palette)], {common})'
        else:
            if kind == 'line':
                marker = 'dict(color=palette[group_index % len(palette)], size=5, line=dict(width=0))'
            trace = f'go.Scatter(x=part["x"].tolist(), y=part["y"].tolist(), mode={"lines+markers" if kind == "line" else "markers"!r}, marker={marker}, line=dict(width=2.5), {common})'
        lines.append('            trace = ' + trace)
        lines.append('            fig.add_trace(trace, row=int(panel_index / panel_columns) + 1, col=panel_index % panel_columns + 1)' if trellis else '            fig.add_trace(trace)')
        lines.append('            seen_groups = seen_groups + [group_index]')
        if kind == 'wafer':
            lines += ['    fig.update_yaxes(scaleanchor="x" if panel_index == 0 else "x" + str(panel_index + 1), scaleratio=1' +
                      (', row=int(panel_index / panel_columns) + 1, col=panel_index % panel_columns + 1)' if trellis else ')')]
        for axis in ('x', 'y'):
            label = (options.get(axis+'_label') or card.get(axis) or '') if axis == 'x' or kind == 'wafer' else value_label(card)
            kw = {'title_text': label, 'tickfont_size': options.get(axis+'_font_size', 10),
                  'type': 'log' if options.get(axis+'_scale') == 'log' else '-', 'automargin': True,
                  'gridcolor': '#edf1f4', 'zeroline': False, 'linecolor': '#dfe7ec'}
            low, high = options.get(axis+'_min'), options.get(axis+'_max')
            if low is not None or high is not None:
                kw['range'] = [math.log10(v) if v is not None and kw['type'] == 'log' else v for v in (low, high)]
            lines += ['', f'fig.update_{axis}axes(' + ', '.join(f'{k}={v!r}' for k, v in kw.items()) + ')']
    layout = {'template': 'project_manager', 'paper_bgcolor': 'white', 'plot_bgcolor': 'white',
              'font': {'family': 'Segoe UI, Malgun Gothic, sans-serif', 'color': '#596e7d', 'size': 11},
              'margin': {'t': 28 if trellis else 6, 'r': 38 if kind == 'wafer' else 8, 'b': 38, 'l': 46},
              'colorway': palette, 'barmode': 'group', 'bargap': .4, 'hovermode': 'closest',
              'legend': {'orientation': 'v', 'x': 1.02, 'xanchor': 'left', 'y': 1, 'yanchor': 'top', 'font': {'size': 10}}}
    lines += ['', 'fig.update_layout(' + pformat(layout, sort_dicts=False, width=100) + ')']
    source = '\n'.join(lines)
    if not grouped and not trellis and kind != 'kpi':
        # No panel/group loop is needed for a single series. Keep the displayed
        # code focused on df and the actual trace instead of identity aliases.
        start = lines.index('fig = go.Figure()')
        axes = next(i for i in range(start, len(lines)) if lines[i].startswith('fig.update_xaxes('))
        trace = [line[8:] for line in lines[start:axes]
                 if line.startswith('            ') and not line.strip().startswith('seen_groups =')]
        direct = [*lines[:start+1], f'palette = {palette!r}', 'if not df.empty:', *trace]
        if kind == 'wafer':
            direct.append('fig.update_yaxes(scaleanchor="x", scaleratio=1)')
        source = '\n'.join([*direct, '', *lines[axes:]])
        replacements = [(node, 'df' if node.id == 'part' else '0')
                        for node in ast.walk(ast.parse(source))
                        if isinstance(node, ast.Name) and node.id in {'part', 'group_index', 'panel_index'}]
        source = _replace_source_nodes(source, replacements)
    return source


def expanded_script(card, *, numeric_color=False):
    base = simple_script(card, numeric_color=numeric_color)
    script = card.get('python_script') or ''
    if card.get('editor_mode') != 'python' or script == LEGACY_SCRIPT:
        return base, base
    if not defines_figure(script):
        return base + '\n\n# 이전에 저장한 사용자 수정\n' + script, base
    return script, base


def _column_names(columns):
    return [column if isinstance(column, str) else column['name'] for column in columns]


class _DatasetPreparation:
    """Emit ordinary pandas operations directly on this card's provided df."""

    def __init__(self, card, columns, *, numeric_color=None):
        self.card = card
        self.names = set(_column_names(columns))
        self.numeric_color = _numeric_color(card, columns, numeric_color)
        self.lines = ['# df: 선택한 데이터셋의 원래 컬럼과 조회 결과입니다.',
                      '# 카드 필터·공통 기간은 조회 범위에만 적용됩니다.',
                      '# 집계·계산·정렬은 아래 코드에서 변경할 수 있습니다.']
        self.mapping = {}
        self.term_index = 0
        self.aggregate_columns = {}
        self.grouped = False
        self.scalar = False

    def new_column(self, desired):
        candidate, suffix = desired, 2
        while candidate in self.names:
            candidate = f'{desired}_{suffix}'
            suffix += 1
        self.names.add(candidate)
        return candidate

    def numeric(self, name, frame='df'):
        self.lines.append(f'{frame}[{name!r}] = pd.to_numeric({frame}[{name!r}], errors="coerce")')

    def grouping(self, keys):
        keys = list(dict.fromkeys(keys))
        self.grouped, self.scalar = bool(keys), not keys
        count = self.new_column('row_count')
        if keys:
            self.lines += [f'grouped_data = df.groupby({keys!r}, dropna=False, sort=False)',
                           f'df = grouped_data.size().reset_index(name={count!r})']
        else:
            self.lines.append('source_data = df')
            self.lines.append(f'df = pd.DataFrame({{{count!r}: [len(source_data)]}})')
        return count

    def column_aggregate(self, name, aggregate):
        key = name, aggregate
        if key in self.aggregate_columns:
            return f'df[{self.aggregate_columns[key]!r}]'
        output = self.new_column(f'{aggregate}_{name}')
        function = 'mean' if aggregate == 'avg' else aggregate
        arguments = 'min_count=1' if function == 'sum' else ''
        source = 'grouped_data' if self.grouped else 'source_data'
        value = f'{source}[{name!r}].{function}({arguments})'
        value = f'{value}.tolist()' if self.grouped else f'[{value}]'
        self.lines.append(f'df[{output!r}] = {value}')
        self.aggregate_columns[key] = output
        return f'df[{output!r}]'

    def divide(self, left, right):
        self.term_index += 1
        name = f'denominator_{self.term_index}'
        self.lines.append(f'{name} = pd.Series({right}, index=df.index)')
        return f'({left} / {name}.where({name} != 0))'

    def expression(self, aggregate):
        tokens = self.card['value_expression']
        position = 0

        def atom():
            nonlocal position
            token = tokens[position]
            position += 1
            if token['kind'] == 'column':
                return (self.column_aggregate(token['name'], aggregate) if aggregate != 'none'
                        else f'df[{token["name"]!r}]')
            if token['kind'] == 'number':
                return repr(float(token['value']))
            if token['value'] in {'+', '-'}:
                return f'({token["value"]}{atom()})'
            if token['value'] == '(':
                result = expression(0)
                position += 1  # Validated closing parenthesis.
                return result
            raise ValueError('Invalid validated card expression')

        def expression(precedence):
            nonlocal position
            left = atom()
            levels = {'+': 1, '-': 1, '*': 2, '/': 2}
            while position < len(tokens):
                operator = tokens[position].get('value')
                level = levels.get(operator, 0)
                if level == 0 or level < precedence:
                    break
                position += 1
                right = expression(level + 1)
                left = self.divide(left, right) if operator == '/' else f'({left} {operator} {right})'
            return left

        return expression(0)

    def result_value(self, aggregate, count, *, wafer=False):
        card = self.card
        if card.get('value_expression'):
            output = self.new_column(card.get('value_expression_name') or 'calculated_value')
            self.lines.append(f'df[{output!r}] = {self.expression(aggregate)}')
            return output
        if aggregate == 'yield':
            numerator = self.column_aggregate('passed_dies', 'sum')
            denominator = self.column_aggregate('tested_dies', 'sum')
            output = self.new_column('yield_percent')
            self.lines.append(f'df[{output!r}] = 100.0 * {self.divide(numerator, denominator)}')
            return output
        if aggregate in {'count', 'none'}:
            return count
        column = card.get('color') if wafer else card.get('y')
        self.column_aggregate(column, aggregate)
        return self.aggregate_columns[column, aggregate]

    def build(self):
        card, lines = self.card, self.lines
        kind, aggregate = card['kind'], card.get('aggregate', 'none')
        x, y, color, trellis = (card.get(key) for key in ('x', 'y', 'color', 'trellis'))
        expression = card.get('value_expression')
        numeric = {token['name'] for token in expression or [] if token['kind'] == 'column'}
        if aggregate == 'yield':
            numeric.update(('passed_dies', 'tested_dies'))
        if kind == 'histogram':
            numeric.add(x)
        elif kind == 'wafer' and color:
            numeric.add(color)
        elif not expression and y and (kind in {'scatter', 'box', 'kpi'} or aggregate in {'avg', 'sum'}):
            numeric.add(y)
        if kind == 'scatter' and color and self.numeric_color:
            numeric.add(color)
        for column in sorted(numeric):
            self.numeric(column)
        if trellis:
            lines.append(f'require(df[{trellis!r}].nunique(dropna=False) <= 12, "Trellis는 최대 12개 패널까지 표시합니다. 필터를 좁혀주세요.")')
        if kind == 'kpi':
            count = self.grouping([])
            self.mapping['value'] = self.result_value(aggregate, count)
        elif kind == 'histogram':
            bins = (card.get('options') or {}).get('bins', 30)
            bucket, center, count = (self.new_column(name) for name in ('bin_number', 'bin_center', 'row_count'))
            lines += [f'df = df[df[{x!r}].notna()]',
                      f'bin_min = df[{x!r}].min()', f'bin_max = df[{x!r}].max()',
                      f'bin_count = {bins}', 'bin_width = 0.0',
                      'if not df.empty and bin_max != bin_min:',
                      '    bin_width = (bin_max - bin_min) / bin_count',
                      f'    df[{bucket!r}] = ((df[{x!r}] - bin_min) / bin_width).astype("int").clip(upper=bin_count - 1) + 1',
                      'else:', f'    df[{bucket!r}] = 1']
            keys = [bucket] + ([trellis] if trellis else [])
            lines += [f'df = df.groupby({keys!r}, dropna=False, sort=False).size().reset_index(name={count!r})',
                      f'df[{center!r}] = bin_min + (df[{bucket!r}] - 0.5) * bin_width',
                      f'df = df.sort_values({keys!r}, na_position="last")']
            self.mapping.update(x=center, y=count)
            color = None  # Existing histogram semantics use global bins, optionally faceted.
        elif kind == 'wafer':
            if trellis:
                lines += [f'wafer_counts = df.groupby({[trellis]!r}, dropna=False)["wafer_id"].nunique(dropna=False)',
                          'require(wafer_counts.empty or wafer_counts.max() <= 1, "각 패널에는 하나의 wafer만 표시할 수 있습니다. 필터를 좁혀주세요.")']
            else:
                lines.append('require(df["wafer_id"].nunique(dropna=False) <= 1, "Wafer map은 하나의 wafer를 선택하세요.")')
            automatic_yield = aggregate == 'none' and not color and not expression and {'passed', 'status'}.issubset(self.names)
            if automatic_yield:
                tested, passed = self.new_column('tested_row'), self.new_column('passed_row')
                lines += [f'df[{tested!r}] = (df["status"] == "tested").astype("int")',
                          f'df[{passed!r}] = ((df["status"] == "tested") & df["passed"].fillna(False).astype("bool")).astype("int")']
            count = self.grouping([x, y] + ([trellis] if trellis else []))
            if automatic_yield:
                numerator = self.column_aggregate(passed, 'sum')
                denominator = self.column_aggregate(tested, 'sum')
                value = self.new_column('tested_yield_percent')
                lines.append(f'df[{value!r}] = 100.0 * {self.divide(numerator, denominator)}')
            else:
                effective = 'avg' if aggregate == 'none' and (color or expression) else aggregate
                value = self.result_value(effective, count, wafer=True)
            self.mapping.update(x=x, y=y, value=value)
            lines.append(f'df = df.sort_values({list(dict.fromkeys([x, y] + ([trellis] if trellis else [])))!r}, na_position="last")')
        elif kind in {'bar', 'line'} and aggregate != 'none':
            count = self.grouping([x] + ([color] if color else []) + ([trellis] if trellis else []))
            self.mapping.update(x=x, y=self.result_value(aggregate, count))
        else:
            if expression:
                y = self.new_column(card.get('value_expression_name') or 'calculated_value')
                lines.append(f'df[{y!r}] = {self.expression("none")}')
            self.mapping.update(x=x, y=y)
        if kind in {'bar', 'line'}:
            ascending = (card.get('options') or {}).get('sort', 'asc') == 'asc'
            lines.append(f'df = df.sort_values({x!r}, ascending={ascending!r}, na_position="last", kind="stable")')
        if color and kind != 'wafer':
            self.mapping['color'] = color
        if trellis:
            self.mapping['trellis'] = trellis
        return lines, self.mapping


def dataset_preparation(card, columns, *, numeric_color=None):
    """Return visible preparation source and old-role → actual-column mapping."""
    lines, mapping = _DatasetPreparation(card, columns, numeric_color=numeric_color).build()
    return '\n'.join(lines), mapping


def _replace_source_nodes(source, replacements):
    """Apply single-line AST replacements without reformatting readable source."""
    lines = source.splitlines(keepends=True)
    edits = []
    for node, value in replacements:
        original = lines[node.lineno - 1]
        start = len(original.encode('utf-8')[:node.col_offset].decode('utf-8'))
        end = len(original.encode('utf-8')[:node.end_col_offset].decode('utf-8'))
        edits.append((node.lineno - 1, start, end, value))
    for line, start, end, value in sorted(edits, reverse=True):
        lines[line] = lines[line][:start] + value + lines[line][end:]
    return ''.join(lines)


def _numeric_color(card, columns, selected):
    if selected is not None:
        return selected
    numeric_types = {'smallint', 'integer', 'bigint', 'numeric', 'real', 'double precision', 'decimal'}
    return card['kind'] == 'scatter' and any(
        not isinstance(column, str) and column['name'] == card.get('color')
        and column.get('type', '').lower() in numeric_types for column in columns)


def dataset_script(card, columns, *, numeric_color=None):
    """Complete plot source: original dataset df, explicit pandas, explicit traces."""
    numeric_color = _numeric_color(card, columns, numeric_color)
    preparation = _DatasetPreparation(card, columns, numeric_color=numeric_color)
    lines, mapping = preparation.build()
    render_card = dict(card)
    if card['kind'] == 'histogram':
        render_card['color'] = None
    grouped_color = bool(render_card.get('color')) and card['kind'] != 'wafer' and not (numeric_color and card['kind'] == 'scatter')
    if grouped_color:
        # Distinct display labels must not mutate a shared X or trellis column.
        category = preparation.new_column('color_label')
        lines.append(f'df[{category!r}] = df[{mapping["color"]!r}]')
        mapping['color'] = category
    legacy = simple_script(render_card, numeric_color=numeric_color)
    plotting = legacy.split('\n# 차트 구성\n', 1)[1]
    replacements = []
    for node in ast.walk(ast.parse(plotting)):
        if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
                and node.value.id in {'df', 'part', 'panel_data'}
                and isinstance(node.slice, ast.Constant) and node.slice.value in mapping):
            replacements.append((node.slice, repr(mapping[node.slice.value])))
    plotting = _replace_source_nodes(plotting, replacements)
    return '\n'.join([*lines, '', '# 차트 구성', plotting])


def _replace_df_references(script, replacement='chart_data'):
    """Change Name nodes only, preserving user comments, strings and formatting."""
    tree = ast.parse(script)
    replacements = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == 'df':
            replacements.append((node, replacement))
    return _replace_source_nodes(script, replacements)


def expanded_dataset_script(card, columns, *, numeric_color=None):
    """Migrate old chart-input scripts without silently discarding user edits."""
    numeric_color = _numeric_color(card, columns, numeric_color)
    baseline = dataset_script(card, columns, numeric_color=numeric_color)
    original = card.get('python_script') or ''
    if card.get('editor_mode') != 'python':
        return baseline, baseline
    if card.get('script_input') != 'chart':
        return original, baseline
    old_baseline = simple_script(card, numeric_color=numeric_color)
    if original in {LEGACY_SCRIPT, old_baseline}:
        return baseline, baseline
    original_tree = ast.parse(original)
    names = {node.id for node in ast.walk(original_tree) if isinstance(node, ast.Name)}
    if not defines_figure(original) and names <= {'fig'}:
        return baseline + '\n\n# 이전에 저장한 사용자 수정\n' + original, baseline
    script, _ = expanded_script(card, numeric_color=numeric_color)
    names.update(node.id for node in ast.walk(ast.parse(script)) if isinstance(node, ast.Name))
    compatible, suffix = 'chart_data', 2
    while compatible in names:
        compatible = f'chart_data_{suffix}'
        suffix += 1
    preparation, mapping = dataset_preparation(card, columns, numeric_color=numeric_color)
    projection = ['# 기존 스크립트와의 호환을 위한 명시적 결과 컬럼입니다.',
                  f'{compatible} = pd.DataFrame(index=df.index)']
    for old_name, actual in mapping.items():
        projection.append(f'{compatible}[{old_name!r}] = df[{actual!r}]')
    migrated = '\n'.join([preparation, '', *projection,
                          '', '# 이전에 저장한 사용자 스크립트', _replace_df_references(script, compatible)])
    return migrated, baseline
