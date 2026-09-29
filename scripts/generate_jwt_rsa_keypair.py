"""Generate an RSA keypair for AR-2 asymmetric JWT signing, and print every
env var an operator needs to paste, in the correct rollout order.

Only data_gateway ever holds the private key this script prints — never copy
JWT_PRIVATE_KEY / JWT_ACTIVE_KID into any other service's .env. See
docs/ACCEPTED-RISKS.md AR-2 and the module docstring of shared/auth/jwt.py for
the full rollout sequence and why RS256 (not EdDSA) was chosen.

Run:  python scripts/generate_jwt_rsa_keypair.py [--kid KID] [--bits 2048]

Output is base64-encoded PEM (see shared.auth.jwt.encode_key_material) — a
single-line value that survives a .env file, a shell export or a cloud
provider's env-var UI without any newline-escaping surprises, which a
multi-line PEM block does not.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

# Repo root -> importable without installing `shared` as a package, matching
# how every service's own conftest.py / entrypoint sets PYTHONPATH.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.auth.jwt import encode_key_material  # noqa: E402


def _default_kid() -> str:
    """Today's date (UTC) — readable in logs, and good enough as a rotation
    label since a new keypair is generated at most a handful of times a year."""
    return datetime.now(tz=UTC).strftime("%Y-%m-%d")


def generate_keypair(bits: int) -> tuple[str, str]:
    """Return (private_pem, public_pem) for a fresh RSA keypair."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=bits)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    return private_pem, public_pem


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--kid",
        default=_default_kid(),
        help="Key id stamped into new tokens and used to select the public key "
        "(default: today's date, e.g. 2026-09-28).",
    )
    parser.add_argument(
        "--bits",
        type=int,
        default=2048,
        help="RSA key size in bits (default: 2048 — the NIST-recommended floor; "
        "3072/4096 also work but every extra bit costs CPU on every verify).",
    )
    parser.add_argument(
        "--merge-public-keys",
        default=None,
        help="Existing JWT_PUBLIC_KEYS JSON to merge the new key into (for "
        "rotating alongside an already-deployed key), e.g. "
        "--merge-public-keys '{\"2026-01-01\": \"...\"}'. Omit to print a "
        "fresh single-entry object.",
    )
    args = parser.parse_args()

    private_pem, public_pem = generate_keypair(args.bits)
    private_b64 = encode_key_material(private_pem)
    public_b64 = encode_key_material(public_pem)

    public_keys: dict[str, str] = (
        json.loads(args.merge_public_keys) if args.merge_public_keys else {}
    )
    public_keys[args.kid] = public_b64

    print("# ============================================================")
    print(f"# RSA-{args.bits} keypair generated, kid={args.kid!r}")
    print("# ============================================================")
    print()
    print("# --- data_gateway ONLY (.env) --- never copy these two lines")
    print("# to interview_core / feedback_billing / admin_ops / space.env:")
    print(f"JWT_PRIVATE_KEY={private_b64}")
    print(f"JWT_ACTIVE_KID={args.kid}")
    print()
    print("# --- ALL FOUR services (+ the LiveKit worker) — identical value: ---")
    print(f"JWT_PUBLIC_KEYS={json.dumps(public_keys)}")
    print()
    print("# --- ALL FIVE processes, rollout step 1 (add RS256 alongside HS256): ---")
    print("JWT_VERIFY_ALGORITHMS=HS256,RS256")
    print()
    print("# --- data_gateway ONLY, rollout step 2 (cut signing over), AFTER step 1")
    print("# has been deployed everywhere and is live: ---")
    print("JWT_SIGNING_ALGORITHM=RS256")
    print()
    print(
        "# See shared/auth/jwt.py's module docstring for the full 4-step "
        "rollout sequence (why the order above matters) and "
        "docs/ACCEPTED-RISKS.md AR-2 for the risk this closes.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
