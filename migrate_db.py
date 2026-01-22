#!/usr/bin/env python3
"""
Database Migration Script for Heroku
=====================================

This script runs during Heroku's release phase to:
1. Create all database tables
2. Create the default admin user if not exists
3. Generate webhook tokens for restaurants without one

This is called automatically by the Procfile release command.
"""

import os
import sys
import secrets

# Set production environment
os.environ['FLASK_ENV'] = 'production'

from app import create_app
from app.models import db, User, Restaurant, ROLE_ADMIN


def generate_webhook_token():
    """Generate a unique webhook token"""
    return secrets.token_urlsafe(32)


def migrate():
    """Run database migrations"""
    print("=" * 60)
    print("Running Database Migration")
    print("=" * 60)
    
    app = create_app('production')
    
    with app.app_context():
        try:
            # Create all tables
            print("Creating database tables...")
            db.create_all()
            print("✓ Database tables created successfully")
            
            # Create default admin user if not exists
            print("Checking for admin user...")
            admin = User.query.filter_by(email='admin@appointmint.com').first()
            
            if not admin:
                print("Creating default admin user...")
                admin = User(
                    email='admin@appointmint.com',
                    first_name='System',
                    last_name='Administrator',
                    role=ROLE_ADMIN,
                    tenant_id=None,
                    is_active=True
                )
                # Use environment variable for admin password or default
                admin_password = os.environ.get('ADMIN_PASSWORD', 'admin123')
                admin.set_password(admin_password)
                db.session.add(admin)
                db.session.commit()
                print("✓ Default admin user created")
                print("  Email: admin@appointmint.com")
                print("  Password: (set via ADMIN_PASSWORD env var or 'admin123')")
            else:
                print("✓ Admin user already exists")
            
            # Generate webhook tokens for restaurants without one
            print("Checking restaurant webhook tokens...")
            restaurants_without_token = Restaurant.query.filter(
                (Restaurant.webhook_token == None) | (Restaurant.webhook_token == '')
            ).all()
            
            if restaurants_without_token:
                for restaurant in restaurants_without_token:
                    restaurant.webhook_token = generate_webhook_token()
                    print(f"  ✓ Generated webhook token for '{restaurant.name}'")
                db.session.commit()
            else:
                print("✓ All restaurants have webhook tokens")
            
            print()
            print("=" * 60)
            print("✅ Migration completed successfully!")
            print("=" * 60)
            
        except Exception as e:
            print()
            print("=" * 60)
            print(f"❌ Migration failed: {str(e)}")
            print("=" * 60)
            sys.exit(1)


if __name__ == '__main__':
    migrate()
