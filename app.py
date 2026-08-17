import os
import hmac
import hashlib
import time
import requests
import re
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ========== CẤU HÌNH TỪ BIẾN MÔI TRƯỜNG ==========
APP_KEY = os.getenv("LAZADA_APP_KEY")
APP_SECRET = os.getenv("LAZADA_APP_SECRET")
ACCESS_TOKEN = os.getenv("LAZADA_ACCESS_TOKEN")
BASE_URL = os.getenv("LAZADA_BASE_URL", "https://api.lazada.vn/rest")

if not APP_KEY or not APP_SECRET or not ACCESS_TOKEN:
    raise RuntimeError("Missing Lazada API credentials. Set LAZADA_APP_KEY, LAZADA_APP_SECRET, LAZADA_ACCESS_TOKEN environment variables.")

# ========== HÀM TẠO CHỮ KÝ ==========
def generate_sign(params: dict) -> str:
    sorted_keys = sorted(params.keys())
    string_to_sign = ""
    for key in sorted_keys:
        string_to_sign += key + str(params[key])
    string_to_sign = APP_SECRET + string_to_sign + APP_SECRET
    sign = hmac.new(
        APP_SECRET.encode('utf-8'),
        string_to_sign.encode('utf-8'),
        hashlib.sha256
    ).hexdigest().upper()
    return sign

# ========== GỌI LAZADA API ==========
def call_lazada_api(api_path: str, extra_params: dict) -> dict:
    params = {
        'app_key': APP_KEY,
        'timestamp': str(int(time.time() * 1000)),
        'sign_method': 'sha256',
        'access_token': ACCESS_TOKEN,
    }
    params.update(extra_params)
    params['sign'] = generate_sign(params)

    url = BASE_URL + api_path
    try:
        response = requests.get(url, params=params, timeout=15)
        return response.json()
    except Exception as e:
        return {'code': 'EXCEPTION', 'message': str(e)}

# ========== LẤY PRODUCT ID TỪ URL (CẢI TIẾN) ==========
def extract_product_id(url: str) -> str:
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7',
        'Connection': 'keep-alive',
    }

    # Thử các regex tìm productId trực tiếp trong URL gốc
    match = re.search(r'-i(\d+)-s', url)
    if match:
        return match.group(1)

    match = re.search(r'[?&](?:id|productId)=(\d+)', url)
    if match:
        return match.group(1)

    # Nếu là link rút gọn, cần xử lý
    try:
        # Gửi request không theo redirect để xem header Location
        resp = requests.get(url, headers=headers, allow_redirects=False, timeout=10)
        if resp.status_code in [301, 302, 303, 307, 308]:
            redirect_url = resp.headers.get('Location')
            if redirect_url:
                # Kiểm tra redirect_url có chứa productId không
                match = re.search(r'-i(\d+)-s', redirect_url)
                if match:
                    return match.group(1)
                # Nếu không, thử follow tiếp
                return extract_product_id(redirect_url)
        elif resp.status_code == 200:
            # Có thể là trang chứa JavaScript redirect
            content = resp.text
            # Tìm productId trực tiếp trong HTML
            match = re.search(r'"productId"\s*:\s*"?(\d+)"?', content)
            if match:
                return match.group(1)
            match = re.search(r'data-product-id="(\d+)"', content)
            if match:
                return match.group(1)
            # Tìm trong các script redirect
            redirect_patterns = [
                r'window\.location\s*=\s*["\'](.*?)["\']',
                r'window\.location\.replace\(["\'](.*?)["\']\)',
                r'window\.location\.href\s*=\s*["\'](.*?)["\']',
                r'http-equiv="refresh"\s+content="\d+;\s*url=(.*?)"',
                r'<a\s+href=["\'](.*?)["\']\s*>',
            ]
            for pattern in redirect_patterns:
                matches = re.findall(pattern, content, re.IGNORECASE)
                for found_url in matches:
                    # Nếu found_url chứa productId
                    if re.search(r'-i(\d+)-s', found_url):
                        return re.search(r'-i(\d+)-s', found_url).group(1)
                    # Nếu là URL tương đối, thử ghép với domain gốc
                    if found_url.startswith('/'):
                        full_url = requests.compat.urljoin(url, found_url)
                        match = re.search(r'-i(\d+)-s', full_url)
                        if match:
                            return match.group(1)
            # Cuối cùng, thử follow redirect tự động
            resp_follow = requests.get(url, headers=headers, allow_redirects=True, timeout=10)
            final_url = resp_follow.url
            match = re.search(r'-i(\d+)-s', final_url)
            if match:
                return match.group(1)
    except Exception as e:
        print(f"Error extracting from {url}: {e}")

    raise ValueError("Không tìm thấy productId trong link")

# ========== LẤY SẢN PHẨM TỪ FEED (thử nhiều offerType) ==========
def get_product_from_feed(product_id):
    for offer_type in [1, 2, 3]:
        result = call_lazada_api('/marketing/product/feed', {
            'offerType': offer_type,
            'productIds': f"[{product_id}]",
            'limit': 10,
            'page': 1
        })
        if result.get('code') == '0' and result.get('data', {}).get('products'):
            return result
    return None

# ========== API ENDPOINT CHÍNH ==========
@app.route('/get-product', methods=['GET'])
def get_product():
    input_url = request.args.get('url')
    if not input_url:
        return jsonify({'error': 'Thiếu tham số url'}), 400

    try:
        product_id = extract_product_id(input_url)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    feed_result = get_product_from_feed(product_id)
    if not feed_result:
        return jsonify({
            'error': 'Không tìm thấy sản phẩm hoặc sản phẩm không thuộc chương trình affiliate',
            'product_id': product_id  # trả về để debug
        }), 404

    products = feed_result['data']['products']
    product = products[0]

    result = {
        'title': product.get('productName', ''),
        'image': product.get('image', ''),
        'price': product.get('salePrice', product.get('price', '')),
        'original_price': product.get('price', ''),
        'commission_rate': product.get('commissionRate', ''),
        'product_id': product.get('productId', product_id)
    }

    # Tạo link affiliate
    link_result = call_lazada_api('/marketing/product/link', {'productId': product_id})
    if link_result.get('code') == '0':
        link_data = link_result.get('data', {})
        result['affiliate_link'] = link_data.get('trackingLink', '')

    return jsonify(result)

# ========== ENDPOINT DEBUG ==========
@app.route('/debug-url', methods=['GET'])
def debug_url():
    input_url = request.args.get('url')
    if not input_url:
        return jsonify({'error': 'Thiếu tham số url'}), 400
    try:
        pid = extract_product_id(input_url)
        return jsonify({'url': input_url, 'product_id': pid})
    except ValueError as e:
        return jsonify({'url': input_url, 'error': str(e)}), 400

@app.route('/', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
