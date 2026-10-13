#!/usr/bin/env python3
"""
API Key Management and Rotation Tool for Road Recorder.
Generates cryptographically random keys, saves to protected USB storage,
and outputs secure integration tokens for consuming teams.
"""

import sys
import os
import argparse
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from road_recorder.config import load_config
from road_recorder.api.auth import get_configured_api_key, generate_and_save_api_key


def main():
    parser = argparse.ArgumentParser(description="Road Recorder API Key Management Utility")
    parser.add_argument("--generate", action="store_true", help="Generate a new API key if one does not exist")
    parser.add_argument("--rotate", action="store_true", help="Rotate and replace the existing API key immediately")
    parser.add_argument("--show", action="store_true", help="Display the currently configured API key")
    args = parser.parse_args()

    config = load_config("config.yaml")
    key_file = config.api_key_file

    if args.rotate:
        new_key = generate_and_save_api_key(key_file)
        print("=" * 65)
        print("[SUCCESS] API Key rotated successfully!")
        print(f"Key saved to: {key_file}")
        print(f"New API Key : {new_key}")
        print("=" * 65)
        print("Share this key securely with the consumer team. Example header:")
        print(f"X-API-Key: {new_key}")
        return

    if args.show:
        current_key = get_configured_api_key(config)
        if current_key:
            print("=" * 65)
            print("CURRENT ROAD RECORDER API KEY:")
            print(f"Key: {current_key}")
            print(f"Source file: {key_file}")
            print("=" * 65)
        else:
            print("[INFO] No API key is currently configured.")
            print("Run with --generate to create one.")
        return

    # Default action or --generate
    existing = get_configured_api_key(config)
    if existing and not args.rotate:
        print(f"[INFO] API key already exists in {key_file}.")
        print("Use --show to display it, or --rotate to generate a new key.")
    else:
        new_key = generate_and_save_api_key(key_file)
        print("=" * 65)
        print("[SUCCESS] New API Key generated successfully!")
        print(f"File location: {key_file}")
        print(f"API Key      : {new_key}")
        print("=" * 65)
        print("Set in environment (optional):")
        print(f"export {config.api_key_env_var}=\"{new_key}\"")


if __name__ == "__main__":
    main()
