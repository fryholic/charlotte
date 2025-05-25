from flask import Flask, render_template_string
import os
import json
from datetime import datetime
from collections import deque
import threading

app = Flask(__name__)

# 최대 1000개의 로그 메시지를 저장
MAX_LOGS = 1000
logs = deque(maxlen=MAX_LOGS)

# HTML 템플릿
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>Charlotte Bot Logs</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #1e1e1e;
            color: #ffffff;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
        }
        .header {
            margin-bottom: 20px;
            padding: 10px;
            background-color: #2d2d2d;
            border-radius: 5px;
        }
        .log-container {
            background-color: #2d2d2d;
            padding: 20px;
            border-radius: 5px;
            white-space: pre-wrap;
        }
        .log-entry {
            margin: 5px 0;
            padding: 5px;
            border-bottom: 1px solid #3d3d3d;
        }
        .log-time {
            color: #888;
            margin-right: 10px;
        }
        .refresh-button {
            background-color: #4CAF50;
            border: none;
            color: white;
            padding: 10px 20px;
            text-align: center;
            text-decoration: none;
            display: inline-block;
            font-size: 16px;
            margin: 4px 2px;
            cursor: pointer;
            border-radius: 4px;
        }
        .clear-button {
            background-color: #f44336;
            border: none;
            color: white;
            padding: 10px 20px;
            text-align: center;
            text-decoration: none;
            display: inline-block;
            font-size: 16px;
            margin: 4px 2px;
            cursor: pointer;
            border-radius: 4px;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Charlotte Bot Logs</h1>
            <button class="refresh-button" onclick="location.reload()">새로고침</button>
            <button class="clear-button" onclick="clearLogs()">로그 지우기</button>
        </div>
        <div class="log-container">
            {% for log in logs %}
            <div class="log-entry">
                <span class="log-time">{{ log.time }}</span>
                <span class="log-message">{{ log.message }}</span>
            </div>
            {% endfor %}
        </div>
    </div>
    <script>
        function clearLogs() {
            fetch('/clear', {method: 'POST'})
                .then(response => {
                    if(response.ok) {
                        location.reload();
                    }
                });
        }
    </script>
</body>
</html>
'''

def add_log(message):
    """로그 메시지 추가"""
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logs.append({"time": current_time, "message": message})

@app.route('/')
def home():
    """메인 페이지"""
    return render_template_string(HTML_TEMPLATE, logs=list(logs))

@app.route('/clear', methods=['POST'])
def clear_logs():
    """로그 지우기"""
    logs.clear()
    return 'OK', 200

@app.route('/add/<message>')
def add_log_route(message):
    """로그 추가 API"""
    add_log(message)
    return 'OK', 200

def run_server():
    """서버 실행"""
    app.run(host='0.0.0.0', port=5000)

if __name__ == '__main__':
    run_server()
