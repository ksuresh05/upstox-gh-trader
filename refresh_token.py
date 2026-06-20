"""
Daily token refresh helper.

Upstox access tokens expire every day, so this needs to run each
trading morning before 9:15 AM. It:
  1. Exchanges your Upstox authorization code for a fresh access token
  2. Updates the UPSTOX_ACCESS_TOKEN secret in your GitHub repo directly
     (using the GitHub CLI, so the next scheduled run picks it up automatically)

Prerequisites (one-time setup):
  - Install GitHub CLI: https://cli.github.com
  - Run: gh auth login   (one-time browser login)

Usage each morning:
  python3 refresh_token.py
"""

import subprocess
import sys
import urllib.request
import urllib.parse
import json
import os

GITHUB_REPO = os.environ.get("GITHUB_REPO", "")  # e.g. "yourusername/upstox-gh-trader"


def get_upstox_token(api_key, api_secret, redirect_uri, auth_code):
    url = "https://api.upstox.com/v2/login/authorization/token"
    payload = {
        "code": auth_code.strip(),
        "client_id": api_key.strip(),
        "client_secret": api_secret.strip(),
        "redirect_uri": redirect_uri.strip(),
        "grant_type": "authorization_code",
    }
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return True, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        try:
            return False, json.loads(body)
        except json.JSONDecodeError:
            return False, {"raw_error": body}


def update_github_secret(repo, token_value):
    """Uses GitHub CLI to set the secret. Requires `gh auth login` done once."""
    result = subprocess.run(
        ["gh", "secret", "set", "UPSTOX_ACCESS_TOKEN", "--repo", repo, "--body", token_value],
        capture_output=True, text=True,
    )
    return result.returncode == 0, result.stdout + result.stderr


def main():
    print("=" * 60)
    print("  Daily Upstox Token Refresh")
    print("=" * 60)

    repo = GITHUB_REPO or input("GitHub repo (e.g. yourusername/upstox-gh-trader): ").strip()
    api_key = input("Upstox API Key: ").strip()
    api_secret = input("Upstox API Secret: ").strip()
    redirect_uri = input("Redirect URI [https://127.0.0.1/]: ").strip() or "https://127.0.0.1/"
    auth_code = input("Authorization Code (from today's login redirect): ").strip()

    print("\nExchanging code for access token...")
    success, result = get_upstox_token(api_key, api_secret, redirect_uri, auth_code)

    if not success or not result.get("access_token"):
        print("\n❌ Failed to get access token.")
        print(json.dumps(result, indent=2))
        sys.exit(1)

    token = result["access_token"]
    print(f"\n✅ Got access token (first 20 chars): {token[:20]}...")

    print(f"\nUpdating GitHub secret on {repo}...")
    ok, output = update_github_secret(repo, token)

    if ok:
        print("✅ GitHub secret updated! Today's trading session will use the new token.")
    else:
        print("❌ Failed to update GitHub secret via CLI.")
        print(output)
        print("\nManual fallback: GitHub repo -> Settings -> Secrets and variables -> Actions")
        print(f"-> Update UPSTOX_ACCESS_TOKEN with: {token}")


if __name__ == "__main__":
    main()
