"""Parquet boundary checks; real PostgreSQL round trip lives in validation/."""
from datetime import datetime, timezone
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

path = Path(__file__).resolve().parents[2] / "scripts/import_parquet_demo.py"
spec = importlib.util.spec_from_file_location("demo_import", path)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def make_files(root, schema=None):
    schema = schema or pa.schema([("product", pa.string()), ("value", pa.decimal128(12, 4))])
    for name in module.TABLES:
        folder = root / name
        folder.mkdir()
        table = pa.Table.from_pylist([{"product": "000A", "value": Decimal("1.2345")}], schema=schema)
        pq.write_table(table, folder / "part-00000.parquet")


def test_all_parts_and_original_names_are_inspected(tmp_path):
    make_files(tmp_path)
    pq.write_table(pa.table({"product": ["000B"], "value": pa.array([None], pa.decimal128(12, 4))}),
        tmp_path / "inline/part-00001.parquet")
    tables = module.inspect_inputs(tmp_path)
    inline = next(t for t in tables if t.name == "inline")
    assert inline.rows == 2 and len(inline.files) == 2
    assert inline.schema.names == ["product", "value"]


def test_missing_table_and_changed_schema_fail_before_db_creation(tmp_path):
    make_files(tmp_path)
    pq.write_table(pa.table({"product": ["A"], "value": [1.0]}), tmp_path / "epm/part-00001.parquet")
    with pytest.raises(module.ImportProblem, match="타입이 다릅니다"):
        module.inspect_inputs(tmp_path)


def test_lot_cd_type_is_not_silently_coerced(tmp_path):
    for name in module.TABLES:
        (tmp_path / name).mkdir()
        pq.write_table(pa.table({"lot_cd": [1]}), tmp_path / name / 'part.parquet')
    with pytest.raises(module.ImportProblem, match='lot_cd'):
        module.inspect_inputs(tmp_path)


@pytest.mark.parametrize("dtype", [pa.list_(pa.int64()), pa.null(), pa.binary()])
def test_unsupported_or_lossy_types_are_explicit(dtype):
    with pytest.raises(module.ImportProblem):
        module.postgres_type(dtype)


def test_supported_precision_and_timezone():
    assert module.postgres_type(pa.decimal128(18, 7)) == "numeric(18,7)"
    assert module.postgres_type(pa.timestamp("us", tz="UTC")) == "timestamp with time zone"
    assert module.postgres_type(pa.timestamp("us")) == "timestamp without time zone"
    assert module.postgres_type(pa.uint64()) == "numeric(20,0)"


def test_spark_int96_is_supported_without_silent_timestamp_truncation(tmp_path):
    moment = datetime(2026, 10, 26, 12, 13, 14, 123456)
    path = tmp_path / 'spark-int96.parquet'
    pq.write_table(pa.table({'at': [moment]}), path, use_deprecated_int96_timestamps=True)
    part = pq.ParquetFile(path)
    assert part.schema_arrow.field('at').type == pa.timestamp('ns')
    assert module.copy_batch(next(part.iter_batches())).to_pylist() == [{'at': moment}]
    batch = pa.record_batch([pa.array([1], type=pa.timestamp('ns'))], names=['at'])
    with pytest.raises(module.ImportProblem, match='정밀도'):
        module.copy_batch(batch)


def test_nested_partition_files_cannot_silently_lose_partition_columns(tmp_path):
    make_files(tmp_path)
    nested = tmp_path / "inline/product=A"
    nested.mkdir()
    pq.write_table(pa.table({"value": [1]}), nested / "part.parquet")
    with pytest.raises(module.ImportProblem, match="分割|분할"):
        module.inspect_inputs(tmp_path)


@pytest.mark.parametrize("name", ["wafer_app", "wafer_demo", "wafer_fab_demo", "postgres", "wafer_bad; DROP DATABASE x"])
def test_existing_runtime_names_cannot_be_import_targets(tmp_path, name):
    with pytest.raises(module.ImportProblem, match="새 DB 이름"):
        module.import_snapshot(tmp_path, name)
