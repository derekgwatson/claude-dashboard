"""Generate VAPID key pair for Web Push notifications.

Run once, then set the printed values as environment variables on your server.
"""

import base64

from cryptography.hazmat.primitives.asymmetric.ec import generate_private_key, SECP256R1
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

key = generate_private_key(SECP256R1())

raw_priv = key.private_numbers().private_value.to_bytes(32, "big")
priv_b64 = base64.urlsafe_b64encode(raw_priv).decode().rstrip("=")

pub_raw = key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
pub_b64 = base64.urlsafe_b64encode(pub_raw).decode().rstrip("=")

print("Set these environment variables on your server:\n")
print(f"VAPID_PRIVATE_KEY={priv_b64}")
print(f"VAPID_PUBLIC_KEY={pub_b64}")
print(f"VAPID_CLAIMS_EMAIL=mailto:your@email.com")
