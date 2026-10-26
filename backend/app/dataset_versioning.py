"""One transaction lock for every writer of a logical dataset's versions."""
from sqlalchemy import text


def lock_dataset_version(db, dataset_id):
    db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:id, 0))"),
               {"id": dataset_id})
