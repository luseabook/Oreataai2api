"""
Banti Token Generator - Pure Python Implementation
Based on reverse-engineered SDK code.
The jt token is computed LOCALLY, not from server.
"""
import json
import time
import random
import hashlib
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


def generate_jt_token(error_msg="", data_key="", timeout=5000):
    """
    Generate a banti jt token.
    
    From the A3 function:
    A0({'i':'0', 'tn':ts, 'tj':ts, 'tp':data_key, 'to':timeout, 'v':version, 'j':token_value}, '31$')
    
    From the Am function (fallback):
    A0 with error message in 'j' field
    """
    ts = str(f7())
    version = str(10617531)  # from $rCkN()
    
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
    version = str(10617531)
    
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