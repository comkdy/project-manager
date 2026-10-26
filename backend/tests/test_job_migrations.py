"""Additive upgrades retain old results and schedules and are repeatable."""
from sqlalchemy import MetaData, create_engine, select

from app.analytics.models import AnalysisRun, AnalysisSchedule, AnalysisNotification, migrate_analytics
from app.semantic.collector_models import CollectionSettings
from app.semantic.migration import migrate_semantic


def test_upgrade_old_run_and_collection_tables_twice_keeps_data():
    engine = create_engine('sqlite://')
    old = MetaData()
    runs = AnalysisRun.__table__.to_metadata(old)
    for name in ['revision','cancel_requested_at','cancelled_at','retry_of','retry_run_id','schedule_id','scheduled_for','comparison_run_id']:
        runs._columns.remove(runs.c[name])
    settings = CollectionSettings.__table__.to_metadata(old)
    settings._columns.remove(settings.c.execution_revision)
    schedules = AnalysisSchedule.__table__.to_metadata(old)
    schedules._columns.remove(schedules.c.outcome_run_id)
    notices = AnalysisNotification.__table__.to_metadata(old)
    notices._columns.remove(notices.c.comparison_run_id)
    old.create_all(engine)
    with engine.begin() as connection:
        connection.execute(runs.insert().values(id='legacy',connection_key='db',kind='statistics',status='completed',
            request={'kind':'statistics'},source_snapshot={},result={'saved':123},question_ids=['old-question'],created_by='user'))
        connection.execute(settings.insert().values(connection_key='db',enabled=True,interval_hours=48,max_questions=7,last_status='completed'))
        connection.execute(schedules.insert().values(id='old-schedule',connection_key='db',created_by='user',origin_run_id='legacy',
            name='Keep schedule',request={'kind':'statistics'},source_snapshot={},method_version='old',interval_hours=24,
            outcome=[['preserved']],enabled=True))
        connection.execute(notices.insert().values(id='old-notice',schedule_id='old-schedule',event_key='legacy:changed',
            kind='changed',message='Keep notice',run_id='legacy'))
    for _ in range(2):
        migrate_analytics(engine)
        migrate_semantic(engine)
    with engine.connect() as connection:
        run = connection.execute(select(AnalysisRun.__table__)).mappings().one()
        config = connection.execute(select(CollectionSettings.__table__)).mappings().one()
        assert run['result'] == {'saved':123} and run['question_ids'] == ['old-question']
        assert run['revision'] == 1 and run['retry_of'] is None and run['cancelled_at'] is None
        assert run['schedule_id'] is None and run['scheduled_for'] is None
        assert run['comparison_run_id'] is None
        schedule = connection.execute(select(AnalysisSchedule.__table__)).mappings().one()
        notice = connection.execute(select(AnalysisNotification.__table__)).mappings().one()
        assert schedule['enabled'] and schedule['outcome'] == [['preserved']] and schedule['outcome_run_id'] is None
        assert notice['message'] == 'Keep notice' and notice['comparison_run_id'] is None
        assert config['enabled'] and config['interval_hours'] == 48 and config['max_questions'] == 7
        assert config['execution_revision'] == 1 and config['last_status'] == 'completed'
    engine.dispose()
