"""Action availability shared by activity and analysis detail responses."""
from datetime import timedelta, timezone

from .activity_contract import ActivityControls
from .models import utcnow


def analysis_controls(run, user):
    permitted = bool(user and user.active and user.role in {'admin', 'editor'} and
                     (user.role == 'admin' or user.id == run.created_by))
    return ActivityControls(revision=run.revision,
        can_cancel=permitted and run.status in {'queued', 'running'},
        can_retry=permitted and run.status in {'failed', 'cancelled'} and not run.retry_run_id,
        retry_run_id=run.retry_run_id)


def collection_controls(cfg, user):
    permitted = bool(user and user.active and user.role == 'admin')
    retry_after = cfg.last_run.replace(tzinfo=timezone.utc) + timedelta(minutes=1) if cfg.last_run else None
    return ActivityControls(revision=cfg.execution_revision,
        can_cancel=permitted and cfg.last_status == 'running',
        can_retry=permitted and cfg.last_status in {'error', 'cancelled'} and
                  (retry_after is None or retry_after <= utcnow()),
        retry_after=retry_after if retry_after and retry_after > utcnow() else None)
