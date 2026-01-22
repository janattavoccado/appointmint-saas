#!/usr/bin/env python3
"""
Database Migration Script: Add Knowledge Base Fields
=====================================================

This script adds the knowledge_base and knowledge_base_updated_at columns
to the restaurants table.

Usage:
    python migrate_knowledge_base.py

The script will:
1. Check if the columns already exist
2. Add them if they don't exist
3. Report the status of the migration
"""

import sqlite3
import os
import sys

# Database path - adjust if your database is in a different location
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'appointmint.db')


def get_connection():
    """Get database connection"""
    if not os.path.exists(DB_PATH):
        print(f"❌ Error: Database not found at {DB_PATH}")
        print("   Make sure you're running this script from the AppointMint directory")
        sys.exit(1)
    
    return sqlite3.connect(DB_PATH)


def column_exists(cursor, table_name, column_name):
    """Check if a column exists in a table"""
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = [row[1] for row in cursor.fetchall()]
    return column_name in columns


def migrate():
    """Run the migration"""
    print("=" * 60)
    print("Knowledge Base Migration Script")
    print("=" * 60)
    print()
    
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        # Check and add knowledge_base column
        if column_exists(cursor, 'restaurants', 'knowledge_base'):
            print("✓ Column 'knowledge_base' already exists")
        else:
            print("Adding column 'knowledge_base'...")
            cursor.execute("""
                ALTER TABLE restaurants 
                ADD COLUMN knowledge_base TEXT
            """)
            print("✓ Column 'knowledge_base' added successfully")
        
        # Check and add knowledge_base_updated_at column
        if column_exists(cursor, 'restaurants', 'knowledge_base_updated_at'):
            print("✓ Column 'knowledge_base_updated_at' already exists")
        else:
            print("Adding column 'knowledge_base_updated_at'...")
            cursor.execute("""
                ALTER TABLE restaurants 
                ADD COLUMN knowledge_base_updated_at DATETIME
            """)
            print("✓ Column 'knowledge_base_updated_at' added successfully")
        
        # Commit changes
        conn.commit()
        
        print()
        print("=" * 60)
        print("✅ Migration completed successfully!")
        print("=" * 60)
        print()
        print("You can now use the Knowledge Base feature:")
        print("1. Go to Admin → Restaurants → [Your Restaurant]")
        print("2. Click the 'Knowledge Base' button")
        print("3. Add your restaurant's FAQ, menu, hours, etc.")
        print()
        
    except Exception as e:
        conn.rollback()
        print()
        print("=" * 60)
        print(f"❌ Migration failed: {str(e)}")
        print("=" * 60)
        sys.exit(1)
        
    finally:
        conn.close()


def verify():
    """Verify the migration was successful"""
    print("Verifying migration...")
    
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("PRAGMA table_info(restaurants)")
        columns = {row[1]: row[2] for row in cursor.fetchall()}
        
        print()
        print("Current 'restaurants' table columns:")
        print("-" * 40)
        for col_name, col_type in columns.items():
            print(f"  {col_name}: {col_type}")
        
        print()
        
        if 'knowledge_base' in columns and 'knowledge_base_updated_at' in columns:
            print("✅ All knowledge base columns are present")
            return True
        else:
            print("❌ Some columns are missing")
            return False
            
    finally:
        conn.close()


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Migrate database for Knowledge Base feature')
    parser.add_argument('--verify', action='store_true', help='Only verify the migration status')
    args = parser.parse_args()
    
    if args.verify:
        verify()
    else:
        migrate()
        verify()
