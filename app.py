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

APP_KEY = os.getenv("LAZADA_APP_KEY")
APP_SECRET = os.getenv("LAZADA_APP_SECRET")
ACCESS_TOKEN = os.getenv("LAZADA_ACCESS_TOKEN")
BASE_URL = os.getenv("LAZADA_BASE_URL", "https://api.lazada.vn/rest")

if not APP_KEY or not APP_SECRET or not ACCESS_TOKEN:
    raise RuntimeError("Missing Lazada API credentials")

def generate_sign(params):
    sorted_keys = sorted(params.keys())
    string_to_sign = ""
    for key in sorted_keys:
        string_to_sign += key + str(params[key])
    string_to_sign = APP_SECRET + string_to_sign + APP_SECRET
    sign = hmac.new(APP_SECRET.encode('utf-8'),
                    string_to_sign.encode('utf-8'),
                    hashlib.sha256).hexdigest().upper()
    return sign

def call_lazada_api(api_path, extra_params):
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

def extract_product_id(url):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    # Xử lý link rút gọn
    if 's.lazada' in url or 's.lz' in url:
        try:
            # Thử bắt header Location
            response = requests.get(url, headers=headers, allow_redirects=False, timeout=10)
            if response.status_code in [301, 302, 303, 307, 308]:
                redirect_url = response.headers.get('Location')
                if redirect_url:
                    url = redirect_url
                else:
                    response = requests.get(url, headers=headers, allow_redirects=True, timeout=10)
                    url = response.url
            else:
                response = requests.get(url, headers=headers, timeout=10)
                content = response.text
                match = re.search(r'"productId"\s*:\s*"?(\d+)"?', content)
                if match:
                    return match.group(1)
                match = re.search(r'data-product-id="(\d+)"', content)
                if match:
                    return match.group(1)
                url = response.url
        except:
            try:
                response = requests.get(url, headers=headers, allow_redirects=True, timeout=10)
                url = response.url
            except:
                pass

    # Tìm productId trong URL
    match = re.search(r'-i(\d+)-s', url)
    if match:
        return match.group(1)

    match = re.search(r'[?&](?:id|productId)=(\d+)', url)
    if match:
        return match.group(1)

    # Tìm trong HTML (fallback)
    try:
        response = requests.get(url, headers=headers, timeout=10)
        content = response.text
        match = re.search(r'"productId"\s*:\s*"?(\d+)"?', content)
        if match:
            return match.group(1)
        match = re.search(r'data-product-id="(\d+)"', content)
        if match:
            return match.group(1)
    except:
        pass

    raise ValueError("Không tìm thấy productId trong link")

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
        return jsonify({'error': 'Không tìm thấy sản phẩm hoặc sản phẩm không thuộc chương trình affiliate'}), 404

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

@app.route('/', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
