#!/usr/bin/env python3
"""
Heroku Migration Script: Add Chatwoot Integration Columns in Postgres
==========================================================

Run this script on Heroku to add the Chatwoot columns to the restaurants table.

Usage:
    heroku run python migrate_chatwoot_heroku.py --app avoccado-appointmint
"""

import os
import sys
import secrets

# Set production environment
os.environ['FLASK_ENV'] = 'production'

def migrate():
    """Add Chatwoot columns to restaurants table"""
    print("=" * 60)
    print("Chatwoot Migration Script for Heroku")
    print("=" * 60)
    print()

    from app import create_app
    from app.models import db
    from sqlalchemy import text

    app = create_app('production')

    with app.app_context():
        try:
            # Define columns to add
            columns = [
                ('webhook_token', 'VARCHAR(64)'),
                ('chatwoot_account_id', 'VARCHAR(50)'),
                ('chatwoot_inbox_id', 'VARCHAR(50)'),
                ('chatwoot_api_key', 'VARCHAR(255)'),
                ('chatwoot_base_url', 'VARCHAR(255)')
            ]

            print("Adding Chatwoot columns to restaurants table...")
            print()

            for col_name, col_type in columns:
                try:
                    # PostgreSQL syntax with IF NOT EXISTS
                    sql = f"ALTER TABLE restaurants ADD COLUMN IF NOT EXISTS {col_name} {col_type}"
                    db.session.execute(text(sql))
                    db.session.commit()
                    print(f"  ✓ Column '{col_name}' added (or already exists)")
                except Exception as e:
                    error_msg = str(e).lower()
                    if 'already exists' in error_msg or 'duplicate' in error_msg:
                        print(f"  ✓ Column '{col_name}' already exists")
                    else:
                        print(f"  ✗ Error adding '{col_name}': {e}")
                        db.session.rollback()

            print()
            print("Generating webhook tokens for restaurants without one...")

            # Now generate webhook tokens for existing restaurants
            try:
                # First check which restaurants need tokens
                result = db.session.execute(text(
                    "SELECT id, name FROM restaurants WHERE webhook_token IS NULL"
                ))
                restaurants = result.fetchall()

                for restaurant_id, restaurant_name in restaurants:
                    token = secrets.token_urlsafe(32)
                    db.session.execute(text(
                        "UPDATE restaurants SET webhook_token = :token WHERE id = :id"
                    ), {'token': token, 'id': restaurant_id})
                    print(f"  ✓ Generated token for '{restaurant_name}'")

                if not restaurants:
                    print("  ✓ All restaurants already have webhook tokens")

                db.session.commit()

            except Exception as e:
                print(f"  ✗ Error generating tokens: {e}")
                db.session.rollback()

            print()
            print("=" * 60)
            print("✅ Migration completed successfully!")
            print("=" * 60)
            print()
            print("Next steps:")
            print("1. Restart the app: heroku restart --app avoccado-appointmint")
            print("2. Go to Restaurant → Chatwoot button to configure")
            print()

        except Exception as e:
            print()
            print("=" * 60)
            print(f"❌ Migration failed: {str(e)}")
            print("=" * 60)
            sys.exit(1)


if __name__ == '__main__':
    migrate()
