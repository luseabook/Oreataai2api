"""
Banti jt token entrypoint.

The live Oreate web flow obtains a server token through the Banti SDK `/dr`
request, then wraps it into the final `31$...` jt payload.  The pure-Python
encoder below is still useful for fixed-input tests and emergency fallback,
but generation traffic should prefer the local Node helper in
`banti_jt_helper.js` because it reproduces the current SDK transport wrapper.
"""
import json
import os
from pathlib import Path
import subprocess
import time
import random
import base64


def f7():
    """Timestamp in ms"""
    return int(time.time() * 1000)


def u1(s):
    """
    Character-shifting obfuscation (from Banti SDK)
    For chars 0x29-0x7a: shift by position % 32 with wrap
    """
    result = ''
    for i, ch in enumerate(s):
        code = ord(ch)
        shift = i % 32
        if 0x29 <= code <= 0x7a:
            if code + shift > 0x7a:
                result += chr(0x28 + code - 0x7a + shift)
            else:
                result += chr(code + shift)
        else:
            result += ch
    return result


def x0(s):
    """Base64 encode (matches btoa)"""
    return base64.b64encode(s.encode()).decode()


def A0(data_dict, salt="31$"):
    """Generate jt token from data dictionary"""
    payload = json.dumps(data_dict, separators=(',', ':'))
    return salt + x0(u1(payload))


def generate_banti_artifacts_from_helper(timeout_sec=10):
    helper = Path(__file__).resolve().parent / "banti_jt_helper.js"
    if not helper.exists():
        raise RuntimeError("banti_jt_helper.js is missing")
    raw = subprocess.check_output(
        ["node", str(helper)],
        cwd=str(helper.parent),
        text=True,
        timeout=timeout_sec,
        stderr=subprocess.DEVNULL,
    )
    body = json.loads(raw)
    token = body.get("jt")
    if not isinstance(token, str) or not token.startswith("31$"):
        raise RuntimeError("banti helper returned invalid jt")
    cookies = body.get("cookies")
    if not isinstance(cookies, dict):
        cookies = {}
    return {"jt": token, "cookies": cookies, "version": body.get("version")}


def generate_jt_token_from_helper(timeout_sec=10):
    return generate_banti_artifacts_from_helper(timeout_sec)["jt"]


def generate_banti_artifacts(timeout_sec=10, prefer_helper=True):
    if prefer_helper and os.environ.get("OREATE_DISABLE_BANTI_HELPER") != "1":
        try:
            return generate_banti_artifacts_from_helper(timeout_sec)
        except Exception:
            pass
    token = generate_jt_token(prefer_helper=False)
    return {"jt": token, "cookies": {}, "version": None}


def generate_jt_token(error_msg="", data_key="", timeout=200, prefer_helper=True):
    """
    Generate a banti jt token.

    The preferred path calls the current local SDK helper and yields a token
    with a non-empty server-issued `j` field. Set environment variable
    OREATE_DISABLE_BANTI_HELPER=1 to force the deterministic Python fallback.

    From the A3 function:
    A0({'i':'0', 'tn':ts, 'tj':ts, 'tp':data_key, 'to':timeout, 'v':version, 'j':token_value}, '31$')

    From the Am function (fallback):
    A0 with error message in 'j' field
    """
    if prefer_helper and os.environ.get("OREATE_DISABLE_BANTI_HELPER") != "1":
        try:
            return generate_jt_token_from_helper()
        except Exception:
            pass

    ts = str(f7())
    version = "1.14.3.1"
    
    data = {
        'i': '0',
        'tn': ts,
        'tj': ts,
        'tp': str(data_key) if data_key else '',
        'to': str(timeout),
        'v': version,
        'j': str(error_msg) if error_msg else ''
    }
    
    return A0(data)


def generate_jt_with_server_response(server_token="", data_key="", timeout=5000):
    """
    Generate jt token using server response.
    If server_token is provided (from /dr endpoint), use it as 'j' field.
    Otherwise use empty string.
    """
    ts = str(f7())
    version = "1.14.3.1"
    
    data = {
        'i': '0',
        'tn': ts,
        'tj': ts,
        'tp': str(data_key) if data_key else '',
        'to': str(timeout),
        'v': version,
        'j': str(server_token) if server_token else ''
    }
    
    return A0(data)


def test():
    """Test token generation and verify format"""
    
    # Test u1 function
    test_str = '{"i":"0","tn":"12345","tj":"12345","tp":"","to":"5000","v":"10617531","j":""}'
    encoded = u1(test_str)
    print(f"Original: {test_str[:60]}...")
    print(f"Encoded:  {encoded[:60]}...")
    
    # Test A0
    token = A0({'i':'0','tn':'12345','tj':'12345','tp':'','to':'5000','v':'10617531','j':''})
    print(f"\nToken: {token}")
    print(f"Starts with 31$: {token.startswith('31$')}")
    
    # Generate multiple tokens (should differ each time)
    print("\n=== Generated Tokens ===")
    for _ in range(3):
        t = generate_jt_token()
        print(f"  {t}")
    
    # Generate with empty fields (like minimal client)
    print("\n=== Minimal Token (empty j) ===")
    t = generate_jt_token()
    print(f"  Length: {len(t)}")
    print(f"  Token: {t[:80]}...")
    
    # Test what the browser might expect
    # The /dr endpoint might need the 'd' field to be properly formatted
    # Let's also check what format the full payload should be
    
    print("\n=== Complete Flow Test ===")
    payload = {
        'subid': '',
        'ts': f"{f7()}_{random.randint(0, 10**10)}",
        'r': format(random.randint(0, 16**6), '06x'),
        'v': '1.0',
        'd': ''  # Fingerprint blob
    }
    
    token = generate_jt_token()
    print(f"Payload: {json.dumps(payload, indent=2)}")
    print(f"jt token: {token}")


if __name__ == '__main__':
    test()
