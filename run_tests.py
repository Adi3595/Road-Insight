#!/usr/bin/env python3
"""
Test runner for Road Recorder.
Discovers and runs test cases across the tests/ directory.
Includes core recorder tests and FastAPI integration tests.
"""

import sys
import unittest
from pathlib import Path

# Add project root to sys.path
ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))


def run_all_tests():
    # Import core test modules
    from tests import test_config, test_storage_manager, test_segment_manager, test_recording_index, test_recorder_lifecycle

    test_modules = [
        test_config,
        test_storage_manager,
        test_segment_manager,
        test_recording_index,
        test_recorder_lifecycle
    ]

    # Try importing API test module if FastAPI is available
    try:
        from tests import test_api
        test_modules.append(test_api)
    except ImportError as e:
        print(f"[NOTICE] FastAPI test suite skipped (dependencies not present in current python env: {e})")

    total_passed = 0
    total_failed = 0
    errors = []

    print("=" * 68)
    print("        ROAD RECORDER - AUTOMATED COMPREHENSIVE TEST SUITE")
    print("=" * 68)

    for mod in test_modules:
        mod_name = mod.__name__
        print(f"\nRunning tests in {mod_name}...")
        for attr_name in dir(mod):
            if attr_name.startswith("test_") and callable(getattr(mod, attr_name)):
                func = getattr(mod, attr_name)
                try:
                    import tempfile
                    with tempfile.TemporaryDirectory() as tmp_dir:
                        tmp_path = Path(tmp_dir)
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

    print("\n" + "=" * 68)
    print(f"TEST SUMMARY: {total_passed} Passed, {total_failed} Failed")
    print("=" * 68)

    if total_failed > 0:
        print("\nFailures:")
        for m, a, err in errors:
            print(f" - {m}.{a}: {err}")
        sys.exit(1)
    else:
        print("ALL AUTOMATED TESTS PASSED SUCCESSFULLY!")
        sys.exit(0)


if __name__ == "__main__":
    run_all_tests()
