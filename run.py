import os
from app import create_app
from app.models import db, User, Tenant

# Get configuration from environment
config_name = os.environ.get('FLASK_ENV', 'development')
app = create_app(config_name)


def init_db():
    """Initialize database with default admin user"""
    with app.app_context():
        db.create_all()
        
        # Check if admin user exists
        admin = User.query.filter_by(email='admin@appointmint.com').first()
        if not admin:
            # Create default admin user
            admin = User(
                email='admin@appointmint.com',
                first_name='System',
                last_name='Admin',
                role='admin',
                is_active=True
            )
            admin.set_password('admin123')  # Change this in production!
            db.session.add(admin)
            db.session.commit()
            print('Default admin user created: admin@appointmint.com / admin123')


if __name__ == '__main__':
    # Only init_db in development
    if config_name == 'development':
        init_db()
    
    # Get port from environment (Heroku sets this)
    port = int(os.environ.get('PORT', 5000))
    debug = config_name == 'development'
    
    app.run(host='0.0.0.0', port=port, debug=debug)
