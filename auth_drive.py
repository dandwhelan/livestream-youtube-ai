"""
One-time Google Drive OAuth setup.
Run this once in a browser-capable terminal to save a refresh token.
After that, main.py uses the saved token headlessly forever.

Usage:
  python auth_drive.py

Requirements:
  - credentials/oauth_client.json  (OAuth 2.0 Desktop client secret from Google Cloud Console)
"""

from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
import json

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
CLIENT_SECRET = Path("credentials/oauth_client.json")
TOKEN_FILE = Path("credentials/drive_token.json")


def main():
    if not CLIENT_SECRET.exists():
        print(
            "\nERROR: credentials/oauth_client.json not found.\n"
            "\nSteps to create it:\n"
            "  1. console.cloud.google.com → your birdbox project\n"
            "  2. APIs & Services → Credentials\n"
            "  3. + Create Credentials → OAuth 2.0 Client ID\n"
            "  4. Application type: Desktop app → name: birdbox → Create\n"
            "  5. Download JSON → save as credentials/oauth_client.json\n"
            "\n(You can delete the service account key — it's no longer needed)\n"
        )
        return

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
    creds = flow.run_local_server(port=0)

    TOKEN_FILE.parent.mkdir(exist_ok=True)
    TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    print(f"\nAuth successful. Token saved to {TOKEN_FILE}")
    print("You can now run main.py — Drive uploads will work headlessly.")


if __name__ == "__main__":
    main()
