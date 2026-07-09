"""测试图片识别 - 发送一个小测试图片到后端"""
import json
import urllib.request
import base64

# 创建一个 1x1 红色像素的 PNG
import struct
import zlib

def create_tiny_png():
    """生成一个 2x2 红色 PNG 图片的 base64 编码"""
    # PNG 签名
    signature = b'\x89PNG\r\n\x1a\n'
    
    # IHDR chunk (2x2, 8-bit RGB)
    ihdr_data = struct.pack('>IIBBBBB', 2, 2, 8, 2, 0, 0, 0)
    ihdr_crc = zlib.crc32(b'IHDR' + ihdr_data)
    ihdr = struct.pack('>I', 13) + b'IHDR' + ihdr_data + struct.pack('>I', ihdr_crc & 0xffffffff)
    
    # IDAT chunk (image data: 2 rows, each: filter byte + 2 pixels * 3 bytes)
    raw_data = b'\x00\xff\x00\x00\xff\x00\x00'  # row 0: filter=none, 2 red pixels
    raw_data += b'\x00\xff\x00\x00\xff\x00\x00'  # row 1
    compressed = zlib.compress(raw_data)
    idat_crc = zlib.crc32(b'IDAT' + compressed)
    idat = struct.pack('>I', len(compressed)) + b'IDAT' + compressed + struct.pack('>I', idat_crc & 0xffffffff)
    
    # IEND chunk
    iend_crc = zlib.crc32(b'IEND')
    iend = struct.pack('>I', 0) + b'IEND' + struct.pack('>I', iend_crc & 0xffffffff)
    
    png = signature + ihdr + idat + iend
    return base64.b64encode(png).decode()

BASE = "http://127.0.0.1:8000"

# 1. 注册/登录获取 token
try:
    req = urllib.request.Request(
        f"{BASE}/api/auth/register",
        data=json.dumps({"username": "img_test_user", "nickname": "ImgTest"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    r = urllib.request.urlopen(req)
    token = json.loads(r.read())["token"]
    print(f"注册成功")
except:
    req = urllib.request.Request(
        f"{BASE}/api/auth/login",
        data=json.dumps({"username": "img_test_user"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    r = urllib.request.urlopen(req)
    token = json.loads(r.read())["token"]
    print(f"登录成功")

# 2. 发送带图片的流式对话
image_b64 = create_tiny_png()
print(f"图片 base64 长度: {len(image_b64)}")
print(f"图片 data URL: data:image/png;base64,{image_b64[:50]}...")

# 用非流式接口测试（更容易看结果）
req = urllib.request.Request(
    f"{BASE}/api/chat",
    data=json.dumps({
        "message": "请描述这张图片的内容",
        "model_name": "qwen-plus",
        "image_data": f"data:image/png;base64,{image_b64}",
    }).encode(),
    headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    },
    method="POST"
)

print("\n发送图片识别请求...")
try:
    r = urllib.request.urlopen(req, timeout=30)
    result = json.loads(r.read())
    print(f"\n响应:")
    print(f"  session_id: {result.get('session_id')}")
    print(f"  model: {result.get('model')}")
    response_text = result.get('response', '')
    print(f"  response ({len(response_text)} chars): {response_text[:200]}")
    print(f"  usage: {result.get('usage')}")
except urllib.error.HTTPError as e:
    print(f"HTTP Error {e.code}: {e.read().decode()}")
except Exception as e:
    print(f"Error: {e}")
