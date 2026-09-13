"""Add business_type and services for multi-business support

Revision ID: 002
Revises: 001
Create Date: 2026-09-11 10:30:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '002'
down_revision = '001'
branch_labels = None
depends_on = None


def upgrade():
    # Add business_type column to shops table
    op.add_column('shops', sa.Column('business_type', sa.String(50), nullable=True))
    
    # Create services table
    op.create_table(
        'services',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('shop_id', sa.String(36), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('base_price', sa.Float, nullable=False),
        sa.Column('duration_minutes', sa.Integer, nullable=True),
        sa.Column('service_type', sa.String(100), nullable=True),
        sa.Column('is_active', sa.String(50), nullable=False, server_default='active'),
        sa.Column('created_at', sa.DateTime, nullable=False),
        sa.Column('updated_at', sa.DateTime, nullable=False),
        sa.ForeignKeyConstraint(['shop_id'], ['shops.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_services_shop', 'services', ['shop_id'])
    op.create_index('ix_services_is_active', 'services', ['is_active'])
    
    # Create staff table
    op.create_table(
        'staff',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('shop_id', sa.String(36), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('email', sa.String(255), nullable=True),
        sa.Column('phone', sa.String(20), nullable=True),
        sa.Column('bio', sa.Text, nullable=True),
        sa.Column('photo_url', sa.String(500), nullable=True),
        sa.Column('specialty', sa.String(255), nullable=True),
        sa.Column('qualifications', sa.Text, nullable=True),
        sa.Column('position', sa.String(100), nullable=True),
        sa.Column('is_active', sa.String(50), nullable=False, server_default='active'),
        sa.Column('total_reservations', sa.Integer, nullable=False, server_default='0'),
        sa.Column('average_rating', sa.Integer, nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime, nullable=False),
        sa.Column('updated_at', sa.DateTime, nullable=False),
        sa.ForeignKeyConstraint(['shop_id'], ['shops.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_staff_shop', 'staff', ['shop_id'])
    op.create_index('ix_staff_is_active', 'staff', ['is_active'])
    
    # Create staff_services table (mapping)
    op.create_table(
        'staff_services',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('staff_id', sa.String(36), nullable=False),
        sa.Column('service_id', sa.String(36), nullable=False),
        sa.Column('created_at', sa.DateTime, nullable=False),
        sa.ForeignKeyConstraint(['service_id'], ['services.id'], ),
        sa.ForeignKeyConstraint(['staff_id'], ['staff.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_staff_services_staff_service', 'staff_services', ['staff_id', 'service_id'], unique=True)
    
    # Add columns to reservations table
    op.add_column('reservations', sa.Column('staff_id', sa.String(36), nullable=True))
    op.add_column('reservations', sa.Column('reservation_details', sa.JSON, nullable=True))
    op.add_column('reservations', sa.Column('total_price', sa.Integer, nullable=True))
    op.add_column('reservations', sa.Column('payment_method', sa.String(50), nullable=True))
    op.add_column('reservations', sa.Column('payment_status', sa.String(50), nullable=True))
    
    # Create foreign key for staff_id in reservations
    op.create_foreign_key('fk_reservations_staff_id', 'reservations', 'staff', ['staff_id'], ['id'])
    op.create_index('ix_reservations_staff', 'reservations', ['staff_id'])
    
    # Create index for business_type
    op.create_index('ix_shops_business_type', 'shops', ['business_type'])


def downgrade():
    # Remove indexes
    op.drop_index('ix_shops_business_type', table_name='shops')
    op.drop_index('ix_reservations_staff', table_name='reservations')
    op.drop_index('ix_staff_services_staff_service', table_name='staff_services')
    op.drop_index('ix_staff_is_active', table_name='staff')
    op.drop_index('ix_staff_shop', table_name='staff')
    op.drop_index('ix_services_is_active', table_name='services')
    op.drop_index('ix_services_shop', table_name='services')
    
    # Remove foreign key and columns from reservations
    op.drop_constraint('fk_reservations_staff_id', 'reservations', type_='foreignkey')
    op.drop_column('reservations', 'payment_status')
    op.drop_column('reservations', 'payment_method')
    op.drop_column('reservations', 'total_price')
    op.drop_column('reservations', 'reservation_details')
    op.drop_column('reservations', 'staff_id')
    
    # Drop tables
    op.drop_table('staff_services')
    op.drop_table('staff')
    op.drop_table('services')
    
    # Remove business_type column
    op.drop_column('shops', 'business_type')
