"""Additive tables: optional provider/member profiles, evidence requests, member confirmations, Member Radar and Phoenix successors."""
from alembic import op
from app.models import metadata
revision = '0002'
down_revision = '0001'

NEW_TABLES = ['provider_profiles', 'member_profiles', 'evidence_requests', 'member_confirmations', 'member_risk', 'radar_batches', 'radar_batch_members', 'provider_successors']

def upgrade():
    # create_all is checkfirst: existing tables (including all 0001 tables) are never altered or recreated.
    metadata.create_all(op.get_bind(), tables=[metadata.tables[name] for name in NEW_TABLES])

def downgrade():
    raise RuntimeError('Destructive downgrade disabled; preserve investigation records.')
