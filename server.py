import datetime
import os
import random
import sqlite3
import time
from datetime import timedelta
from functools import wraps

import flask
from captcha.image import ImageCaptcha
from flask import Flask, g, render_template, redirect, request, session, url_for

import encoder

app = Flask(__name__)

context = ('local.crt', 'local.key')  # certificate and key files

app.secret_key = 't6w9z$C&F)J@NcRf'
app.permanent_session_lifetime = timedelta(minutes=60)
app.SESSION_COOKIE_HTTPONLY = True
app.SESSION_COOKIE_SAMESITE = 'Strict'

DATABASE = 'database.sqlite'


def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)

    def make_dicts(cursor, row):
        return dict((cursor.description[idx][0], value)
                    for idx, value in enumerate(row))

    db.row_factory = make_dicts

    return db


def query_db(query, args=(), one=False):
    cur = get_db().execute(query, args)
    rv = cur.fetchall()
    cur.close()
    return (rv[0] if rv else None) if one else rv


def std_context(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        context = {}
        request.context = context
        if 'userid' in session:
            context['loggedin'] = True
            context['username'] = session['username']
        else:
            context['loggedin'] = False
        return f(*args, **kwargs)

    return wrapper


@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()


@app.route("/")
@std_context
def index():
    posts = query_db(
        'SELECT posts.creator,posts.date,posts.title,posts.content,users.name,users.username,users.USER_PATH_ID FROM '
        'posts JOIN users ON posts.creator=users.userid ORDER BY date DESC LIMIT 10')

    def fix(item):
        item['date'] = datetime.datetime.fromtimestamp(item['date']).strftime('%Y-%m-%d %H:%M')
        item['content'] = '%s...' % (item['content'][:200])
        return item

    context = request.context

    context['posts'] = map(fix, encoder.encode_qry(posts))
    return render_template('index.html', **context)


@app.route("/<uname>/")
@std_context
def users_posts(uname=None):
    cid = query_db('SELECT userid FROM users WHERE username=(?)', (uname,))
    if len(cid) < 1:
        return 'No such user'

    cid = cid[0]['userid']

    if 'userid' in session.keys() and session['userid'] == cid:
        query = 'SELECT date,title,content FROM posts WHERE creator=(?) ORDER BY date DESC'

        context = request.context

        def fix(item):
            item['date'] = datetime.datetime.fromtimestamp(item['date']).strftime('%Y-%m-%d %H:%M')
            return item

        context['posts'] = map(fix, encoder.encode_qry(query_db(query)))
        return render_template('user_posts.html', **context)
    return 'Access Denied'


@app.route("/user_path_id/<user_path_id>/")
@std_context
def users_posts_by_user_path_id(user_path_id=None):
    cid = query_db('SELECT userid FROM users WHERE USER_PATH_ID=%s' % (user_path_id))
    if len(cid) < 1:
        return 'No such user'

    query = 'SELECT date,title,content, USER_PATH_ID FROM POSTS NATURAL JOIN USERS WHERE USER_PATH_ID=%s ORDER BY ' \
            'date DESC' % user_path_id

    context = request.context

    def fix(item):
        item['date'] = datetime.datetime.fromtimestamp(item['date']).strftime('%Y-%m-%d %H:%M')
        return item

    results = query_db(query)
    context['posts'] = map(fix, encoder.encode_qry(results))
    return render_template('user_posts.html', **context)


def generate_captcha_string():
    char_options = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890'
    captcha_string = ''
    for i in range(8):
        captcha_string += char_options[random.randint(1, len(char_options))]
    return captcha_string


@app.route("/captcha-check/", methods=['GET', 'POST'])
@std_context
def captcha_check():
    captcha_input = request.form.get('captcha', '')
    image = ImageCaptcha(width=280, height=90)
    context = request.context
    context['filename'] = str(round(time.time())) + '.png'
    if captcha_input == '':
        session['data'] = generate_captcha_string()
        image.write(session['data'], 'static/' + context['filename'])
        if 'last_captcha_file' in session.keys():
            os.remove('static/' + session['last_captcha_file'])
        session['last_captcha_file'] = context['filename']
        return render_template('captcha.html', **context)
    if captcha_input.lower() == session['data'].lower():
        ip = flask.request.remote_addr
        session.pop(ip)
        return redirect(url_for('login'))
    return render_template('captcha.html', **context)


@app.route("/login/", methods=['GET', 'POST'])
@std_context
def login():
    ip = flask.request.remote_addr
    if ip in session.keys():
        session[ip] = session[ip] + 1
    else:
        session[ip] = 1

    if session[ip] >= 3:
        return redirect(url_for('captcha_check'))

    username = request.form.get('username', '')
    password = request.form.get('password', '')
    context = request.context

    if len(username) < 1 and len(password) < 1:
        return render_template('login.html', **context)

    query = "SELECT userid FROM users WHERE username=(?)"
    account = query_db(query, (username,))
    user_exists = len(account) > 0

    query = "SELECT userid FROM users WHERE username=(?) AND password=(?)"
    account2 = query_db(query, (username, password))
    pass_match = len(account2) > 0

    if user_exists and pass_match:
        session['userid'] = account[0]['userid']
        session['username'] = username
        session['token'] = str(os.urandom(16))
        session.permanent = True
        return redirect(url_for('index'))
    else:
        # Username or password incorrect
        return redirect(url_for('login_fail', error='Username or password incorrect'))


@app.route("/loginfail/")
@std_context
def login_fail():
    context = request.context
    context['error_msg'] = request.args.get('error', 'Unknown error')
    return render_template('login_fail.html', **context)


@app.route("/logout/")
def logout():
    session.pop('userid', None)
    session.pop('username', None)
    return redirect('/')


@app.route("/post/", methods=['GET', 'POST'])
@std_context
def new_post():
    if 'userid' not in session:
        return redirect(url_for('login'))

    userid = session['userid']
    context = request.context

    if request.method == 'GET':
        session['token'] = str(os.urandom(16))
        return render_template('new_post.html', token=session.get('token'), **context)

    csrf = request.form.get('csrf')

    if csrf == session.get('token'):
        date = datetime.datetime.now().timestamp()
        title = request.form.get('title')
        content = request.form.get('content')
        query = "INSERT INTO posts (creator, date, title, content) VALUES ((?), (?), (?), (?))"
        query_db(query, (userid, date, title, content))
        get_db().commit()

    return redirect('/')


@app.route("/reset/", methods=['GET', 'POST'])
@std_context
def reset():
    context = request.context

    email = request.form.get('email', '')
    if email == '':
        return render_template('reset_request.html')

    context['email'] = encoder.encode(email)
    return render_template('sent_reset.html', **context)


@app.route("/search/")
@std_context
def search_page():
    context = request.context
    search = request.args.get('s', '')

    wildcard = '%' + search + '%'
    print(wildcard)

    query = """SELECT username FROM users WHERE username LIKE (?);"""
    users = query_db(query, (wildcard,))

    context['users'] = encoder.encode_qry(users)
    context['query'] = encoder.encode(search)
    return render_template('search_results.html', **context)


if __name__ == '__main__':
    app.run(ssl_context=('server.crt', 'server.key'))
