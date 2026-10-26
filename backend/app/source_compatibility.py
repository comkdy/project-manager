"""Historical results tolerate added source columns, never changed used definitions."""


def columns_preserved(previous, current):
    if isinstance(previous, dict) or isinstance(current, dict):
        return isinstance(previous, dict) and isinstance(current, dict) and all(current.get(k) == v for k, v in previous.items())
    def name(col): return col['name'] if isinstance(col, dict) else col
    names = {name(col) for col in previous}
    return [col for col in current if name(col) in names] == previous


def sources_compatible(previous, current):
    if len(previous) != len(current):
        return False
    for old, new in zip(previous, current):
        if {k: v for k, v in old.items() if k != 'columns'} != {k: v for k, v in new.items() if k != 'columns'}:
            return False
        if not columns_preserved(old.get('columns', []), new.get('columns', [])):
            return False
    return True


def dataset_compatible(previous, current):
    return bool(current is not None and
        {k: v for k, v in previous.items() if k != 'sources'} == {k: v for k, v in current.items() if k != 'sources'} and
        sources_compatible(previous.get('sources', []), current.get('sources', [])))
