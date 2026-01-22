from datetime import datetime, timedelta
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


# User roles:
# - 'admin': System administrator - exclusive access to all tenants, users, and restaurants
# - 'tenant_superuser': Tenant administrator - full access to their tenant's data only
# - 'tenant_user': Regular tenant user - limited access to their tenant's data

ROLE_ADMIN = 'admin'
ROLE_TENANT_SUPERUSER = 'tenant_superuser'
ROLE_TENANT_USER = 'tenant_user'

ALL_ROLES = [ROLE_ADMIN, ROLE_TENANT_SUPERUSER, ROLE_TENANT_USER]
TENANT_ROLES = [ROLE_TENANT_SUPERUSER, ROLE_TENANT_USER]

# Subscription plans
PLAN_FREE_TRIAL = 'free_trial'
PLAN_STARTER = 'starter'
PLAN_PROFESSIONAL = 'professional'
PLAN_ENTERPRISE = 'enterprise'

# Subscription statuses
STATUS_TRIAL = 'trial'
STATUS_ACTIVE = 'active'
STATUS_PAST_DUE = 'past_due'
STATUS_CANCELLED = 'cancelled'
STATUS_EXPIRED = 'expired'

# Trial limits
TRIAL_DAYS = 14
TRIAL_MAX_BOOKINGS = 15


class Tenant(db.Model):
    """Multi-tenant client (restaurant owner/company)"""
    __tablename__ = 'tenants'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    phone = db.Column(db.String(20))
    
    # Subscription info
    subscription_plan = db.Column(db.String(50), default=PLAN_FREE_TRIAL)
    subscription_status = db.Column(db.String(20), default=STATUS_TRIAL)
    
    # Trial tracking
    trial_start_date = db.Column(db.DateTime, default=datetime.utcnow)
    trial_end_date = db.Column(db.DateTime)
    trial_booking_count = db.Column(db.Integer, default=0)
    trial_booking_limit = db.Column(db.Integer, default=TRIAL_MAX_BOOKINGS)
    
    # Stripe integration
    stripe_customer_id = db.Column(db.String(100))
    stripe_subscription_id = db.Column(db.String(100))
    stripe_payment_method_id = db.Column(db.String(100))
    
    # Payment status
    payment_status = db.Column(db.String(20), default='pending')  # pending, ok, failed
    last_payment_date = db.Column(db.DateTime)
    next_billing_date = db.Column(db.DateTime)
    
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    restaurants = db.relationship('Restaurant', backref='tenant', lazy=True, cascade='all, delete-orphan')
    users = db.relationship('User', backref='tenant', lazy=True, cascade='all, delete-orphan')
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Set trial end date if not provided
        if not self.trial_end_date and self.trial_start_date:
            self.trial_end_date = self.trial_start_date + timedelta(days=TRIAL_DAYS)
        elif not self.trial_end_date:
            self.trial_start_date = datetime.utcnow()
            self.trial_end_date = self.trial_start_date + timedelta(days=TRIAL_DAYS)
    
    @property
    def is_trial(self):
        """Check if tenant is on free trial"""
        return self.subscription_plan == PLAN_FREE_TRIAL and self.subscription_status == STATUS_TRIAL
    
    @property
    def is_trial_expired(self):
        """Check if trial has expired (by date or bookings)"""
        if not self.is_trial:
            return False
        
        # Check date expiration
        if self.trial_end_date and datetime.utcnow() > self.trial_end_date:
            return True
        
        # Check booking limit
        if self.trial_booking_count >= self.trial_booking_limit:
            return True
        
        return False
    
    @property
    def trial_days_remaining(self):
        """Get number of days remaining in trial"""
        if not self.is_trial or not self.trial_end_date:
            return 0
        
        remaining = (self.trial_end_date - datetime.utcnow()).days
        return max(0, remaining)
    
    @property
    def trial_bookings_remaining(self):
        """Get number of bookings remaining in trial"""
        if not self.is_trial:
            return 0
        
        return max(0, self.trial_booking_limit - self.trial_booking_count)
    
    @property
    def can_make_booking(self):
        """Check if tenant can make new bookings"""
        # Paid customers can always make bookings
        if self.payment_status == 'ok':
            return True
        
        # Trial customers check limits
        if self.is_trial and not self.is_trial_expired:
            return True
        
        return False
    
    @property
    def is_paid(self):
        """Check if tenant has paid subscription"""
        return self.payment_status == 'ok'
    
    def increment_booking_count(self):
        """Increment the trial booking count"""
        self.trial_booking_count = (self.trial_booking_count or 0) + 1
    
    def activate_paid_subscription(self, plan=PLAN_STARTER):
        """Activate paid subscription after successful payment"""
        self.subscription_plan = plan
        self.subscription_status = STATUS_ACTIVE
        self.payment_status = 'ok'
        self.last_payment_date = datetime.utcnow()
    
    def __repr__(self):
        return f'<Tenant {self.name}>'


class User(UserMixin, db.Model):
    """Users who can access the system"""
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    first_name = db.Column(db.String(50), nullable=False)
    last_name = db.Column(db.String(50), nullable=False)
    role = db.Column(db.String(20), default=ROLE_TENANT_USER)
    is_active = db.Column(db.Boolean, default=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=True)  # Null for system admin only
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    @property
    def full_name(self):
        return f'{self.first_name} {self.last_name}'
    
    def is_admin(self):
        """Check if user is system administrator"""
        return self.role == ROLE_ADMIN
    
    def is_tenant_superuser(self):
        """Check if user is tenant superuser"""
        return self.role == ROLE_TENANT_SUPERUSER
    
    def is_tenant_user(self):
        """Check if user is regular tenant user"""
        return self.role == ROLE_TENANT_USER
    
    def can_manage_tenant(self, tenant_id):
        """Check if user can manage a specific tenant"""
        if self.is_admin():
            return True
        if self.tenant_id == tenant_id and self.role in [ROLE_TENANT_SUPERUSER]:
            return True
        return False
    
    def can_view_tenant(self, tenant_id):
        """Check if user can view a specific tenant"""
        if self.is_admin():
            return True
        return self.tenant_id == tenant_id
    
    def can_manage_restaurant(self, restaurant):
        """Check if user can manage a specific restaurant"""
        if self.is_admin():
            return True
        if self.tenant_id == restaurant.tenant_id and self.role in [ROLE_TENANT_SUPERUSER]:
            return True
        return False
    
    def can_view_restaurant(self, restaurant):
        """Check if user can view a specific restaurant"""
        if self.is_admin():
            return True
        return self.tenant_id == restaurant.tenant_id
    
    def can_manage_users(self):
        """Check if user can manage other users"""
        return self.role in [ROLE_ADMIN, ROLE_TENANT_SUPERUSER]
    
    def __repr__(self):
        return f'<User {self.email}>'


class Restaurant(db.Model):
    """Restaurant belonging to a tenant"""
    __tablename__ = 'restaurants'
    
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    address = db.Column(db.String(255))
    city = db.Column(db.String(100))
    state = db.Column(db.String(50))
    zip_code = db.Column(db.String(20))
    country = db.Column(db.String(50), default='USA')
    phone = db.Column(db.String(20))
    email = db.Column(db.String(120))
    cuisine_type = db.Column(db.String(100))  # Italian, French, etc.
    description = db.Column(db.Text)  # Restaurant description
    timezone = db.Column(db.String(50), default='UTC')
    is_active = db.Column(db.Boolean, default=True)
    is_live = db.Column(db.Boolean, default=False)  # Whether the restaurant is live (accepting real bookings)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Knowledge base - Markdown content for FAQ, menu, hours, etc.
    knowledge_base = db.Column(db.Text)  # Markdown content
    knowledge_base_updated_at = db.Column(db.DateTime)
    
    # Widget settings
    widget_primary_color = db.Column(db.String(20), default='#2D8B7A')
    widget_position = db.Column(db.String(20), default='bottom-right')
    widget_welcome_message = db.Column(db.Text)
    
    # Relationships
    tables = db.relationship('Table', backref='restaurant', lazy=True, cascade='all, delete-orphan')
    reservations = db.relationship('Reservation', backref='restaurant', lazy=True, cascade='all, delete-orphan')
    operating_hours = db.relationship('OperatingHours', backref='restaurant', lazy=True, cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<Restaurant {self.name}>'


class Table(db.Model):
    """Tables in a restaurant"""
    __tablename__ = 'tables'
    
    id = db.Column(db.Integer, primary_key=True)
    restaurant_id = db.Column(db.Integer, db.ForeignKey('restaurants.id'), nullable=False)
    table_number = db.Column(db.String(20), nullable=False)
    capacity = db.Column(db.Integer, nullable=False)
    location = db.Column(db.String(50))  # indoor, outdoor, patio, etc.
    is_available = db.Column(db.Boolean, default=True)
    is_active = db.Column(db.Boolean, default=True)
    notes = db.Column(db.Text)
    
    # Relationships
    reservations = db.relationship('Reservation', backref='table', lazy=True)
    
    @property
    def name(self):
        """Return table name (alias for table_number)"""
        return self.table_number
    
    def __repr__(self):
        return f'<Table {self.table_number} at Restaurant {self.restaurant_id}>'


class OperatingHours(db.Model):
    """Operating hours for a restaurant"""
    __tablename__ = 'operating_hours'
    
    id = db.Column(db.Integer, primary_key=True)
    restaurant_id = db.Column(db.Integer, db.ForeignKey('restaurants.id'), nullable=False)
    day_of_week = db.Column(db.Integer, nullable=False)  # 0=Monday, 6=Sunday
    open_time = db.Column(db.Time)
    close_time = db.Column(db.Time)
    is_closed = db.Column(db.Boolean, default=False)
    
    def __repr__(self):
        return f'<OperatingHours {self.day_of_week} for Restaurant {self.restaurant_id}>'


class Reservation(db.Model):
    """Table reservations"""
    __tablename__ = 'reservations'
    
    id = db.Column(db.Integer, primary_key=True)
    restaurant_id = db.Column(db.Integer, db.ForeignKey('restaurants.id'), nullable=False)
    table_id = db.Column(db.Integer, db.ForeignKey('tables.id'), nullable=True)
    customer_name = db.Column(db.String(100), nullable=False)
    customer_email = db.Column(db.String(120))
    customer_phone = db.Column(db.String(20), nullable=False)
    party_size = db.Column(db.Integer, nullable=False)
    reservation_date = db.Column(db.Date, nullable=False)
    reservation_time = db.Column(db.Time, nullable=False)
    duration_minutes = db.Column(db.Integer, default=90)
    status = db.Column(db.String(20), default='pending')  # pending, confirmed, cancelled, completed, no_show
    special_requests = db.Column(db.Text)
    source = db.Column(db.String(20), default='web')  # web, phone, voice_ai, walk_in
    is_trial_booking = db.Column(db.Boolean, default=False)  # Track if this was a trial booking
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<Reservation {self.id} for {self.customer_name}>'


class AIConversation(db.Model):
    """Log of AI voice/text conversations"""
    __tablename__ = 'ai_conversations'
    
    id = db.Column(db.Integer, primary_key=True)
    restaurant_id = db.Column(db.Integer, db.ForeignKey('restaurants.id'), nullable=False)
    reservation_id = db.Column(db.Integer, db.ForeignKey('reservations.id'), nullable=True)
    conversation_type = db.Column(db.String(20), default='voice')  # voice, text
    transcript = db.Column(db.Text)
    customer_phone = db.Column(db.String(20))
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    ended_at = db.Column(db.DateTime)
    status = db.Column(db.String(20), default='completed')  # in_progress, completed, failed
    tokens_used = db.Column(db.Integer, default=0)
    
    def __repr__(self):
        return f'<AIConversation {self.id}>'


class StripeEvent(db.Model):
    """Log of Stripe webhook events"""
    __tablename__ = 'stripe_events'
    
    id = db.Column(db.Integer, primary_key=True)
    stripe_event_id = db.Column(db.String(100), unique=True, nullable=False)
    event_type = db.Column(db.String(100), nullable=False)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=True)
    data = db.Column(db.Text)  # JSON data
    processed = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<StripeEvent {self.stripe_event_id}>'
