import os
from flask import Flask
from flask_login import LoginManager
from flask_migrate import Migrate
from config import config

login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.login_message_category = 'info'

migrate = Migrate()


def create_app(config_name=None):
    """Application factory"""
    if config_name is None:
        config_name = os.environ.get('FLASK_ENV', 'default')
    
    app = Flask(__name__)
    app.config.from_object(config[config_name])
    
    # Handle Heroku's DATABASE_URL format for PostgreSQL
    if config_name == 'production':
        database_url = os.environ.get('DATABASE_URL', '')
        if database_url.startswith('postgres://'):
            database_url = database_url.replace('postgres://', 'postgresql://', 1)
        app.config['SQLALCHEMY_DATABASE_URI'] = database_url
    
    # Initialize extensions
    from app.models import db
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    
    # User loader for Flask-Login
    @login_manager.user_loader
    def load_user(user_id):
        from app.models import User
        return User.query.get(int(user_id))
    
    # Register blueprints
    from app.routes.main import main_bp
    from app.routes.auth import auth_bp
    from app.routes.admin import admin_bp
    from app.routes.api import api_bp
    from app.routes.billing import billing_bp
    
    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(admin_bp, url_prefix='/admin')
    app.register_blueprint(api_bp, url_prefix='/api')
    app.register_blueprint(billing_bp, url_prefix='/admin/billing')
    
    # Create database tables and default admin user (for development)
    # In production, use Flask-Migrate for database management
    if config_name != 'production':
        with app.app_context():
            db.create_all()
            create_default_admin(db)
    
    return app


def create_default_admin(db):
    """Create default admin user if not exists"""
    from app.models import User, ROLE_ADMIN
    
    admin = User.query.filter_by(email='admin@appointmint.com').first()
    if not admin:
        admin = User(
            email='admin@appointmint.com',
            first_name='System',
            last_name='Administrator',
            role=ROLE_ADMIN,
            tenant_id=None,  # Admin has no tenant
            is_active=True
        )
        admin.set_password('admin123')
        db.session.add(admin)
        db.session.commit()
        print('Default admin user created: admin@appointmint.com / admin123')
