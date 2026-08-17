import os
import hmac
import hashlib
import time
import requests
import re
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)  # Cho phép mọi domain gọi API, có thể hạn chế sau

# ========== CẤU HÌNH TỪ BIẾN MÔI TRƯỜNG (set trên Render) ==========
APP_KEY = os.getenv("LAZADA_APP_KEY")
APP_SECRET = os.getenv("LAZADA_APP_SECRET")
ACCESS_TOKEN = os.getenv("LAZADA_ACCESS_TOKEN")
BASE_URL = os.getenv("LAZADA_BASE_URL", "https://api.lazada.vn/rest")  # mặc định VN

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

# ========== LẤY PRODUCT ID TỪ URL ==========
def extract_product_id(url: str) -> str:
    # Nếu là link rút gọn, follow redirect
    if 's.lazada' in url or 's.lz' in url:
        try:
            r = requests.get(url, allow_redirects=True, timeout=10)
            url = r.url
        except:
            pass

    # Thử regex dạng -i{id}-s
    match = re.search(r'-i(\d+)-s', url)
    if match:
        return match.group(1)

    # Thử query params
    match = re.search(r'[?&](?:id|productId)=(\d+)', url)
    if match:
        return match.group(1)

    raise ValueError("Không tìm thấy productId trong link")

# ========== API ENDPOINT ==========
@app.route('/get-product', methods=['GET'])
def get_product():
    input_url = request.args.get('url')
    if not input_url:
        return jsonify({'error': 'Thiếu tham số url'}), 400

    try:
        product_id = extract_product_id(input_url)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    # Gọi Lazada product feed
    feed_result = call_lazada_api('/marketing/product/feed', {
        'offerType': 1,          # 1 = thường, 2 = MM, 3 = DM
        'productIds': f"[{product_id}]",
        'limit': 10,
        'page': 1
    })

    if feed_result.get('code') != '0':
        return jsonify({
            'error': 'Lỗi từ Lazada API',
            'detail': feed_result.get('message', feed_result)
        }), 500

    products = feed_result.get('data', {}).get('products', [])
    if not products:
        return jsonify({'error': 'Không tìm thấy sản phẩm hoặc sản phẩm không thuộc chương trình affiliate'}), 404

    product = products[0]

    # Trích xuất thông tin cần thiết
    # Lưu ý: tên trường có thể thay đổi, bạn có thể in thử response để điều chỉnh
    result = {
        'title': product.get('productName', ''),
        'image': product.get('image', ''),
        'price': product.get('salePrice', product.get('price', '')),
        'original_price': product.get('price', ''),
        'commission_rate': product.get('commissionRate', ''),
        'product_id': product.get('productId', product_id)
    }

    # (Tùy chọn) Tạo link affiliate
    link_result = call_lazada_api('/marketing/product/link', {'productId': product_id})
    if link_result.get('code') == '0':
        link_data = link_result.get('data', {})
        result['affiliate_link'] = link_data.get('trackingLink', '')
        # Có thể thêm thông tin hoa hồng từ link API nếu cần

    return jsonify(result)

# ========== HEALTH CHECK ==========
@app.route('/', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'message': 'Lazada Affiliate API is running'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
