# AppointMint - Free Trial & Stripe Integration Update

## Overview

This update adds:
1. **Free Trial Registration Flow** - 14 days, 15 test bookings
2. **Stripe Payment Integration** - For going live with paid plans
3. **Go Live Page** - Pricing plans and checkout
4. **Widget Code Page** - Embed code for paid customers

## Files Updated

### Models
- `app/models/models.py` - Added trial and Stripe fields to Tenant model
- `app/models/__init__.py` - Export new constants

### Routes
- `app/routes/auth.py` - Enhanced registration with auto-setup
- `app/routes/admin.py` - Added Go Live button logic
- `app/routes/billing.py` - **NEW** - Stripe checkout and webhooks
- `app/routes/api.py` - Trial booking tracking

### Services
- `app/services/stripe_service.py` - **NEW** - Stripe integration
- `app/services/datetime_utils.py` - pytz-based datetime handling
- `app/services/ai_assistant_fallback.py` - Trial booking counter

### Templates
- `app/templates/auth/register.html` - New registration form
- `app/templates/admin/dashboard.html` - Trial status banner
- `app/templates/admin/restaurant_detail.html` - Go Live button
- `app/templates/billing/go_live.html` - **NEW** - Pricing page

### App Factory
- `app/__init__.py` - Register billing blueprint

## Environment Variables Required

Add these to your `.env` file:

```bash
# Stripe API Keys (get from https://dashboard.stripe.com/apikeys)
STRIPE_SECRET_KEY=sk_test_...
STRIPE_PUBLISHABLE_KEY=pk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...  # For webhook verification

# Stripe Price IDs (create in Stripe Dashboard)
STRIPE_STARTER_MONTHLY_PRICE_ID=price_...
STRIPE_STARTER_YEARLY_PRICE_ID=price_...
STRIPE_PROFESSIONAL_MONTHLY_PRICE_ID=price_...
STRIPE_PROFESSIONAL_YEARLY_PRICE_ID=price_...
```

## Stripe Setup Instructions

### 1. Create Products in Stripe Dashboard

Go to https://dashboard.stripe.com/products and create:

**Starter Plan:**
- Monthly: $49/month (recurring)
- Yearly: $470/year (recurring)

**Professional Plan:**
- Monthly: $99/month (recurring)
- Yearly: $950/year (recurring)

### 2. Get Price IDs

After creating products, copy the Price IDs (starts with `price_`) and add them to your `.env` file.

### 3. Set Up Webhook

1. Go to https://dashboard.stripe.com/webhooks
2. Add endpoint: `https://yourdomain.com/admin/billing/webhook`
3. Select events:
   - `checkout.session.completed`
   - `customer.subscription.updated`
   - `customer.subscription.deleted`
   - `invoice.payment_succeeded`
   - `invoice.payment_failed`
4. Copy the webhook signing secret to `STRIPE_WEBHOOK_SECRET`

## Database Migration

Since new columns were added to the `tenants` table, you need to either:

**Option A: Delete and recreate database (development only)**
```bash
rm appointmint.db
python run.py  # Will create new tables
```

**Option B: Run migration (production)**
```sql
ALTER TABLE tenants ADD COLUMN subscription_plan VARCHAR(50) DEFAULT 'free_trial';
ALTER TABLE tenants ADD COLUMN subscription_status VARCHAR(50) DEFAULT 'trial';
ALTER TABLE tenants ADD COLUMN trial_start_date DATETIME;
ALTER TABLE tenants ADD COLUMN trial_end_date DATETIME;
ALTER TABLE tenants ADD COLUMN trial_booking_count INTEGER DEFAULT 0;
ALTER TABLE tenants ADD COLUMN trial_booking_limit INTEGER DEFAULT 15;
ALTER TABLE tenants ADD COLUMN stripe_customer_id VARCHAR(255);
ALTER TABLE tenants ADD COLUMN stripe_subscription_id VARCHAR(255);
ALTER TABLE tenants ADD COLUMN stripe_payment_method_id VARCHAR(255);
ALTER TABLE tenants ADD COLUMN payment_status VARCHAR(50) DEFAULT 'pending';
ALTER TABLE tenants ADD COLUMN last_payment_date DATETIME;
ALTER TABLE tenants ADD COLUMN next_billing_date DATETIME;
```

## Testing the Flow

1. **Register**: Go to `/auth/register` and create a new account
2. **Trial**: You'll see the trial banner with 14 days and 15 bookings
3. **Test AI**: Make test reservations (counts against 15 limit)
4. **Go Live**: Click "Go Live" button to see pricing
5. **Checkout**: Click "Get Started" to go to Stripe checkout
6. **Widget**: After payment, access widget code at `/admin/restaurants/{id}/widget`

## Pricing Plans

| Plan | Monthly | Yearly | Features |
|------|---------|--------|----------|
| Starter | $49 | $470 | 1 restaurant, basic analytics |
| Professional | $99 | $950 | 5 restaurants, SMS, advanced analytics |
| Enterprise | Custom | Custom | Unlimited, API access, SLA |

## Trial Limits

- **Duration**: 14 days from registration
- **Bookings**: 15 test reservations
- **Expiry**: Whichever comes first

When trial expires, the AI assistant will inform customers that the restaurant is not accepting online reservations.
