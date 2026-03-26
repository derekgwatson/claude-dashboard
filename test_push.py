"""Manual test for Web Push — run on the server to diagnose push issues."""

import json
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cloud.db")

# Check pywebpush
try:
    from pywebpush import webpush
    print("pywebpush: OK")
except ImportError:
    print("pywebpush: NOT INSTALLED")
    exit(1)

# Check VAPID keys
priv = os.environ.get("VAPID_PRIVATE_KEY", "")
pub = os.environ.get("VAPID_PUBLIC_KEY", "")
email = os.environ.get("VAPID_CLAIMS_EMAIL", "")
print(f"VAPID_PRIVATE_KEY set: {bool(priv)}")
print(f"VAPID_PUBLIC_KEY set: {bool(pub)}")
print(f"VAPID_CLAIMS_EMAIL: {email or '(not set)'}")

if not priv:
    print("\nNo VAPID_PRIVATE_KEY — source your .env first:")
    print("  export $(grep -v '^#' .env | xargs)")
    exit(1)

# Check subscriptions
db = sqlite3.connect(DB_PATH)
db.row_factory = sqlite3.Row
sub = db.execute("SELECT endpoint, keys_json FROM push_subscriptions").fetchone()
db.close()

if not sub:
    print("\nNo push subscriptions in DB")
    exit(1)

print(f"Subscription endpoint: {sub['endpoint'][:60]}...")

# Send test push
print("\nSending test push...")
try:
    result = webpush(
        subscription_info={"endpoint": sub["endpoint"], "keys": json.loads(sub["keys_json"])},
        data=json.dumps({"title": "Test", "body": "Hello from server!"}),
        vapid_private_key=priv,
        vapid_claims={"sub": email or "mailto:test@test.com"},
    )
    print(f"Response: {result.status_code}")
    if result.status_code == 201:
        print("SUCCESS — check your phone!")
    else:
        print(f"Unexpected status. Body: {result.text}")
except Exception as e:
    print(f"FAILED: {e}")
