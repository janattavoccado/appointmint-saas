#!/usr/bin/env python3
"""
Database Migration Script: Add Chatwoot Integration Fields
==========================================================

This script adds the Chatwoot webhook integration fields to the restaurants table:
- webhook_token: Unique token for webhook URL
- chatwoot_account_id: Chatwoot account ID
- chatwoot_inbox_id: Chatwoot inbox ID
- chatwoot_api_key: Chatwoot API access token
- chatwoot_base_url: Chatwoot instance URL

Usage:
    python migrate_chatwoot.py

The script will:
1. Check if the columns already exist
2. Add them if they don't exist
3. Generate webhook tokens for existing restaurants
4. Report the status of the migration
"""

import sqlite3
import os
import sys
import secrets

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


def generate_token():
    """Generate a unique webhook token"""
    return secrets.token_urlsafe(32)


def migrate():
    """Run the migration"""
    print("=" * 60)
    print("Chatwoot Integration Migration Script")
    print("=" * 60)
    print()
    
    conn = get_connection()
    cursor = conn.cursor()
    
    columns_to_add = [
        ('webhook_token', 'VARCHAR(64) UNIQUE'),
        ('chatwoot_account_id', 'VARCHAR(50)'),
        ('chatwoot_inbox_id', 'VARCHAR(50)'),
        ('chatwoot_api_key', 'VARCHAR(255)'),
        ('chatwoot_base_url', 'VARCHAR(255)')
    ]
    
    try:
        for column_name, column_type in columns_to_add:
            if column_exists(cursor, 'restaurants', column_name):
                print(f"✓ Column '{column_name}' already exists")
            else:
                print(f"Adding column '{column_name}'...")
                cursor.execute(f"""
                    ALTER TABLE restaurants 
                    ADD COLUMN {column_name} {column_type}
                """)
                print(f"✓ Column '{column_name}' added successfully")
        
        # Commit column changes
        conn.commit()
        
        # Generate webhook tokens for existing restaurants without one
        print()
        print("Generating webhook tokens for existing restaurants...")
        cursor.execute("SELECT id, name FROM restaurants WHERE webhook_token IS NULL")
        restaurants = cursor.fetchall()
        
        for restaurant_id, restaurant_name in restaurants:
            token = generate_token()
            cursor.execute(
                "UPDATE restaurants SET webhook_token = ? WHERE id = ?",
                (token, restaurant_id)
            )
            print(f"  ✓ Generated token for '{restaurant_name}'")
        
        if not restaurants:
            print("  No restaurants needed token generation")
        
        # Commit token updates
        conn.commit()
        
        print()
        print("=" * 60)
        print("✅ Migration completed successfully!")
        print("=" * 60)
        print()
        print("You can now use the Chatwoot integration:")
        print("1. Go to Admin → Restaurants → [Your Restaurant]")
        print("2. Click the 'Chatwoot' button")
        print("3. Copy the webhook URL and configure in Chatwoot")
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
        print("Chatwoot-related columns in 'restaurants' table:")
        print("-" * 40)
        
        chatwoot_columns = ['webhook_token', 'chatwoot_account_id', 'chatwoot_inbox_id', 
                           'chatwoot_api_key', 'chatwoot_base_url']
        
        all_present = True
        for col in chatwoot_columns:
            if col in columns:
                print(f"  ✓ {col}: {columns[col]}")
            else:
                print(f"  ✗ {col}: MISSING")
                all_present = False
        
        print()
        
        if all_present:
            print("✅ All Chatwoot columns are present")
            
            # Show restaurants with tokens
            cursor.execute("SELECT id, name, webhook_token FROM restaurants")
            restaurants = cursor.fetchall()
            
            if restaurants:
                print()
                print("Restaurants with webhook tokens:")
                print("-" * 40)
                for rid, name, token in restaurants:
                    token_status = "✓ Has token" if token else "✗ No token"
                    print(f"  {name}: {token_status}")
            
            return True
        else:
            print("❌ Some columns are missing")
            return False
            
    finally:
        conn.close()


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Migrate database for Chatwoot integration')
    parser.add_argument('--verify', action='store_true', help='Only verify the migration status')
    args = parser.parse_args()
    
    if args.verify:
        verify()
    else:
        migrate()
        verify()
