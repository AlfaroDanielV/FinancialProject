# Phase 6b — Google Cloud setup (one-time)

> **Audience**: Daniel (project owner / sole admin during the personal MVP
> phase). This is a one-time setup and a recurring "add-a-tester" task.

The goal: get a working OAuth client ID/secret pair and a consent screen
that lets a small group of beta testers grant the bot read-only access to
their Gmail.

We stay in **Testing mode** (max 100 testers, manually approved). Going to
production verification is a Phase 8 / SaaS concern; for now we explicitly
opt into the warning screen Google shows for unverified apps. The bot
anticipates this in `bot/handlers/gmail.py` so users know it's expected.

---

## 1. Create the project

1. Go to <https://console.cloud.google.com/projectcreate>.
2. Project name: `finance-agent-dev` (separate `finance-agent-prod` later).
3. Take note of the resulting **Project ID** — you'll see it in the URL bar
   and on the dashboard. You won't need it after setup, but it's useful
   for support tickets.

## 2. Enable the Gmail API

1. APIs & Services → **Library**.
2. Search **Gmail API** → click → **Enable**.
3. Without this, every `users.messages.list` call returns 403 with
   `accessNotConfigured`.

## 3. Configure the OAuth consent screen

APIs & Services → **OAuth consent screen**.

1. **User type**: External. (Internal is only for Workspace orgs and we're
   using personal Gmail accounts.)
2. **App information**:
   - App name: `Finance Agent` (visible to users at consent time)
   - User support email: `dalfaroviquez@gmail.com`
   - App logo: optional, skip
   - App domain / authorized domains: skip while in Testing mode
   - Developer contact information: same email as user support
3. **Scopes** → click *Add or remove scopes*:
   - Add `https://www.googleapis.com/auth/gmail.readonly`
   - **Do not add anything else.** Extra scopes either expand the consent
     screen or trip Google's restricted-scope policy and force verification.
4. **Test users**:
   - Click *Add users* and paste each beta tester's Gmail email, one per
     line.
   - Add Daniel's `dalfaroviquez@gmail.com` first.
   - Hard limit: 100 users while in Testing mode. We're nowhere close.
5. **Summary** → Back to dashboard. Publishing status stays **Testing**.

## 4. Create OAuth client credentials

APIs & Services → **Credentials** → *Create credentials* → *OAuth client ID*.

1. **Application type**: Web application.
2. **Name**: `finance-agent-backend-dev` (or `-prod` when you create the
   prod project).
3. **Authorized redirect URIs** — add both for dev:
   - `http://localhost:8000/api/v1/gmail/oauth/callback`
   - `http://127.0.0.1:8000/api/v1/gmail/oauth/callback`
   (Google rejects the callback if the redirect URI doesn't match
   character-for-character. Do not add a trailing slash.)
4. For prod, additionally add the public callback URL once you know it,
   e.g. `https://api.finance-agent.example.com/api/v1/gmail/oauth/callback`.
5. Click **Create**.
6. The dialog shows **Client ID** and **Client secret**. Copy both.
7. Add them to `.env`:

   ```ini
   GMAIL_CLIENT_ID=<copied client id>
   GMAIL_CLIENT_SECRET=<copied client secret>
   GMAIL_REDIRECT_URI=http://localhost:8000/api/v1/gmail/oauth/callback
   GMAIL_OAUTH_STATE_SECRET=<generate with: python -c "import secrets; print(secrets.token_urlsafe(48))">
   ```

## 5. Adding a new beta tester (recurring task)

This step is needed every time someone new wants to connect their Gmail
while we're in Testing mode.

1. Get the user's Gmail address (the one they'll authorize).
2. Console → APIs & Services → OAuth consent screen → **Test users** →
   *Add users* → paste the email → Save.
3. Tell the user they can now run `/conectar_gmail` in the bot.

If a user runs `/conectar_gmail` before being added, Google returns
`error=access_denied` with `error_description=The given user is not in
the test list`. Our `/oauth/callback` handler catches this and shows
`gmail-error.html`; the user is told to ask Daniel for an invite.

## 6. Switching off / cleanup

To stop a user's access at the Google side (independent of `/desconectar_gmail`):

1. APIs & Services → Credentials → click the OAuth client.
2. There is no per-user revocation here — Google only lets the user
   revoke from <https://myaccount.google.com/permissions>.
3. Server-side revocation is in our hands: `POST /api/v1/gmail/oauth/revoke`
   on the user's row + delete the Key Vault entry. The user will be
   unable to use the bot's Gmail flows even if they don't revoke from
   Google's side; new `/conectar_gmail` calls re-issue OAuth from scratch.

---

## What the user sees on first connect

In **Testing mode** with an unverified app, Google's screen looks like:

> Google hasn't verified this app
>
> The app is requesting access to sensitive info in your Google Account.
> Until the developer (...) verifies this app with Google, you should only
> use it if you trust the developer.
>
> [Back to safety]                              [Advanced]

The user clicks **Advanced** → **Go to Finance Agent (unsafe)** → then
sees the actual scope grant screen with a checkbox for the Gmail readonly
scope. They click **Continue** and Google redirects to our callback.

The bot warns them about this in `bot/handlers/gmail.py` *before* they
click the link, so the warning isn't a surprise.

---

## Verification (Phase 8+)

When we're ready to go past 100 users, Google verification requires:

- A privacy policy URL on a domain we control
- A YouTube demo of the OAuth flow
- A security assessment if we use restricted scopes
- 2–6 weeks of waiting

Not in scope for 6b. Don't start this until you have a clear product reason.
