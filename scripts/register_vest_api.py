#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import json
import secrets
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import requests
    from eth_account import Account
    from eth_account.messages import encode_typed_data
except ModuleNotFoundError as exc:  # pragma: no cover - runtime UX path
    missing = exc.name or "dependency"
    print(
        "Missing dependency: "
        f"{missing}.\n"
        "Run this script with uv so temporary dependencies are installed automatically:\n"
        "  uv run --with eth-account --with requests python scripts/register_vest_api.py --help",
        file=sys.stderr,
    )
    raise SystemExit(2) from exc

PRODUCTION_CONTRACT = "0x919386306C47b2Fe1036e3B4F7C40D22D2461a23"
DEVELOPMENT_CONTRACT = "0x8E4D87AEf4AC4D5415C35A12319013e34223825B"
PRODUCTION_BASE_URL = "https://server-prod.hz.vestmarkets.com/v2"
DEVELOPMENT_BASE_URL = "https://server-dev.hz.vestmarkets.com/v2"
NETWORK_TYPE_PRODUCTION = 0
NETWORK_TYPE_DEVELOPMENT = 1
DEFAULT_EXPIRY_DAYS = 7


@dataclass(frozen=True)
class VestNetwork:
    name: str
    base_url: str
    verifying_contract: str
    network_type: int


NETWORKS = {
    "prod": VestNetwork(
        name="prod",
        base_url=PRODUCTION_BASE_URL,
        verifying_contract=PRODUCTION_CONTRACT,
        network_type=NETWORK_TYPE_PRODUCTION,
    ),
    "dev": VestNetwork(
        name="dev",
        base_url=DEVELOPMENT_BASE_URL,
        verifying_contract=DEVELOPMENT_CONTRACT,
        network_type=NETWORK_TYPE_DEVELOPMENT,
    ),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Register a Vest API key locally. The script creates a signing wallet, "
            "signs the SignerProof with your primary wallet, calls POST /register, "
            "and prints apiKey + accGroup."
        )
    )
    parser.add_argument("--network", choices=sorted(NETWORKS), default="prod", help="Vest environment")
    parser.add_argument(
        "--primary-private-key",
        help="Primary wallet private key in 0x... form. If omitted, the script prompts securely.",
    )
    parser.add_argument(
        "--signing-private-key",
        help="Optional existing signing private key in 0x... form. If omitted, a fresh one is generated.",
    )
    parser.add_argument(
        "--expiry-days",
        type=int,
        default=DEFAULT_EXPIRY_DAYS,
        help="How many days the signing delegate should remain valid.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="HTTP timeout in seconds.",
    )
    parser.add_argument(
        "--show-signing-private-key",
        action="store_true",
        help="Print the generated signing private key. Off by default for safety.",
    )
    parser.add_argument(
        "--save-env",
        type=Path,
        help="Optional path to write VEST_API_KEY and VEST_ACCOUNT_GROUP after success.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do everything except the final POST /register request.",
    )
    return parser


def ensure_hex_key(raw: str, *, label: str) -> str:
    value = raw.strip()
    if not value:
        raise ValueError(f"{label} is empty")
    if not value.startswith("0x"):
        value = "0x" + value
    if len(value) != 66:
        raise ValueError(f"{label} must be a 32-byte hex private key")
    int(value[2:], 16)
    return value


def prompt_secret(prompt: str) -> str:
    secret = getpass.getpass(prompt)
    if not secret:
        raise ValueError("Input was empty")
    return secret


def make_signing_key(explicit_key: str | None) -> tuple[str, str]:
    private_key = ensure_hex_key(explicit_key, label="signing private key") if explicit_key else "0x" + secrets.token_hex(32)
    account = Account.from_key(private_key)
    return private_key, account.address.lower()


def build_register_payload(primary_private_key: str, signing_addr: str, network: VestNetwork, expiry_days: int) -> dict[str, Any]:
    primary_private_key = ensure_hex_key(primary_private_key, label="primary private key")
    primary_account = Account.from_key(primary_private_key)
    primary_addr = primary_account.address.lower()
    expiry_time = int(time.time() * 1000) + max(expiry_days, 1) * 24 * 60 * 60 * 1000

    domain_data = {
        "name": "VestRouterV2",
        "version": "0.0.1",
        "verifyingContract": network.verifying_contract,
    }
    message_types = {
        "SignerProof": [
            {"name": "approvedSigner", "type": "address"},
            {"name": "signerExpiry", "type": "uint256"},
        ]
    }
    message_data = {
        "approvedSigner": signing_addr,
        "signerExpiry": expiry_time,
    }
    signable = encode_typed_data(domain_data, message_types, message_data)
    signature = Account.sign_message(signable, private_key=primary_private_key).signature.hex()

    return {
        "signingAddr": signing_addr,
        "primaryAddr": primary_addr,
        "signature": signature,
        "expiryTime": expiry_time,
        "networkType": network.network_type,
    }


def register_vest(payload: dict[str, Any], network: VestNetwork, timeout: float) -> dict[str, Any]:
    response = requests.post(
        f"{network.base_url}/register",
        json=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        timeout=timeout,
    )
    try:
        body = response.json()
    except Exception:
        body = {"raw": response.text}
    if response.status_code >= 400:
        raise RuntimeError(f"Vest register failed: HTTP {response.status_code}: {json.dumps(body, ensure_ascii=False)}")
    if not isinstance(body, dict) or "apiKey" not in body:
        raise RuntimeError(f"Unexpected Vest register response: {json.dumps(body, ensure_ascii=False)}")
    return body


def save_env_file(path: Path, api_key: str, account_group: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        f'VEST_API_KEY="{api_key}"\n'
        f'VEST_ACCOUNT_GROUP="{account_group}"\n'
    )
    path.write_text(content, encoding="utf-8")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    network = NETWORKS[args.network]

    try:
        primary_private_key = args.primary_private_key or prompt_secret("Enter PRIMARY wallet private key (input hidden): ")
        primary_private_key = ensure_hex_key(primary_private_key, label="primary private key")
        signing_private_key, signing_addr = make_signing_key(args.signing_private_key)
        payload = build_register_payload(primary_private_key, signing_addr, network, args.expiry_days)
    except Exception as exc:
        print(f"Input error: {exc}", file=sys.stderr)
        return 2

    primary_addr = Account.from_key(primary_private_key).address.lower()
    print("Vest registration payload prepared locally.")
    print(f"Network: {network.name}")
    print(f"Base URL: {network.base_url}")
    print(f"Primary address: {primary_addr}")
    print(f"Signing address: {signing_addr}")
    print(f"Expiry time (ms): {payload['expiryTime']}")
    if args.show_signing_private_key:
        print(f"Signing private key: {signing_private_key}")
    else:
        print("Signing private key: [hidden; use --show-signing-private-key if you want to print it]")

    if args.dry_run:
        print("Dry run enabled; POST /register was not sent.")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    try:
        result = register_vest(payload, network, args.timeout)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    api_key = str(result["apiKey"])
    account_group = result.get("accGroup")

    print("\nVest API key registered successfully.")
    print(f"apiKey: {api_key}")
    print(f"accGroup: {account_group}")
    print("\nUse these in delta-neutral-monitor:")
    print(f'VEST_API_KEY="{api_key}"')
    print(f'VEST_ACCOUNT_GROUP="{account_group}"')

    if args.save_env:
        save_env_file(args.save_env, api_key, account_group)
        print(f"\nSaved env file: {args.save_env}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
