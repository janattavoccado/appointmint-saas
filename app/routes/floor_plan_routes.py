"""
Floor Plan Routes
=================

Add these routes to your admin.py file, or create a new blueprint.

To add as a new blueprint:
1. Create this file as app/routes/floor_plan.py
2. Import and register in app/__init__.py:
   from app.routes.floor_plan import floor_plan_bp
   app.register_blueprint(floor_plan_bp)

Or add the routes directly to admin.py.
"""

from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for
from flask_login import login_required, current_user
from pydantic import BaseModel, Field, validator
from typing import List, Optional
from datetime import datetime

# Pydantic Models for Request Validation
class TableConfigInput(BaseModel):
    """Pydantic model for table configuration input"""
    table_id: str = Field(..., min_length=1, max_length=20)
    table_name: Optional[str] = Field(None, max_length=100)
    seats: int = Field(4, ge=1, le=50)
    shape: str = Field('rectangle')
    width: int = Field(2, ge=1, le=10)
    height: int = Field(2, ge=1, le=10)
    pos_x: int = Field(0, ge=0)
    pos_y: int = Field(0, ge=0)
    table_type: str = Field('standard')
    is_active: bool = Field(True)
    min_guests: int = Field(1, ge=1)
    notes: Optional[str] = None
    
    @validator('shape')
    def validate_shape(cls, v):
        allowed = ['rectangle', 'circle', 'square', 'booth']
        if v not in allowed:
            raise ValueError(f'Shape must be one of: {allowed}')
        return v
    
    @validator('table_type')
    def validate_table_type(cls, v):
        allowed = ['standard', 'counter', 'high_top', 'outdoor', 'booth']
        if v not in allowed:
            raise ValueError(f'Table type must be one of: {allowed}')
        return v


class FloorCellInput(BaseModel):
    """Pydantic model for floor cell input"""
    pos_x: int = Field(..., ge=0)
    pos_y: int = Field(..., ge=0)
    cell_type: str = Field('floor')
    color: Optional[str] = None


class FloorPlanInput(BaseModel):
    """Pydantic model for floor plan input"""
    name: str = Field('Main Floor', max_length=100)
    grid_rows: int = Field(20, ge=5, le=100)
    grid_cols: int = Field(20, ge=5, le=100)
    cell_size: int = Field(40, ge=20, le=100)
    floor_color: str = Field('#404040')
    tables: List[TableConfigInput] = Field(default_factory=list)
    floor_cells: List[FloorCellInput] = Field(default_factory=list)


# Routes to add to admin.py
ROUTES_CODE = '''
# Add these imports at the top of admin.py
from pydantic import ValidationError

# Add these routes to admin.py

@admin_bp.route('/restaurants/<int:restaurant_id>/floor-plan')
@login_required
def floor_plan_editor(restaurant_id):
    """Display the floor plan editor for a restaurant"""
    restaurant = Restaurant.query.get_or_404(restaurant_id)
    
    # Check access
    if not current_user.is_admin:
        if current_user.tenant_id != restaurant.tenant_id:
            flash('Access denied', 'error')
            return redirect(url_for('admin.restaurants'))
    
    # Get or create floor plan
    floor_plan = FloorPlan.query.filter_by(
        restaurant_id=restaurant_id,
        is_active=True
    ).first()
    
    return render_template('admin/floor_plan_editor.html',
                         restaurant=restaurant,
                         floor_plan=floor_plan)


@admin_bp.route('/restaurants/<int:restaurant_id>/floor-plan/save', methods=['POST'])
@login_required
def save_floor_plan(restaurant_id):
    """Save floor plan configuration"""
    restaurant = Restaurant.query.get_or_404(restaurant_id)
    
    # Check access
    if not current_user.is_admin:
        if current_user.tenant_id != restaurant.tenant_id:
            return jsonify({'success': False, 'error': 'Access denied'}), 403
    
    try:
        data = request.get_json()
        
        # Validate with Pydantic
        from app.routes.floor_plan_routes import FloorPlanInput, TableConfigInput, FloorCellInput
        floor_plan_data = FloorPlanInput(**data)
        
        # Get or create floor plan
        floor_plan = FloorPlan.query.filter_by(
            restaurant_id=restaurant_id,
            is_active=True
        ).first()
        
        if not floor_plan:
            floor_plan = FloorPlan(restaurant_id=restaurant_id)
            db.session.add(floor_plan)
        
        # Update floor plan properties
        floor_plan.name = floor_plan_data.name
        floor_plan.grid_rows = floor_plan_data.grid_rows
        floor_plan.grid_cols = floor_plan_data.grid_cols
        floor_plan.cell_size = floor_plan_data.cell_size
        floor_plan.floor_color = floor_plan_data.floor_color
        floor_plan.updated_at = datetime.utcnow()
        
        db.session.flush()  # Get floor_plan.id
        
        # Clear existing tables and floor cells
        TableConfig.query.filter_by(floor_plan_id=floor_plan.id).delete()
        FloorCell.query.filter_by(floor_plan_id=floor_plan.id).delete()
        
        # Add tables
        for table_data in floor_plan_data.tables:
            table = TableConfig(
                floor_plan_id=floor_plan.id,
                table_id=table_data.table_id,
                table_name=table_data.table_name,
                seats=table_data.seats,
                shape=table_data.shape,
                width=table_data.width,
                height=table_data.height,
                pos_x=table_data.pos_x,
                pos_y=table_data.pos_y,
                table_type=table_data.table_type,
                is_active=table_data.is_active,
                min_guests=table_data.min_guests,
                notes=table_data.notes
            )
            db.session.add(table)
        
        # Add floor cells
        for cell_data in floor_plan_data.floor_cells:
            cell = FloorCell(
                floor_plan_id=floor_plan.id,
                pos_x=cell_data.pos_x,
                pos_y=cell_data.pos_y,
                cell_type=cell_data.cell_type,
                color=cell_data.color
            )
            db.session.add(cell)
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'floor_plan_id': floor_plan.id,
            'message': 'Floor plan saved successfully'
        })
        
    except ValidationError as e:
        return jsonify({
            'success': False,
            'error': 'Validation error',
            'details': e.errors()
        }), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@admin_bp.route('/restaurants/<int:restaurant_id>/floor-plan/data')
@login_required
def get_floor_plan_data(restaurant_id):
    """Get floor plan data as JSON"""
    restaurant = Restaurant.query.get_or_404(restaurant_id)
    
    # Check access
    if not current_user.is_admin:
        if current_user.tenant_id != restaurant.tenant_id:
            return jsonify({'success': False, 'error': 'Access denied'}), 403
    
    floor_plan = FloorPlan.query.filter_by(
        restaurant_id=restaurant_id,
        is_active=True
    ).first()
    
    if not floor_plan:
        return jsonify({
            'success': True,
            'floor_plan': None
        })
    
    return jsonify({
        'success': True,
        'floor_plan': floor_plan.to_dict()
    })


@admin_bp.route('/restaurants/<int:restaurant_id>/floor-plan/tables')
@login_required
def get_tables(restaurant_id):
    """Get all tables for a restaurant"""
    restaurant = Restaurant.query.get_or_404(restaurant_id)
    
    floor_plan = FloorPlan.query.filter_by(
        restaurant_id=restaurant_id,
        is_active=True
    ).first()
    
    if not floor_plan:
        return jsonify({'success': True, 'tables': []})
    
    tables = TableConfig.query.filter_by(
        floor_plan_id=floor_plan.id,
        is_active=True
    ).all()
    
    return jsonify({
        'success': True,
        'tables': [t.to_dict() for t in tables]
    })


@admin_bp.route('/restaurants/<int:restaurant_id>/floor-plan/upload-excel', methods=['POST'])
@login_required
def upload_excel_layout(restaurant_id):
    """Upload Excel file to parse table layout"""
    import openpyxl
    from io import BytesIO
    
    restaurant = Restaurant.query.get_or_404(restaurant_id)
    
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'No file uploaded'}), 400
    
    file = request.files['file']
    if not file.filename.endswith(('.xlsx', '.xls')):
        return jsonify({'success': False, 'error': 'Invalid file type'}), 400
    
    try:
        wb = openpyxl.load_workbook(BytesIO(file.read()))
        sheet = wb.active
        
        # Parse the Excel layout
        range_ref = sheet.dimensions
        if not range_ref or range_ref == 'A1:A1':
            return jsonify({'success': False, 'error': 'Empty spreadsheet'}), 400
        
        from openpyxl.utils import range_boundaries
        min_col, min_row, max_col, max_row = range_boundaries(range_ref)
        
        rows = max_row - min_row + 1
        cols = max_col - min_col + 1
        
        green_cells = []
        dark_cells = []
        
        for row in range(min_row, max_row + 1):
            for col in range(min_col, max_col + 1):
                cell = sheet.cell(row=row, column=col)
                fill = cell.fill
                
                grid_row = row - min_row
                grid_col = col - min_col
                
                if fill and fill.fgColor:
                    color_type = fill.fgColor.type
                    if color_type == 'theme':
                        theme = fill.fgColor.theme
                        if theme == 9:  # Green = table
                            green_cells.append({'row': grid_row, 'col': grid_col})
                        elif theme == 1:  # Dark = floor
                            dark_cells.append({'row': grid_row, 'col': grid_col})
        
        # Group green cells into tables
        tables = find_connected_cells(green_cells)
        
        parsed_tables = []
        for i, table_cells in enumerate(tables, 1):
            min_r = min(c['row'] for c in table_cells)
            max_r = max(c['row'] for c in table_cells)
            min_c = min(c['col'] for c in table_cells)
            max_c = max(c['col'] for c in table_cells)
            
            width = max_c - min_c + 1
            height = max_r - min_r + 1
            seats = len(table_cells)
            
            parsed_tables.append({
                'table_id': f'T{i}',
                'seats': seats,
                'width': width,
                'height': height,
                'pos_x': min_c,
                'pos_y': min_r,
                'shape': 'square' if width == height else 'rectangle'
            })
        
        return jsonify({
            'success': True,
            'grid_rows': rows,
            'grid_cols': cols,
            'tables': parsed_tables,
            'floor_cells': dark_cells
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


def find_connected_cells(cells):
    """Find groups of connected cells (tables)"""
    if not cells:
        return []
    
    cell_set = set((c['row'], c['col']) for c in cells)
    groups = []
    
    while cell_set:
        start = cell_set.pop()
        group = [{'row': start[0], 'col': start[1]}]
        queue = [start]
        
        while queue:
            current = queue.pop(0)
            row, col = current
            
            neighbors = [
                (row-1, col), (row+1, col),
                (row, col-1), (row, col+1)
            ]
            
            for n in neighbors:
                if n in cell_set:
                    cell_set.remove(n)
                    group.append({'row': n[0], 'col': n[1]})
                    queue.append(n)
        
        groups.append(group)
    
    return groups
'''

print("Routes code generated. Add to admin.py or create as separate blueprint.")
