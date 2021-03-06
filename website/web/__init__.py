#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import pickle
from zipfile import ZipFile, ZIP_DEFLATED
from io import BytesIO
import os
import logging
from pathlib import Path

from flask import Flask, render_template, request, session, send_file, redirect, url_for, Response
from flask_bootstrap import Bootstrap  # type: ignore

from lookyloo.helpers import get_homedir, update_user_agents, get_user_agents
from lookyloo.lookyloo import Lookyloo
from lookyloo.exceptions import NoValidHarFile

from typing import Tuple

app: Flask = Flask(__name__)

secret_file_path: Path = get_homedir() / 'secret_key'

if not secret_file_path.exists() or secret_file_path.stat().st_size < 64:
    with secret_file_path.open('wb') as f:
        f.write(os.urandom(64))

with secret_file_path.open('rb') as f:
    app.config['SECRET_KEY'] = f.read()

Bootstrap(app)
app.config['BOOTSTRAP_SERVE_LOCAL'] = True
app.config['SESSION_COOKIE_NAME'] = 'lookyloo'
app.debug = False

splash_url: str = 'http://127.0.0.1:8050'
# API entry point for splash
if os.environ.get('SPLASH_URL'):
    splash_url = os.environ['SPLASH_URL']
# Splash log level
loglevel = logging.DEBUG
# Set it to True if your instance is publicly available so users aren't able to scan your internal network
only_global_lookups: bool = False

lookyloo: Lookyloo = Lookyloo(splash_url=splash_url, loglevel=loglevel, only_global_lookups=only_global_lookups)


# keep
def load_tree(report_dir: Path) -> Tuple[dict, str, str, str, dict]:
    session.clear()
    temp_file_name, tree_json, tree_time, tree_ua, tree_root_url, meta = lookyloo.load_tree(report_dir)
    session["tree"] = temp_file_name
    return tree_json, tree_time, tree_ua, tree_root_url, meta


@app.route('/submit', methods=['POST', 'GET'])
def submit():
    to_query = request.get_json(force=True)
    perma_uuid = lookyloo.enqueue_scrape(to_query)
    return Response(perma_uuid, mimetype='text/text')


@app.route('/scrape', methods=['GET', 'POST'])
def scrape_web():
    if request.form.get('url'):
        perma_uuid = lookyloo.scrape(url=request.form.get('url'), depth=request.form.get('depth'),
                                     listing=request.form.get('listing'), user_agent=request.form.get('user_agent'),
                                     os=request.form.get('os'), browser=request.form.get('browser'))
        return redirect(url_for('tree', tree_uuid=perma_uuid))
    user_agents = get_user_agents()
    user_agents.pop('by_frequency')
    return render_template('scrape.html', user_agents=user_agents)


@app.route('/tree/hostname/<node_uuid>/text', methods=['GET'])
def hostnode_details_text(node_uuid):
    with open(session["tree"], 'rb') as f:
        ct = pickle.load(f)
    hostnode = ct.root_hartree.get_host_node_by_uuid(node_uuid)
    urls = []
    for url in hostnode.urls:
        urls.append(url.name)
    content = '''# URLs

{}
'''.format('\n'.join(urls))
    to_return = BytesIO(content.encode())
    to_return.seek(0)
    return send_file(to_return, mimetype='text/markdown',
                     as_attachment=True, attachment_filename='file.md')


@app.route('/tree/hostname/<node_uuid>', methods=['GET'])
def hostnode_details(node_uuid):
    with open(session["tree"], 'rb') as f:
        ct = pickle.load(f)
    hostnode = ct.root_hartree.get_host_node_by_uuid(node_uuid)
    urls = []
    for url in hostnode.urls:
        if hasattr(url, 'body_hash'):
            sane_js_r = lookyloo.sane_js_query(url.body_hash)
            if sane_js_r.get('response'):
                url.add_feature('sane_js_details', sane_js_r['response'])
                print('######## SANEJS ##### ', url.sane_js_details)
        urls.append(url.to_json())
    return json.dumps(urls)


@app.route('/tree/url/<node_uuid>', methods=['GET'])
def urlnode_details(node_uuid):
    with open(session["tree"], 'rb') as f:
        ct = pickle.load(f)
    urlnode = ct.root_hartree.get_url_node_by_uuid(node_uuid)
    to_return = BytesIO()
    got_content = False
    if hasattr(urlnode, 'body'):
        body_content = urlnode.body.getvalue()
        if body_content:
            got_content = True
            with ZipFile(to_return, 'w', ZIP_DEFLATED) as zfile:
                zfile.writestr(urlnode.filename, urlnode.body.getvalue())
    if not got_content:
        with ZipFile(to_return, 'w', ZIP_DEFLATED) as zfile:
            zfile.writestr('file.txt', b'Response body empty')
    to_return.seek(0)
    return send_file(to_return, mimetype='application/zip',
                     as_attachment=True, attachment_filename='file.zip')


@app.route('/tree/<string:tree_uuid>/image', methods=['GET'])
def image(tree_uuid):
    report_dir = lookyloo.lookup_report_dir(tree_uuid)
    if not report_dir:
        return Response('Not available.', mimetype='text/text')
    to_return = lookyloo.load_image(report_dir)
    return send_file(to_return, mimetype='image/png',
                     as_attachment=True, attachment_filename='image.png')


@app.route('/tree/<string:tree_uuid>', methods=['GET'])
def tree(tree_uuid):
    report_dir = lookyloo.lookup_report_dir(tree_uuid)
    if not report_dir:
        return redirect(url_for('index'))

    try:
        tree_json, start_time, user_agent, root_url, meta = load_tree(report_dir)
        return render_template('tree.html', tree_json=tree_json, start_time=start_time,
                               user_agent=user_agent, root_url=root_url, tree_uuid=tree_uuid,
                               meta=meta)
    except NoValidHarFile as e:
        return render_template('error.html', error_message=e)


@app.route('/', methods=['GET'])
def index():
    if request.method == 'HEAD':
        # Just returns ack if the webserver is running
        return 'Ack'
    lookyloo.cleanup_old_tmpfiles()
    update_user_agents()
    session.clear()
    titles = []
    for report_dir in lookyloo.report_dirs:
        cached = lookyloo.report_cache(report_dir)
        if not cached or 'no_index' in cached:
            continue
        titles.append((cached['uuid'], cached['title']))
    return render_template('index.html', titles=titles)
