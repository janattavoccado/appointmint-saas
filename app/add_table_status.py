"""
Migration: Add real-time table status fields to table_config table.

Run this migration after deploying the updated models.py:
    heroku run python migrations/add_table_status.py --app avoccado-appointmint
"""
import os
import sys

# Add parent directory to path so we can import app
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.models import db

def migrate():
    app = create_app()
    with app.app_context():
        # Check which columns need to be added
        columns_to_add = [
            ("current_status", "VARCHAR(20) DEFAULT 'free'"),
            ("current_guest_name", "VARCHAR(100)"),
            ("current_guest_count", "INTEGER"),
            ("status_updated_at", "TIMESTAMP"),
            ("current_reservation_id", "INTEGER"),
            ("status_notes", "TEXT"),
        ]
        
        for col_name, col_type in columns_to_add:
            try:
                db.session.execute(db.text(
                    f"ALTER TABLE table_config ADD COLUMN {col_name} {col_type}"
                ))
                db.session.commit()
                print(f"  + Added column: {col_name}")
            except Exception as e:
                db.session.rollback()
                if 'duplicate column' in str(e).lower() or 'already exists' in str(e).lower():
                    print(f"  ~ Column already exists: {col_name}")
                else:
                    print(f"  ! Error adding {col_name}: {e}")
        
        # Set default status for existing tables that have NULL status
        try:
            db.session.execute(db.text(
                "UPDATE table_config SET current_status = 'free' WHERE current_status IS NULL"
            ))
            db.session.commit()
            print("  + Set default status 'free' for existing tables")
        except Exception as e:
            db.session.rollback()
            print(f"  ! Error setting defaults: {e}")
        
        print("\nMigration completed successfully!")

if __name__ == '__main__':
    migrate()
