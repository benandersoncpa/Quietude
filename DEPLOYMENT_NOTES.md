# Deployment to Streamlit Cloud - Authentication Update

## Summary of Changes

The application has been updated to use Streamlit Secrets for Google API credentials instead of relying on a local `credentials.json` file. This enables deployment on Streamlit Cloud while maintaining local development capabilities.

## Files Modified

### 1. **plan_my_day.py**
- Added `import json` for parsing secrets
- Updated `authenticate_google()` function to load credentials from `st.secrets["GOOGLE_CLIENT_SECRETS"]` instead of the local `.streamlit/credentials.json` file
- Uses `InstalledAppFlow.from_client_config()` instead of `from_client_secrets_file()`

### 2. **fetch_communications.py**
- Added `import json` for parsing secrets
- Updated `main()` function to load credentials from secrets with fallback to local file for development
- Checks if `GOOGLE_CLIENT_SECRETS` is available in secrets, otherwise falls back to local credentials file

### 3. **quietude.py**
- Added `import json` for parsing secrets
- Updated `authenticate_google()` function to load credentials from `st.secrets["GOOGLE_CLIENT_SECRETS"]`
- Uses `InstalledAppFlow.from_client_config()` for secret-based authentication

### 4. **.streamlit/secrets.toml**
- Added `GOOGLE_CLIENT_SECRETS` key containing the full Google service account credentials as a JSON string
- This file is properly excluded from git via `.gitignore`

## Deployment Instructions

### For Streamlit Cloud:

1. Go to your Streamlit Cloud app dashboard
2. Click on "Settings" → "Secrets"
3. Add the following secret:
   ```
   GOOGLE_CLIENT_SECRETS = '[Your OAuth2 or Service Account JSON]'
   ```
4. Add all other secrets:
   - `SPREADSHEET_ID`
   - `COMPLETE_LABEL_ID`
   - `LABEL_ID_AEGIS_EMAIL`
   - `LABEL_ID_PERSONAL_EMAIL`
   - `LABEL_ID_AEGIS_GV`
   - `LABEL_ID_1099_GV`

### For Local Development:

1. The `.streamlit/secrets.toml` file already contains the necessary credentials
2. Replace placeholder values with your actual Google API credentials
3. Ensure `.streamlit/secrets.toml` is never committed (already in `.gitignore`)

## How It Works

1. **Local Development**: The app reads credentials from `.streamlit/secrets.toml`
2. **Streamlit Cloud**: The app reads credentials from the Secrets panel in your Streamlit Cloud dashboard
3. **Token Caching**: The app saves a `token.json` file locally to cache authentication tokens
   - This file is excluded from version control via `.gitignore`
   - Each deployment environment maintains its own token cache

## Important Notes

- ⚠️ **Do NOT commit `.streamlit/secrets.toml`** - it contains sensitive credentials
- ✅ The `.gitignore` file already includes `.streamlit/secrets.toml` and `credentials.json`
- 📝 The `token.json` file created at runtime is also excluded from git
- 🔒 When deploying to Streamlit Cloud, use the Secrets management panel, NOT the local secrets file
