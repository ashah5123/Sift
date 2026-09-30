# GitHub App setup

## 1. Register the App

Go to https://github.com/settings/apps/new and fill in:

| Field | Value |
|---|---|
| GitHub App name | `sift-triage-<yourname>` (must be globally unique) |
| Homepage URL | `https://github.com/ashah5123/Sift` |
| Webhook URL | `https://<your-host>/webhook` (see step 3) |
| Webhook secret | a long random string, e.g. `python3 -c "import secrets; print(secrets.token_hex(32))"` |

**Repository permissions**

| Permission | Access | Why |
|---|---|---|
| Issues | Read & write | read issues, add labels and comments |
| Pull requests | Read & write | same for PRs |
| Contents | Read-only | read `CODEOWNERS` and `.github/sift.yml` |
| Metadata | Read-only | required |

**Subscribe to events:** Issues, Pull request.

**Where can this App be installed:** "Only on this account" while developing; switch to "Any account" before the Marketplace listing.

After creating it:
- Note the **App ID**.
- Under "Private keys", **Generate a private key** and save the `.pem` outside the repo.
- Click **Install App** and install it on one or two of your own repos.

## 2. Services (all free tiers)

| Service | Suggestion | Env var |
|---|---|---|
| Postgres + pgvector | Neon or Supabase | `DATABASE_URL` |
| Redis | Upstash (use the `rediss://` URL) | `REDIS_URL` |

Copy `.env.example` to `.env`, fill it in, then apply the schema:

```bash
python -m sift.db
```

## 3. Run

```bash
uvicorn sift.app:app_factory --factory --host 0.0.0.0 --port 8000   # webhook receiver
python -m sift.worker                                                # queue consumer
```

GitHub must be able to reach `/webhook`. Either deploy both processes to a free host (Render, Railway, Fly.io) or, for local testing, forward webhooks through https://smee.io.

## 4. Verify

- On the App's settings page, **Advanced → Recent Deliveries** should show the initial `ping` with a 202.
- Open an issue on an installed repo. A row appears in `issues` and one in `events`.
- Click **Redeliver** on that delivery. The response is `{"status": "duplicate"}` and no new row is written.

## Notes

- Events sent by bots (including Sift itself) are ignored, so Sift's own labels are never mistaken for maintainer corrections.
- A delivery that fails 5 times goes to the `sift:dead` Redis stream. Redelivering it from GitHub within 7 days is ignored as a duplicate; replay it from the dead-letter stream instead.
