"""Additive tables for live claims monitoring: stream sessions, stream claim stage coverage, ordered processing events,
enrichment runs and runner heartbeats. Existing tables are not altered."""
from alembic import op
from app.models import metadata
revision = '0003'
down_revision = '0002'

NEW_TABLES = ['stream_sessions', 'stream_claims', 'stream_claim_stages', 'stream_events', 'stream_enrichment_runs', 'stream_runners']

def upgrade():
    metadata.create_all(op.get_bind(), tables=[metadata.tables[name] for name in NEW_TABLES])

def downgrade():
    raise RuntimeError('Destructive downgrade disabled; preserve investigation records.')
