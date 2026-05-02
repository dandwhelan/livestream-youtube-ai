"""
One-time YouTube OAuth setup for chapter markers.
Run this once in a browser-capable terminal to save a refresh token.
After that, main.py updates chapter markers headlessly.

Usage:
  python auth_youtube.py

Requirements:
  - credentials/oauth_client.json  (OAuth 2.0 Desktop client secret from Google Cloud Console)
    NOTE: The YouTube Data API v3 must be enabled in your Google Cloud project.
          console.cloud.google.com → APIs & Services → Enable APIs → YouTube Data API v3
"""

from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube"]
CLIENT_SECRET = Path("credentials/oauth_client.json")
TOKEN_FILE = Path("credentials/youtube_token.json")


def main():
    if not CLIENT_SECRET.exists():
        print(
            "\nERROR: credentials/oauth_client.json not found.\n"
            "\nSteps to create it:\n"
            "  1. console.cloud.google.com → your birdbox project\n"
            "  2. APIs & Services → Enable APIs → search YouTube Data API v3 → Enable\n"
            "  3. APIs & Services → Credentials → + Create Credentials → OAuth 2.0 Client ID\n"
            "  4. Application type: Desktop app → name: birdbox → Create\n"
            "  5. Download JSON → save as credentials/oauth_client.json\n"
        )
        return

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
    creds = flow.run_local_server(port=0)

    TOKEN_FILE.parent.mkdir(exist_ok=True)
    TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    print(f"\nAuth successful. Token saved to {TOKEN_FILE}")
    print("Restart main.py — chapter markers will now update automatically.")


if __name__ == "__main__":
    main()
