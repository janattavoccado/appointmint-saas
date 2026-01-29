"""
Database Migration: Add Table Configuration
============================================

This migration adds the FloorPlan and TableConfig models for restaurant table management.

Run this migration with:
    heroku run python -c "
    from app import create_app, db
    from sqlalchemy import text
    app = create_app()
    with app.app_context():
        # Create floor_plan table
        db.session.execute(text('''
            CREATE TABLE IF NOT EXISTS floor_plan (
                id SERIAL PRIMARY KEY,
                restaurant_id INTEGER NOT NULL REFERENCES restaurant(id) ON DELETE CASCADE,
                name VARCHAR(100) NOT NULL DEFAULT 'Main Floor',
                grid_rows INTEGER NOT NULL DEFAULT 20,
                grid_cols INTEGER NOT NULL DEFAULT 20,
                cell_size INTEGER NOT NULL DEFAULT 40,
                floor_color VARCHAR(20) DEFAULT '#404040',
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        '''))
        
        # Create table_config table
        db.session.execute(text('''
            CREATE TABLE IF NOT EXISTS table_config (
                id SERIAL PRIMARY KEY,
                floor_plan_id INTEGER NOT NULL REFERENCES floor_plan(id) ON DELETE CASCADE,
                table_id VARCHAR(20) NOT NULL,
                table_name VARCHAR(100),
                seats INTEGER NOT NULL DEFAULT 4,
                shape VARCHAR(20) DEFAULT 'rectangle',
                width INTEGER NOT NULL DEFAULT 2,
                height INTEGER NOT NULL DEFAULT 2,
                pos_x INTEGER NOT NULL DEFAULT 0,
                pos_y INTEGER NOT NULL DEFAULT 0,
                rotation INTEGER DEFAULT 0,
                table_type VARCHAR(50) DEFAULT 'standard',
                is_active BOOLEAN DEFAULT TRUE,
                min_guests INTEGER DEFAULT 1,
                max_guests INTEGER,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(floor_plan_id, table_id)
            )
        '''))
        
        # Create floor_cell table for custom floor areas
        db.session.execute(text('''
            CREATE TABLE IF NOT EXISTS floor_cell (
                id SERIAL PRIMARY KEY,
                floor_plan_id INTEGER NOT NULL REFERENCES floor_plan(id) ON DELETE CASCADE,
                pos_x INTEGER NOT NULL,
                pos_y INTEGER NOT NULL,
                cell_type VARCHAR(20) DEFAULT 'floor',
                color VARCHAR(20),
                UNIQUE(floor_plan_id, pos_x, pos_y)
            )
        '''))
        
        db.session.commit()
        print('Migration completed successfully!')
    "
"""

# SQLAlchemy Models (add to app/models.py)

MODELS_CODE = '''
# Add these imports at the top of models.py
from sqlalchemy import UniqueConstraint

# Add these models to models.py

class FloorPlan(db.Model):
    """Floor plan configuration for a restaurant"""
    __tablename__ = 'floor_plan'
    
    id = db.Column(db.Integer, primary_key=True)
    restaurant_id = db.Column(db.Integer, db.ForeignKey('restaurant.id', ondelete='CASCADE'), nullable=False)
    name = db.Column(db.String(100), nullable=False, default='Main Floor')
    grid_rows = db.Column(db.Integer, nullable=False, default=20)
    grid_cols = db.Column(db.Integer, nullable=False, default=20)
    cell_size = db.Column(db.Integer, nullable=False, default=40)  # pixels
    floor_color = db.Column(db.String(20), default='#404040')
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    restaurant = db.relationship('Restaurant', backref=db.backref('floor_plans', lazy='dynamic'))
    tables = db.relationship('TableConfig', backref='floor_plan', lazy='dynamic', cascade='all, delete-orphan')
    floor_cells = db.relationship('FloorCell', backref='floor_plan', lazy='dynamic', cascade='all, delete-orphan')
    
    def to_dict(self):
        return {
            'id': self.id,
            'restaurant_id': self.restaurant_id,
            'name': self.name,
            'grid_rows': self.grid_rows,
            'grid_cols': self.grid_cols,
            'cell_size': self.cell_size,
            'floor_color': self.floor_color,
            'is_active': self.is_active,
            'tables': [t.to_dict() for t in self.tables],
            'floor_cells': [c.to_dict() for c in self.floor_cells]
        }


class TableConfig(db.Model):
    """Table configuration within a floor plan"""
    __tablename__ = 'table_config'
    __table_args__ = (
        UniqueConstraint('floor_plan_id', 'table_id', name='uq_floor_table'),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    floor_plan_id = db.Column(db.Integer, db.ForeignKey('floor_plan.id', ondelete='CASCADE'), nullable=False)
    table_id = db.Column(db.String(20), nullable=False)  # e.g., "T1", "T2"
    table_name = db.Column(db.String(100))  # e.g., "Window Table", "Corner Booth"
    seats = db.Column(db.Integer, nullable=False, default=4)
    shape = db.Column(db.String(20), default='rectangle')  # rectangle, circle, square
    width = db.Column(db.Integer, nullable=False, default=2)  # grid cells
    height = db.Column(db.Integer, nullable=False, default=2)  # grid cells
    pos_x = db.Column(db.Integer, nullable=False, default=0)  # grid position
    pos_y = db.Column(db.Integer, nullable=False, default=0)  # grid position
    rotation = db.Column(db.Integer, default=0)  # degrees (0, 90, 180, 270)
    table_type = db.Column(db.String(50), default='standard')  # standard, counter, high_top, outdoor, booth
    is_active = db.Column(db.Boolean, default=True)
    min_guests = db.Column(db.Integer, default=1)
    max_guests = db.Column(db.Integer)  # if null, uses seats value
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'floor_plan_id': self.floor_plan_id,
            'table_id': self.table_id,
            'table_name': self.table_name,
            'seats': self.seats,
            'shape': self.shape,
            'width': self.width,
            'height': self.height,
            'pos_x': self.pos_x,
            'pos_y': self.pos_y,
            'rotation': self.rotation,
            'table_type': self.table_type,
            'is_active': self.is_active,
            'min_guests': self.min_guests,
            'max_guests': self.max_guests or self.seats,
            'notes': self.notes
        }


class FloorCell(db.Model):
    """Individual floor cells for custom floor areas"""
    __tablename__ = 'floor_cell'
    __table_args__ = (
        UniqueConstraint('floor_plan_id', 'pos_x', 'pos_y', name='uq_floor_cell_pos'),
    )
    
    id = db.Column(db.Integer, primary_key=True)
    floor_plan_id = db.Column(db.Integer, db.ForeignKey('floor_plan.id', ondelete='CASCADE'), nullable=False)
    pos_x = db.Column(db.Integer, nullable=False)
    pos_y = db.Column(db.Integer, nullable=False)
    cell_type = db.Column(db.String(20), default='floor')  # floor, wall, entrance, kitchen, bar
    color = db.Column(db.String(20))  # custom color override
    
    def to_dict(self):
        return {
            'id': self.id,
            'pos_x': self.pos_x,
            'pos_y': self.pos_y,
            'cell_type': self.cell_type,
            'color': self.color
        }
'''

print("Migration script created. Run the SQL commands above to create the tables.")
print("\nThen add the model code to app/models.py")
