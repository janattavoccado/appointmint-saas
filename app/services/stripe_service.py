"""
Stripe Payment Service for AppointMint

Handles all Stripe-related operations including:
- Customer creation
- Checkout session creation
- Subscription management
- Webhook processing
"""

import os
import json
from datetime import datetime
from typing import Optional, Dict, Any

import stripe
from flask import current_app

from app.models import db, Tenant, StripeEvent, PLAN_STARTER, STATUS_ACTIVE


# Stripe price IDs (configure these in your Stripe dashboard)
STRIPE_PRICES = {
    'starter_monthly': os.environ.get('STRIPE_PRICE_STARTER_MONTHLY', 'price_starter_monthly'),
    'starter_yearly': os.environ.get('STRIPE_PRICE_STARTER_YEARLY', 'price_starter_yearly'),
    'professional_monthly': os.environ.get('STRIPE_PRICE_PROFESSIONAL_MONTHLY', 'price_professional_monthly'),
    'professional_yearly': os.environ.get('STRIPE_PRICE_PROFESSIONAL_YEARLY', 'price_professional_yearly'),
}

# Plan features
PLAN_FEATURES = {
    'starter': {
        'name': 'Starter',
        'price_monthly': 49,
        'price_yearly': 470,  # ~20% discount
        'features': [
            'Unlimited reservations',
            'AI voice & text assistant',
            'Email notifications',
            'Basic analytics',
            '1 restaurant',
            'Email support',
        ]
    },
    'professional': {
        'name': 'Professional',
        'price_monthly': 99,
        'price_yearly': 950,  # ~20% discount
        'features': [
            'Everything in Starter',
            'SMS notifications',
            'Advanced analytics',
            'Up to 5 restaurants',
            'Priority support',
            'Custom branding',
        ]
    },
    'enterprise': {
        'name': 'Enterprise',
        'price_monthly': 'Custom',
        'price_yearly': 'Custom',
        'features': [
            'Everything in Professional',
            'Unlimited restaurants',
            'API access',
            'Dedicated support',
            'Custom integrations',
            'SLA guarantee',
        ]
    }
}


def get_stripe_client():
    """Get configured Stripe client"""
    api_key = os.environ.get('STRIPE_SECRET_KEY') or current_app.config.get('STRIPE_SECRET_KEY')
    if api_key:
        stripe.api_key = api_key
    return stripe


def create_customer(tenant: Tenant) -> Optional[str]:
    """
    Create a Stripe customer for a tenant.
    
    Args:
        tenant: The tenant to create a customer for
        
    Returns:
        The Stripe customer ID or None if failed
    """
    try:
        stripe_client = get_stripe_client()
        
        customer = stripe_client.Customer.create(
            email=tenant.email,
            name=tenant.name,
            metadata={
                'tenant_id': str(tenant.id),
                'app': 'appointmint'
            }
        )
        
        tenant.stripe_customer_id = customer.id
        db.session.commit()
        
        return customer.id
        
    except stripe.error.StripeError as e:
        current_app.logger.error(f"Stripe customer creation error: {str(e)}")
        return None


def create_checkout_session(
    tenant: Tenant,
    plan: str = 'starter',
    billing_period: str = 'monthly',
    success_url: str = None,
    cancel_url: str = None
) -> Optional[Dict[str, Any]]:
    """
    Create a Stripe Checkout session for subscription.
    
    Args:
        tenant: The tenant subscribing
        plan: The plan name (starter, professional)
        billing_period: monthly or yearly
        success_url: URL to redirect on success
        cancel_url: URL to redirect on cancel
        
    Returns:
        Dict with session_id and url, or None if failed
    """
    try:
        stripe_client = get_stripe_client()
        
        # Create customer if not exists
        if not tenant.stripe_customer_id:
            customer_id = create_customer(tenant)
            if not customer_id:
                return None
        else:
            customer_id = tenant.stripe_customer_id
        
        # Get price ID
        price_key = f'{plan}_{billing_period}'
        price_id = STRIPE_PRICES.get(price_key)
        
        if not price_id:
            current_app.logger.error(f"Invalid plan/billing: {price_key}")
            return None
        
        # Create checkout session
        session = stripe_client.checkout.Session.create(
            customer=customer_id,
            payment_method_types=['card'],
            line_items=[{
                'price': price_id,
                'quantity': 1,
            }],
            mode='subscription',
            success_url=success_url or f"{current_app.config.get('BASE_URL', '')}/admin/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=cancel_url or f"{current_app.config.get('BASE_URL', '')}/admin/billing/cancel",
            metadata={
                'tenant_id': str(tenant.id),
                'plan': plan,
                'billing_period': billing_period
            },
            subscription_data={
                'metadata': {
                    'tenant_id': str(tenant.id),
                    'plan': plan
                }
            }
        )
        
        return {
            'session_id': session.id,
            'url': session.url
        }
        
    except stripe.error.StripeError as e:
        current_app.logger.error(f"Stripe checkout session error: {str(e)}")
        return None


def create_portal_session(tenant: Tenant, return_url: str = None) -> Optional[str]:
    """
    Create a Stripe Customer Portal session for managing subscription.
    
    Args:
        tenant: The tenant
        return_url: URL to return to after portal
        
    Returns:
        Portal session URL or None if failed
    """
    try:
        stripe_client = get_stripe_client()
        
        if not tenant.stripe_customer_id:
            return None
        
        session = stripe_client.billing_portal.Session.create(
            customer=tenant.stripe_customer_id,
            return_url=return_url or f"{current_app.config.get('BASE_URL', '')}/admin/settings"
        )
        
        return session.url
        
    except stripe.error.StripeError as e:
        current_app.logger.error(f"Stripe portal session error: {str(e)}")
        return None


def handle_webhook_event(payload: bytes, sig_header: str) -> bool:
    """
    Handle Stripe webhook events.
    
    Args:
        payload: Raw request body
        sig_header: Stripe signature header
        
    Returns:
        True if handled successfully, False otherwise
    """
    try:
        stripe_client = get_stripe_client()
        webhook_secret = os.environ.get('STRIPE_WEBHOOK_SECRET') or current_app.config.get('STRIPE_WEBHOOK_SECRET')
        
        if webhook_secret:
            event = stripe_client.Webhook.construct_event(
                payload, sig_header, webhook_secret
            )
        else:
            # For testing without webhook signature verification
            data = json.loads(payload)
            event = stripe.Event.construct_from(data, stripe.api_key)
        
        # Check if we've already processed this event
        existing = StripeEvent.query.filter_by(stripe_event_id=event.id).first()
        if existing:
            current_app.logger.info(f"Duplicate webhook event: {event.id}")
            return True
        
        # Log the event
        stripe_event = StripeEvent(
            stripe_event_id=event.id,
            event_type=event.type,
            data=json.dumps(event.data.object) if hasattr(event.data, 'object') else None
        )
        db.session.add(stripe_event)
        
        # Handle specific event types
        if event.type == 'checkout.session.completed':
            handle_checkout_completed(event.data.object)
        elif event.type == 'customer.subscription.created':
            handle_subscription_created(event.data.object)
        elif event.type == 'customer.subscription.updated':
            handle_subscription_updated(event.data.object)
        elif event.type == 'customer.subscription.deleted':
            handle_subscription_deleted(event.data.object)
        elif event.type == 'invoice.paid':
            handle_invoice_paid(event.data.object)
        elif event.type == 'invoice.payment_failed':
            handle_invoice_payment_failed(event.data.object)
        
        stripe_event.processed = True
        db.session.commit()
        
        return True
        
    except stripe.error.SignatureVerificationError as e:
        current_app.logger.error(f"Webhook signature verification failed: {str(e)}")
        return False
    except Exception as e:
        current_app.logger.error(f"Webhook handling error: {str(e)}")
        db.session.rollback()
        return False


def handle_checkout_completed(session):
    """Handle checkout.session.completed event"""
    tenant_id = session.get('metadata', {}).get('tenant_id')
    if not tenant_id:
        return
    
    tenant = Tenant.query.get(int(tenant_id))
    if not tenant:
        return
    
    plan = session.get('metadata', {}).get('plan', 'starter')
    
    # Update tenant subscription
    tenant.stripe_subscription_id = session.get('subscription')
    tenant.activate_paid_subscription(plan)
    
    # Mark restaurant as live
    for restaurant in tenant.restaurants:
        restaurant.is_live = True
    
    db.session.commit()
    current_app.logger.info(f"Tenant {tenant_id} subscription activated via checkout")


def handle_subscription_created(subscription):
    """Handle customer.subscription.created event"""
    customer_id = subscription.get('customer')
    tenant = Tenant.query.filter_by(stripe_customer_id=customer_id).first()
    
    if tenant:
        tenant.stripe_subscription_id = subscription.get('id')
        tenant.subscription_status = STATUS_ACTIVE
        db.session.commit()


def handle_subscription_updated(subscription):
    """Handle customer.subscription.updated event"""
    customer_id = subscription.get('customer')
    tenant = Tenant.query.filter_by(stripe_customer_id=customer_id).first()
    
    if tenant:
        status = subscription.get('status')
        if status == 'active':
            tenant.subscription_status = STATUS_ACTIVE
            tenant.payment_status = 'ok'
        elif status == 'past_due':
            tenant.subscription_status = 'past_due'
            tenant.payment_status = 'failed'
        elif status in ['canceled', 'unpaid']:
            tenant.subscription_status = 'cancelled'
            tenant.payment_status = 'failed'
            # Mark restaurants as not live
            for restaurant in tenant.restaurants:
                restaurant.is_live = False
        
        db.session.commit()


def handle_subscription_deleted(subscription):
    """Handle customer.subscription.deleted event"""
    customer_id = subscription.get('customer')
    tenant = Tenant.query.filter_by(stripe_customer_id=customer_id).first()
    
    if tenant:
        tenant.subscription_status = 'cancelled'
        tenant.payment_status = 'failed'
        tenant.stripe_subscription_id = None
        
        # Mark restaurants as not live
        for restaurant in tenant.restaurants:
            restaurant.is_live = False
        
        db.session.commit()


def handle_invoice_paid(invoice):
    """Handle invoice.paid event"""
    customer_id = invoice.get('customer')
    tenant = Tenant.query.filter_by(stripe_customer_id=customer_id).first()
    
    if tenant:
        tenant.payment_status = 'ok'
        tenant.last_payment_date = datetime.utcnow()
        
        # Calculate next billing date
        if invoice.get('lines', {}).get('data'):
            period_end = invoice['lines']['data'][0].get('period', {}).get('end')
            if period_end:
                tenant.next_billing_date = datetime.fromtimestamp(period_end)
        
        db.session.commit()


def handle_invoice_payment_failed(invoice):
    """Handle invoice.payment_failed event"""
    customer_id = invoice.get('customer')
    tenant = Tenant.query.filter_by(stripe_customer_id=customer_id).first()
    
    if tenant:
        tenant.payment_status = 'failed'
        tenant.subscription_status = 'past_due'
        db.session.commit()


def get_subscription_info(tenant: Tenant) -> Optional[Dict[str, Any]]:
    """
    Get subscription information for a tenant.
    
    Args:
        tenant: The tenant
        
    Returns:
        Dict with subscription info or None
    """
    try:
        stripe_client = get_stripe_client()
        
        if not tenant.stripe_subscription_id:
            return None
        
        subscription = stripe_client.Subscription.retrieve(tenant.stripe_subscription_id)
        
        return {
            'id': subscription.id,
            'status': subscription.status,
            'plan': subscription.get('metadata', {}).get('plan', 'starter'),
            'current_period_start': datetime.fromtimestamp(subscription.current_period_start),
            'current_period_end': datetime.fromtimestamp(subscription.current_period_end),
            'cancel_at_period_end': subscription.cancel_at_period_end,
        }
        
    except stripe.error.StripeError as e:
        current_app.logger.error(f"Error getting subscription info: {str(e)}")
        return None
