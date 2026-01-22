"""
Billing Routes for AppointMint

Handles subscription management, payment processing, and Stripe webhooks.
"""

from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, current_app
from flask_login import login_required, current_user

from app.models import db, Tenant, Restaurant
from app.services.stripe_service import (
    create_checkout_session,
    create_portal_session,
    handle_webhook_event,
    get_subscription_info,
    PLAN_FEATURES
)

billing_bp = Blueprint('billing', __name__)


@billing_bp.route('/pricing')
def pricing():
    """Show pricing page"""
    return render_template('billing/pricing.html', plans=PLAN_FEATURES)


@billing_bp.route('/go-live/<int:restaurant_id>')
@login_required
def go_live(restaurant_id):
    """Show go-live page for a restaurant"""
    restaurant = Restaurant.query.get_or_404(restaurant_id)
    
    # Check permissions
    if not current_user.can_view_restaurant(restaurant):
        flash('You do not have permission to access this restaurant.', 'error')
        return redirect(url_for('admin.dashboard'))
    
    tenant = restaurant.tenant
    
    return render_template(
        'billing/go_live.html',
        restaurant=restaurant,
        tenant=tenant,
        plans=PLAN_FEATURES,
        is_paid=tenant.is_paid if tenant else False,
        is_trial=tenant.is_trial if tenant else False,
        trial_days_remaining=tenant.trial_days_remaining if tenant else 0,
        trial_bookings_remaining=tenant.trial_bookings_remaining if tenant else 0
    )


@billing_bp.route('/checkout', methods=['POST'])
@login_required
def checkout():
    """Create Stripe checkout session"""
    plan = request.form.get('plan', 'starter')
    billing_period = request.form.get('billing_period', 'monthly')
    restaurant_id = request.form.get('restaurant_id')
    
    tenant = current_user.tenant
    if not tenant:
        flash('No tenant associated with your account.', 'error')
        return redirect(url_for('admin.dashboard'))
    
    # Build success/cancel URLs
    base_url = request.host_url.rstrip('/')
    success_url = f"{base_url}/admin/billing/success?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{base_url}/admin/billing/cancel"
    
    if restaurant_id:
        success_url += f"&restaurant_id={restaurant_id}"
        cancel_url += f"?restaurant_id={restaurant_id}"
    
    result = create_checkout_session(
        tenant=tenant,
        plan=plan,
        billing_period=billing_period,
        success_url=success_url,
        cancel_url=cancel_url
    )
    
    if result and result.get('url'):
        return redirect(result['url'])
    else:
        flash('Unable to create checkout session. Please try again.', 'error')
        if restaurant_id:
            return redirect(url_for('billing.go_live', restaurant_id=restaurant_id))
        return redirect(url_for('admin.settings'))


@billing_bp.route('/success')
@login_required
def success():
    """Handle successful payment"""
    session_id = request.args.get('session_id')
    restaurant_id = request.args.get('restaurant_id')
    
    tenant = current_user.tenant
    if tenant:
        # Refresh tenant data
        db.session.refresh(tenant)
        
        # Mark restaurants as live
        for restaurant in tenant.restaurants:
            restaurant.is_live = True
        db.session.commit()
    
    flash('Payment successful! Your subscription is now active.', 'success')
    
    if restaurant_id:
        return redirect(url_for('admin.widget_code', id=restaurant_id))
    return redirect(url_for('admin.dashboard'))


@billing_bp.route('/cancel')
@login_required
def cancel():
    """Handle cancelled payment"""
    restaurant_id = request.args.get('restaurant_id')
    
    flash('Payment was cancelled. You can try again when ready.', 'info')
    
    if restaurant_id:
        return redirect(url_for('billing.go_live', restaurant_id=restaurant_id))
    return redirect(url_for('admin.dashboard'))


@billing_bp.route('/portal')
@login_required
def portal():
    """Redirect to Stripe Customer Portal"""
    tenant = current_user.tenant
    if not tenant or not tenant.stripe_customer_id:
        flash('No billing information found.', 'error')
        return redirect(url_for('admin.settings'))
    
    return_url = request.host_url.rstrip('/') + url_for('admin.settings')
    portal_url = create_portal_session(tenant, return_url)
    
    if portal_url:
        return redirect(portal_url)
    else:
        flash('Unable to access billing portal. Please try again.', 'error')
        return redirect(url_for('admin.settings'))


@billing_bp.route('/subscription')
@login_required
def subscription():
    """Show subscription details"""
    tenant = current_user.tenant
    if not tenant:
        flash('No tenant associated with your account.', 'error')
        return redirect(url_for('admin.dashboard'))
    
    subscription_info = get_subscription_info(tenant) if tenant.stripe_subscription_id else None
    
    return render_template(
        'billing/subscription.html',
        tenant=tenant,
        subscription=subscription_info,
        plans=PLAN_FEATURES
    )


@billing_bp.route('/webhook', methods=['POST'])
def webhook():
    """Handle Stripe webhooks"""
    payload = request.get_data()
    sig_header = request.headers.get('Stripe-Signature', '')
    
    if handle_webhook_event(payload, sig_header):
        return jsonify({'status': 'success'}), 200
    else:
        return jsonify({'status': 'error'}), 400
