from flask import Flask

app = Flask(__name__)

from app import routes # type: ignore

@app.route('/')
@app.route('/index')
def index():
    return "Hello, World!"