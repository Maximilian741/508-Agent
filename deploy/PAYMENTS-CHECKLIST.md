# PAYMENTS-CHECKLIST.md — verify money works, end to end

This is the literal, step-by-step payment bring-up. Work top to bottom. Every
"VERIFY" block is a command or click you can run to prove the step worked
before moving on. Nothing here requires reading code.

## What is ALREADY verified for you (2026-09-07, automated, in production mode)

You do not need to re-test any of this — it is pinned by the smoke suite
(`smoke_billing`, `smoke_billing_safety`, `smoke_subscriptions`,
`smoke_remediate_jobs`, `smoke_no_charge_no_fix`):

- **Webhook is fail-closed.** Without `STRIPE_WEBHOOK_SECRET` the endpoint
  returns 503 and processes nothing, so nobody can forge a
  `checkout.session.completed` to self-credit.
- **Signatures + replay.** Wrong signature → 400. Events older than 5 minutes
  → rejected. A duplicate delivery of the same event grants **nothing** (the
  ledger description is the idempotency key).
- **No free credits in production.** The dev mock `/credits/purchase` returns
  503 in `ENVIRONMENT=production` and 409 the moment `STRIPE_SECRET_KEY` is
  set. Verified against a real migrated production-mode database.
- **Charges are honest.** A corrupt upload doesn't charge; a broke user gets
  402 and an untouched balance; a remediation only charges when fixes
  actually persist into the file; if your browser disconnects mid-remediate,
  the charge is deferred to the first successful download and taken exactly
  once ("Recent remediations" on the dashboard is the recovery path).
- **Subscription lifecycle.** First-month grant, renewal grant, cancel →
  inactive, annual = 12× upfront, `subscription_create` invoices don't
  double-grant.
- **Card data never touches our servers.** We use Stripe Checkout (Stripe's
  hosted page). Our backend only ever sees price IDs and webhook events.

What CANNOT be verified without your Stripe account — and is exactly what
this checklist walks you through: real Checkout sessions against
api.stripe.com, real webhook deliveries from Stripe's servers, the customer
portal, and your live price IDs.

---

## Step 1. Create the Stripe account (only you)

1. Go to https://dashboard.stripe.com/register and create the account.
2. Complete business verification (name, address, bank account for payouts).
   You can do all of Step 2–4 in **test mode** while verification is pending.
3. Never paste secret keys anywhere but the server's `.env`. Anyone with
   `sk_live_...` can move your money.

## Step 2. TEST MODE first — create the products

Stripe test mode and live mode are **separate universes**: products, prices,
keys and webhooks made in one do not exist in the other. You will do this
twice (once now in test, once in Step 6 in live).

In the dashboard, toggle **Test mode** ON (top right), then Products → Add
product. Create 7 products/prices and note each `price_...` id:

| Product name        | Type      | Suggested price | Env var to hold the price id |
|---------------------|-----------|-----------------|------------------------------|
| Starter pack (50 credits)   | One-time  | $9    | `STRIPE_PRICE_STARTER` |
| Pro pack (250 credits)      | One-time  | $39   | `STRIPE_PRICE_PRO` |
| Studio pack (1300 credits)  | One-time  | $179  | `STRIPE_PRICE_STUDIO` |
| Team (1000 cr/mo, 3 seats)  | Recurring monthly | $99/mo  | `STRIPE_PRICE_TEAM` |
| Team annual (12000 cr/yr)   | Recurring yearly  | $990/yr | `STRIPE_PRICE_TEAM_ANNUAL` |
| Business (6000 cr/mo, 10 seats) | Recurring monthly | $499/mo | `STRIPE_PRICE_BUSINESS` |
| Business annual (72000 cr/yr)   | Recurring yearly  | $4990/yr | `STRIPE_PRICE_BUSINESS_ANNUAL` |

The CREDIT amounts are fixed in the app (50/250/1300 packs; 1000/mo Team,
6000/mo Business; annual grants 12× upfront). The DOLLAR amounts are yours to
choose — whatever you set on the Stripe price is what's charged. If you pick
different dollar amounts than the suggestions, also set
`STRIPE_PRICE_TEAM_USD=<number>` etc. so the admin MRR screen reports right.

## Step 3. Configure the server (test keys)

On the VPS, edit `/opt/508-agent/.env`:

```
STRIPE_SECRET_KEY=sk_test_...           # Developers -> API keys (test mode)
STRIPE_PRICE_STARTER=price_...
STRIPE_PRICE_PRO=price_...
STRIPE_PRICE_STUDIO=price_...
STRIPE_PRICE_TEAM=price_...
STRIPE_PRICE_TEAM_ANNUAL=price_...
STRIPE_PRICE_BUSINESS=price_...
STRIPE_PRICE_BUSINESS_ANNUAL=price_...
PUBLIC_BASE_URL=https://app.yourdomain.com
```

(Leave `STRIPE_WEBHOOK_SECRET` for Step 4.) Then:

```
docker compose up -d --force-recreate backend
```

**VERIFY** — from your laptop:

```
curl -s https://api.yourdomain.com/billing/config
```

Expect `"enabled": true` and every tier/plan listing a configured price. Any
`false`/missing price = the matching env var is empty or has a typo.

## Step 4. The webhook (test mode)

This is the step people get wrong. The webhook is how credits actually land
after payment — Checkout succeeding is NOT enough on its own.

1. Dashboard (test mode) → Developers → Webhooks → **Add endpoint**.
2. Endpoint URL: `https://api.yourdomain.com/billing/webhook`
3. Events to send — select exactly these seven:
   - `checkout.session.completed`
   - `checkout.session.async_payment_succeeded`
   - `checkout.session.async_payment_failed`
   - `invoice.payment_succeeded`
   - `invoice.payment_failed`
   - `customer.subscription.updated`
   - `customer.subscription.deleted`

   Why the three `async_payment` / `payment_failed` ones matter: a bank-debit
   payment (ACH, SEPA, Bacs) finishes Checkout as **unpaid**. We grant nothing
   until `checkout.session.async_payment_succeeded` arrives. Without that
   event, those customers pay and never get their credits.

   **Already created this endpoint with the old four?** Developers → Webhooks
   → click the endpoint → edit its events → add the three missing ones
   (`checkout.session.async_payment_succeeded`,
   `checkout.session.async_payment_failed`, `invoice.payment_failed`) → save.
   The signing secret does not change. Do this in test mode AND live mode.
4. After creating it, click **Reveal** on the signing secret (`whsec_...`) and
   put it in `.env` as `STRIPE_WEBHOOK_SECRET=whsec_...`, then
   `docker compose up -d --force-recreate backend` again.

**VERIFY** — in the webhook's page click **Send test event** →
`checkout.session.completed`. The dashboard should show the delivery got a
**2xx** response. The endpoint's event list should show all seven events. A `400 invalid_signature` means the `whsec_` in `.env`
doesn't match THIS endpoint (each endpoint has its own secret); a
`503 webhook_not_configured` means the env var didn't load (recreate the
container, check for quotes/whitespace).

## Step 5. The money test (test mode, real UI)

1. Open your live site in a private window, sign in with a throwaway account.
2. Note the credit balance (top-right chip).
3. Billing → buy the **Starter pack**. You land on Stripe's checkout page.
4. Pay with the test card `4242 4242 4242 4242`, any future expiry, any CVC,
   any ZIP.
5. You are redirected back to the app. Within a few seconds the balance chip
   should read **+50**.

**VERIFY the plumbing end to end:**

- Balance went up by exactly 50 (refresh the page if the chip is stale).
- Stripe dashboard → Payments shows the payment; Developers → Webhooks →
  your endpoint shows `checkout.session.completed` delivered with **200**.
- **Replay safety:** on that event click **Resend**. The delivery should be
  200 again but the balance must NOT change (idempotent grant).
- Repeat once for a **Team monthly** subscription (same test card): balance
  +1000, and Billing shows the plan active. Then Dashboard → open the
  customer → cancel the subscription; the app should show it inactive within
  a minute (the `customer.subscription.deleted` webhook).
- **Portal:** in the app click "Manage subscription" — Stripe's customer
  portal should open. If it errors, enable the portal once at
  Dashboard → Settings → Billing → Customer portal → Save.

If the payment succeeded but credits did NOT land: the webhook is the
problem, not the payment — check the endpoint's delivery log first, it shows
you the exact response our server gave.

## Step 6. Flip to LIVE

1. Toggle Test mode OFF. Repeat Step 2 (create the same 7 products/prices in
   live mode — new `price_...` ids) and Step 4 (new webhook endpoint → new
   `whsec_...`).
2. Swap `.env` to `sk_live_...`, the live `price_...` ids, and the live
   `whsec_...`. Recreate the backend container.
3. `curl -s https://api.yourdomain.com/billing/config` → `"enabled": true`.
4. Make ONE real purchase yourself with a real card (Starter, $9), confirm
   +50 credits, then refund it in the Stripe dashboard (Payments → refund).
   The refund does not claw back the credits — that's fine for your own test.

## Step 7. After launch, watch two pages for the first week

- Developers → Webhooks → your endpoint: any non-200 deliveries mean credits
  aren't landing; the response body says why.
- Payments: anything in "Uncaptured/Failed" that a customer complains about.

## Failure table

| Symptom | Meaning | Fix |
|---|---|---|
| `/billing/config` says `enabled: false` | `STRIPE_SECRET_KEY` empty | set it, recreate container |
| Checkout button → 503 `billing_not_configured` | same | same |
| Checkout button → 400 `price_not_configured` | that tier's `STRIPE_PRICE_*` empty/typo | set the right `price_...` id |
| Paid but no credits | webhook failing | endpoint delivery log → our response body |
| Webhook 400 `invalid_signature` | wrong `whsec_` for that endpoint | copy the secret from THAT endpoint |
| Webhook 503 `webhook_not_configured` | env not loaded | recreate container; check `.env` syntax |
| Webhook 200 but `ignored` | event type not in our seven | fix the event selection on the endpoint |
| Bank-debit (ACH/SEPA) customer paid but no credits | `checkout.session.async_payment_succeeded` not selected on the endpoint | add it (Step 4), then **Resend** that event from the delivery log |
| Webhook 200 with `payment_pending` | Checkout finished unpaid (delayed bank debit) | nothing — credits land on `async_payment_succeeded` |
| Subscriber with overage on gets 402 after a few top-ups in a day | overage is capped at `OVERAGE_MAX_PACKS_PER_DAY` packs ($10 each) per rolling 24h, default 3 | intended; raise the number in `.env` and recreate the backend if a customer needs more |
| `/credits/purchase` → 409 `use_stripe_checkout` | correct behavior with Stripe on | nothing — the UI uses Checkout |
