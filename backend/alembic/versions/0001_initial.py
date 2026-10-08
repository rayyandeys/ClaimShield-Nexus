"""Initial relational schema and immutable audit trail."""
from alembic import op
from app.models import metadata
revision = '0001'
down_revision = None

def upgrade():
    metadata.create_all(op.get_bind())
    op.execute("""CREATE FUNCTION deny_audit_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN RAISE EXCEPTION 'Audit records are append-only'; END; $$""")
    op.execute('CREATE TRIGGER immutable_audit BEFORE UPDATE OR DELETE ON audit_events FOR EACH ROW EXECUTE FUNCTION deny_audit_mutation()')

def downgrade():
    raise RuntimeError('Destructive downgrade disabled; preserve investigation records.')

