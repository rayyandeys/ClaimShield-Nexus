"""Additive table for policy-governed investigator decisions (CLAIMSHIELD-SIU-V1). Existing tables are not altered."""
from alembic import op
from app.models import metadata
revision = '0004'
down_revision = '0003'

def upgrade():
    metadata.create_all(op.get_bind(), tables=[metadata.tables['policy_decisions']])

def downgrade():
    raise RuntimeError('Destructive downgrade disabled; preserve investigation records.')
