"""
Migration: Add table status fields to table_config
Run this on Heroku with: heroku run python migrations/add_table_status.py
"""

import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.models import db

def migrate():
    """Add table status columns to table_config table"""
    app = create_app('production' if os.environ.get('DATABASE_URL') else 'development')
    
    with app.app_context():
        # Check if we're using PostgreSQL or SQLite
        is_postgres = 'postgresql' in str(db.engine.url)
        
        # Get connection
        connection = db.engine.connect()
        
        # List of columns to add
        columns_to_add = [
            ("current_status", "VARCHAR(20) DEFAULT 'free'"),
            ("current_reservation_id", "INTEGER REFERENCES reservations(id)"),
            ("current_guest_name", "VARCHAR(100)"),
            ("current_guest_count", "INTEGER"),
            ("status_updated_at", "TIMESTAMP"),
            ("status_notes", "TEXT"),
        ]
        
        for col_name, col_type in columns_to_add:
            try:
                # Check if column exists
                if is_postgres:
                    result = connection.execute(db.text(f"""
                        SELECT column_name FROM information_schema.columns 
                        WHERE table_name='table_config' AND column_name='{col_name}'
                    """))
                else:
                    result = connection.execute(db.text(f"PRAGMA table_info(table_config)"))
                    columns = [row[1] for row in result.fetchall()]
                    if col_name in columns:
                        print(f"Column {col_name} already exists, skipping...")
                        continue
                
                if is_postgres and result.fetchone():
                    print(f"Column {col_name} already exists, skipping...")
                    continue
                
                # Add the column
                sql = f"ALTER TABLE table_config ADD COLUMN {col_name} {col_type}"
                connection.execute(db.text(sql))
                connection.commit()
                print(f"Added column: {col_name}")
                
            except Exception as e:
                print(f"Error adding column {col_name}: {e}")
                continue
        
        connection.close()
        print("\nMigration completed!")

if __name__ == '__main__':
    migrate()
