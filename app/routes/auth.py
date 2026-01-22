from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_user, logout_user, login_required, current_user
from app.models import db, User, Tenant, Restaurant, Table, ROLE_TENANT_SUPERUSER, PLAN_FREE_TRIAL, STATUS_TRIAL
from datetime import datetime, timedelta, time

auth_bp = Blueprint('auth', __name__)


def create_sample_tables(restaurant):
    """Create sample tables for a new restaurant"""
    sample_tables = [
        {'table_number': 'Table 1', 'capacity': 2, 'location': 'Window'},
        {'table_number': 'Table 2', 'capacity': 4, 'location': 'Main Floor'},
        {'table_number': 'Table 3', 'capacity': 4, 'location': 'Main Floor'},
        {'table_number': 'Table 4', 'capacity': 6, 'location': 'Private Room'},
        {'table_number': 'Table 5', 'capacity': 8, 'location': 'Patio'},
    ]
    
    for table_data in sample_tables:
        table = Table(
            restaurant_id=restaurant.id,
            table_number=table_data['table_number'],
            capacity=table_data['capacity'],
            location=table_data['location'],
            is_active=True
        )
        db.session.add(table)


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """User login"""
    if current_user.is_authenticated:
        return redirect(url_for('admin.dashboard'))
    
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        remember = request.form.get('remember', False)
        
        user = User.query.filter_by(email=email).first()
        
        if user and user.check_password(password):
            if not user.is_active:
                flash('Your account has been deactivated. Please contact support.', 'error')
                return render_template('auth/login.html')
            
            login_user(user, remember=remember)
            user.last_login = datetime.utcnow()
            db.session.commit()
            
            next_page = request.args.get('next')
            return redirect(next_page or url_for('admin.dashboard'))
        
        flash('Invalid email or password.', 'error')
    
    return render_template('auth/login.html')


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    """User registration - creates new tenant, restaurant, and admin user"""
    if current_user.is_authenticated:
        return redirect(url_for('admin.dashboard'))
    
    if request.method == 'POST':
        # Get form data
        company_name = request.form.get('company_name', '').strip()
        restaurant_name = request.form.get('restaurant_name', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        first_name = request.form.get('first_name', '').strip()
        last_name = request.form.get('last_name', '').strip()
        phone = request.form.get('phone', '').strip()
        timezone = request.form.get('timezone', 'America/New_York')
        
        # Use restaurant name as company name if not provided
        if not company_name:
            company_name = restaurant_name
        
        # Validation
        errors = []
        
        if not restaurant_name:
            errors.append('Restaurant name is required.')
        
        if not email:
            errors.append('Email is required.')
        
        if not password:
            errors.append('Password is required.')
        elif len(password) < 8:
            errors.append('Password must be at least 8 characters.')
        
        if password != confirm_password:
            errors.append('Passwords do not match.')
        
        if User.query.filter_by(email=email).first():
            errors.append('Email already registered. Please login instead.')
        
        if errors:
            for error in errors:
                flash(error, 'error')
            return render_template('auth/register.html')
        
        try:
            # Create tenant with trial settings
            trial_start = datetime.utcnow()
            tenant = Tenant(
                name=company_name,
                email=email,
                phone=phone,
                subscription_plan=PLAN_FREE_TRIAL,
                subscription_status=STATUS_TRIAL,
                trial_start_date=trial_start,
                trial_end_date=trial_start + timedelta(days=14),
                trial_booking_count=0,
                trial_booking_limit=15,
                payment_status='pending'
            )
            db.session.add(tenant)
            db.session.flush()  # Get tenant ID
            
            # Create restaurant
            restaurant = Restaurant(
                tenant_id=tenant.id,
                name=restaurant_name,
                email=email,
                phone=phone,
                timezone=timezone,
                is_active=True,
                is_live=False,  # Not live until payment
                widget_welcome_message=f"Welcome to {restaurant_name}! I'm your AI reservation assistant. How can I help you today?"
            )
            db.session.add(restaurant)
            db.session.flush()  # Get restaurant ID
            
            # Create sample tables
            create_sample_tables(restaurant)
            
            # Create admin user for tenant
            user = User(
                email=email,
                first_name=first_name or 'Admin',
                last_name=last_name or 'User',
                role=ROLE_TENANT_SUPERUSER,
                tenant_id=tenant.id
            )
            user.set_password(password)
            db.session.add(user)
            
            db.session.commit()
            
            # Log in the user automatically
            login_user(user)
            user.last_login = datetime.utcnow()
            db.session.commit()
            
            flash(f'Welcome to AppointMint! Your 14-day free trial has started. You have 15 test bookings available.', 'success')
            return redirect(url_for('admin.restaurant_detail', id=restaurant.id))
            
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Registration error: {str(e)}")
            flash('An error occurred during registration. Please try again.', 'error')
            return render_template('auth/register.html')
    
    return render_template('auth/register.html')


@auth_bp.route('/logout')
@login_required
def logout():
    """User logout"""
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('main.index'))


@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    """Password reset request"""
    if request.method == 'POST':
        email = request.form.get('email')
        user = User.query.filter_by(email=email).first()
        
        # Always show success message to prevent email enumeration
        flash('If an account exists with that email, you will receive a password reset link.', 'info')
        return redirect(url_for('auth.login'))
    
    return render_template('auth/forgot_password.html')
