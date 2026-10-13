#!/usr/bin/env python3
"""
Zero-dependency test runner using Python standard library unittest.
Discovers and runs test cases across the tests/ directory.
"""

import sys
import unittest
from pathlib import Path

# Add project root to sys.path
ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))


def run_all_tests():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Import test modules
    from tests import test_config, test_storage_manager, test_segment_manager, test_recording_index, test_recorder_lifecycle

    # Convert pytest test functions into unittest cases if needed, or run unittest TestCase
    test_modules = [
        test_config,
        test_storage_manager,
        test_segment_manager,
        test_recording_index,
        test_recorder_lifecycle
    ]

    total_passed = 0
    total_failed = 0
    errors = []

    print("=" * 65)
    print("        ROAD RECORDER - AUTOMATED UNIT TEST SUITE")
    print("=" * 65)

    for mod in test_modules:
        mod_name = mod.__name__
        print(f"\nRunning tests in {mod_name}...")
        for attr_name in dir(mod):
            if attr_name.startswith("test_") and callable(getattr(mod, attr_name)):
                func = getattr(mod, attr_name)
                # Check arguments
                try:
                    import tempfile
                    with tempfile.TemporaryDirectory() as tmp_dir:
                        tmp_path = Path(tmp_dir)
                        # Check func signature
                        import inspect
                        sig = inspect.signature(func)
                        kwargs = {}
                        if "tmp_path" in sig.parameters:
                            kwargs["tmp_path"] = tmp_path
                        if "mock_config" in sig.parameters:
                            from tests.test_recorder_lifecycle import mock_config
                            kwargs["mock_config"] = mock_config(tmp_path)

                        func(**kwargs)
                    print(f"  [PASS] {attr_name}")
                    total_passed += 1
                except Exception as e:
                    print(f"  [FAIL] {attr_name}: {e}")
                    total_failed += 1
                    errors.append((mod_name, attr_name, str(e)))

    print("\n" + "=" * 65)
    print(f"TEST SUMMARY: {total_passed} Passed, {total_failed} Failed")
    print("=" * 65)

    if total_failed > 0:
        print("\nFailures:")
        for m, a, err in errors:
            print(f" - {m}.{a}: {err}")
        sys.exit(1)
    else:
        print("ALL AUTOMATED UNIT TESTS PASSED SUCCESSFULLY!")
        sys.exit(0)


if __name__ == "__main__":
    run_all_tests()
