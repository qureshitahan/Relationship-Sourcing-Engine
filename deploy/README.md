# Azure deployment

## Automatic (preferred)

Pushing or merging to `main` runs [`.github/workflows/deploy-azure.yml`](../.github/workflows/deploy-azure.yml), which deploys:

- `backend/` → App Service `relationship-sourcing-api`
- built `frontend/` + `deploy/web-host/` → App Service `relationship-sourcing-web`

### One-time GitHub secrets

1. Azure Portal → each App Service → **Download publish profile**
2. GitHub → **Settings → Secrets and variables → Actions** → add:
   - `AZURE_WEBAPP_PUBLISH_PROFILE_API`
   - `AZURE_WEBAPP_PUBLISH_PROFILE_WEB`

App settings (API keys, Gmail App Passwords, `CORS_ORIGINS`, etc.) stay in Azure Configuration — not in git.

## Manual zips (optional fallback)

```bash
./deploy/build-zips.sh
```

Then upload the zips from `deploy/artifacts/` in the Azure Portal.
