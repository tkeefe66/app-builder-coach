"""brief kinds, day index, and brief_recommendations

Additive only. No rows are deleted: this repo has no database backups, and the
existing prose briefs stay in the history as legacy delta entries.

Revision ID: 9c1a4f2b7e30
Revises: 334147163440
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '9c1a4f2b7e30'
down_revision: Union[str, Sequence[str], None] = '334147163440'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('briefs', sa.Column('kind', sa.String(16),
                                      nullable=False, server_default='delta'))
    op.add_column('briefs', sa.Column('day', sa.String(10),
                                      nullable=False, server_default=''))
    op.add_column('briefs', sa.Column('fingerprint', sa.String(64),
                                      nullable=False, server_default=''))
    op.create_index('ix_briefs_kind', 'briefs', ['kind'])
    # Deliberately NOT unique -- see models.Brief.day.
    op.create_index('ix_briefs_day', 'briefs', ['day'])
    # Backfill day from the ISO timestamp already stored on every row.
    op.execute("UPDATE briefs SET day = substr(created_at, 1, 10)")

    op.create_table(
        'brief_recommendations',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('brief_id', sa.Integer(), sa.ForeignKey('briefs.id'),
                  nullable=False),
        sa.Column('ord', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('target', sa.String(120), nullable=False),
        sa.Column('why', sa.Text(), nullable=False, server_default=''),
        sa.Column('evidence', sa.Text(), nullable=False, server_default=''),
        sa.Column('outcome', sa.String(16), nullable=False, server_default='open'),
        sa.Column('outcome_at', sa.String(32), nullable=False, server_default=''),
    )
    op.create_index('ix_brief_recommendations_brief_id',
                    'brief_recommendations', ['brief_id'])
    op.create_index('ix_brief_recommendations_target',
                    'brief_recommendations', ['target'])
    op.create_index('ix_brief_recommendations_outcome',
                    'brief_recommendations', ['outcome'])


def downgrade() -> None:
    op.drop_table('brief_recommendations')
    op.drop_index('ix_briefs_day', table_name='briefs')
    op.drop_index('ix_briefs_kind', table_name='briefs')
    op.drop_column('briefs', 'fingerprint')
    op.drop_column('briefs', 'day')
    op.drop_column('briefs', 'kind')
