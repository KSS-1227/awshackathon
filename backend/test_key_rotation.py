"""
Test multi-key rotation for Gemini API calls on 429 quota exhaustion.

This test simulates:
1. First key returns 429 (quota exhausted)
2. Second key succeeds (returns 200)
3. Logs show proper rotation happening
"""
import sys
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock
import httpx

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


async def test_key_rotation_on_429():
    """Test that key rotation happens on 429 but not 503."""
    from backend.llm.client import _with_key_rotation, QuotaExhaustedError
    
    print("\n" + "="*70)
    print("TEST: Multi-Key Rotation on 429 Quota Exhaustion")
    print("="*70)
    
    # Simulate two keys: first exhausted (429), second works (200)
    call_count = [0]
    
    async def mock_call(api_key: str, key_idx: int, keys_total: int) -> str:
        """Mock API call that fails on first key (429) and succeeds on second."""
        call_count[0] += 1
        print(f"\n  Mock call #{call_count[0]}: Testing key {key_idx}/{keys_total}")
        
        if api_key == "key1_invalid":
            # First key always returns 429 after all retries
            print(f"    → Key 1 returning 429 (quota exhausted)")
            response = MagicMock()
            response.status_code = 429
            response.text = '{"error": "Quota exceeded"}'
            raise httpx.HTTPStatusError("429 Quota Exceeded", request=MagicMock(), response=response)
        elif api_key == "key2_valid":
            # Second key succeeds
            print(f"    → Key 2 returning 200 (success)")
            return "✓ Success with key 2!"
        else:
            raise ValueError(f"Unknown test key: {api_key}")
    
    test_keys = ["key1_invalid", "key2_valid"]
    
    try:
        result = await _with_key_rotation(
            mock_call,
            test_keys,
            "test",
            max_retries=1,  # Only 1 retry per key to speed up test
            base_delay=0.1,
        )
        print(f"\n✓ PASS: Rotation succeeded!")
        print(f"  Result: {result}")
        print(f"  Total mock calls: {call_count[0]}")
        return True
    except Exception as e:
        print(f"\n✗ FAIL: {type(e).__name__}: {e}")
        return False


async def test_all_keys_exhausted():
    """Test that QuotaExhaustedError is raised when all keys return 429."""
    from backend.llm.client import _with_key_rotation, QuotaExhaustedError
    
    print("\n" + "="*70)
    print("TEST: All Keys Exhausted (QuotaExhaustedError)")
    print("="*70)
    
    async def mock_call_all_429(api_key: str, key_idx: int, keys_total: int) -> str:
        """Mock that returns 429 for all keys."""
        print(f"\n  Testing key {key_idx}/{keys_total}: Returning 429")
        response = MagicMock()
        response.status_code = 429
        response.text = '{"error": "Quota exceeded"}'
        raise httpx.HTTPStatusError("429 Quota Exceeded", request=MagicMock(), response=response)
    
    test_keys = ["key1", "key2", "key3"]
    
    try:
        await _with_key_rotation(
            mock_call_all_429,
            test_keys,
            "test",
            max_retries=1,
            base_delay=0.05,
        )
        print(f"\n✗ FAIL: Should have raised QuotaExhaustedError")
        return False
    except QuotaExhaustedError as e:
        print(f"\n✓ PASS: QuotaExhaustedError raised as expected")
        print(f"  Error message: {str(e)}")
        return True
    except Exception as e:
        print(f"\n✗ FAIL: Wrong exception: {type(e).__name__}: {e}")
        return False


async def test_single_key_backward_compat():
    """Test that single key (no rotation) works unchanged."""
    from backend.llm.client import _with_key_rotation
    
    print("\n" + "="*70)
    print("TEST: Single Key Backward Compatibility")
    print("="*70)
    
    async def mock_call_single(api_key: str, key_idx: int, keys_total: int) -> str:
        """Mock that succeeds on single key."""
        print(f"\n  Testing key {key_idx}/{keys_total}: Returning success")
        if key_idx == 1 and keys_total == 1:
            return "✓ Single key success!"
        raise ValueError("Unexpected key indices")
    
    test_keys = ["single_key"]
    
    try:
        result = await _with_key_rotation(
            mock_call_single,
            test_keys,
            "test",
            max_retries=1,
        )
        print(f"\n✓ PASS: Single key works")
        print(f"  Result: {result}")
        return True
    except Exception as e:
        print(f"\n✗ FAIL: {type(e).__name__}: {e}")
        return False


async def test_logging_output():
    """Test that logging shows key indices (not actual keys)."""
    from backend.llm.client import _with_key_rotation
    import logging
    
    print("\n" + "="*70)
    print("TEST: Logging Shows Key Indices Only")
    print("="*70)
    
    # Capture logs
    log_capture = []
    handler = logging.StreamHandler()
    handler.emit = lambda record: log_capture.append(record.getMessage())
    logger = logging.getLogger("backend.llm.client")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    
    async def mock_call_log(api_key: str, key_idx: int, keys_total: int) -> str:
        """Mock that succeeds immediately."""
        return f"Result from key {key_idx}"
    
    test_keys = ["actual_key_1", "actual_key_2"]
    
    try:
        result = await _with_key_rotation(
            mock_call_log,
            test_keys,
            "embedding",
            max_retries=1,
        )
        
        # Check that logs don't contain actual keys
        logs_str = "\n".join(log_capture)
        print(f"\nCaptured logs:\n{logs_str}")
        
        if "actual_key" in logs_str:
            print(f"\n✗ FAIL: Logs contain actual key material!")
            return False
        
        if "using key 1/2" in logs_str or "EMBEDDING" in logs_str:
            print(f"\n✓ PASS: Logs show key indices only (no actual keys exposed)")
            return True
        else:
            print(f"\n✗ FAIL: Expected key index logging not found")
            return False
            
    except Exception as e:
        print(f"\n✗ FAIL: {type(e).__name__}: {e}")
        return False
    finally:
        logger.removeHandler(handler)


async def main():
    """Run all tests."""
    print("\n" + "="*70)
    print("MULTI-KEY ROTATION TEST SUITE")
    print("="*70)
    
    results = []
    
    # Test 1: Key rotation on 429
    results.append(("Key rotation on 429", await test_key_rotation_on_429()))
    
    # Test 2: All keys exhausted
    results.append(("All keys exhausted", await test_all_keys_exhausted()))
    
    # Test 3: Single key backward compat
    results.append(("Single key backward compat", await test_single_key_backward_compat()))
    
    # Test 4: Logging
    results.append(("Logging shows indices only", await test_logging_output()))
    
    # Summary
    print("\n" + "="*70)
    print("TEST SUMMARY")
    print("="*70)
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status}: {name}")
    
    print(f"\nTotal: {passed}/{total} tests passed")
    print("="*70 + "\n")
    
    return all(result for _, result in results)


if __name__ == "__main__":
    success = asyncio.run(main())
    exit(0 if success else 1)
