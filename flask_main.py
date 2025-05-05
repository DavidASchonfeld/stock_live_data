from flask import Flask

app = Flask(__name__)

# from app import routes # type: ignore

@app.route('/')
@app.route('/index')
def index():
    return "Hello, World!"

@app.route('/hello')
def hello():
    return "Hello!"

# Runs if you call the script directly
# Does not run when you use Gunicorn to run this script
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)