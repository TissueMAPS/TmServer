# -*- coding: utf-8 -*-
"""
This module holds an application instance that is passed to a server such as
gunicorn or uWSGI.

Depending on the environment variable ``TMAPS_EXECMODE``,
different configs will be loaded. This variable should be set to ``DEV``,
``TEST``, or ``PROD``. The associated configs are named ``DevConfig``,
``TestConfig``, and ``ProdConfig`` and should be importable from the config
module.

If this module is executed directly, the application is executed by a flask
development server.

"""

import os
import logging
from werkzeug.contrib.profiler import ProfilerMiddleware
import flask
from tmserver.appfactory import create_app
from tmlib.logging_utils import configure_logging

logo = """
  _____ _                    __  __    _    ____  ____
 |_   _(_)___ ___ _   _  ___|  \/  |  / \  |  _ \/ ___|
   | | | / __/ __| | | |/ _ \ |\/| | / _ \ | |_) \___ \\
   | | | \__ \__ \ |_| |  __/ |  | |/ ___ \|  __/ ___) |
   |_| |_|___/___/\__,_|\___|_|  |_/_/   \_\_|   |____/

"""

print logo

configure_logging(logging.INFO)
tmlib_logger = logging.getLogger('tmlib')
tmlib_logger.setLevel(logging.INFO)
tmserver_logger = logging.getLogger('tmserver')
tmserver_logger.setLevel(logging.INFO)

app = create_app()

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='TissueMAPS server')
    parser.add_argument(
        '--port', action='store', type=int, default=5002,
        help='the port on which the server should listen')
    # parser.add_argument(
    #     '--threaded', action='store_true', default=False,
    #     help='if the dev server should run in multi-threaded mode')
    parser.add_argument(
        '--gevent', action='store_true', default=False,
        help='if the dev server should run in gevent mode')
    parser.add_argument(
        '--profile', action='store_true', default=False,
        help='if application should be profiled')
    args = parser.parse_args()

    if args.profile:
        app.config['PROFILE'] = True
        app.wsgi_app = ProfilerMiddleware(app.wsgi_app, restrictions=[30])

    if args.gevent:
        app.run(port=args.port, debug=True)
    else:
        app.run(port=args.port, debug=True, threaded=True)