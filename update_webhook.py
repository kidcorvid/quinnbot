# webhook_listener.py
from flask import Flask, request
import subprocess
import hmac
import hashlib

app = Flask(__name__)
SECRET = "sweetbroisaselfinsert"

@app.route('/update', methods=['POST'])
def handle_webhook():
    # Verify GitHub signature
    signature = request.headers.get('X-Hub-Signature-256')
    if signature:
        expected = 'sha256=' + hmac.new(
            SECRET.encode(),
            request.data,
            hashlib.sha256
        ).hexdigest()
        
        if not hmac.compare_digest(signature, expected):
            return 'Unauthorized', 401
    
    # Trigger update
    subprocess.run(['/home/kidcorvid/quinnbot/update.sh'])
    return 'OK', 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
