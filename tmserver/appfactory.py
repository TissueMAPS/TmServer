# TmServer - TissueMAPS server application.
# Copyright (C) 2016  Markus D. Herrmann, University of Zurich and Robin Hafen
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
import sys
import os
from os.path import join, dirname, abspath
import logging
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import NoResultFound, MultipleResultsFound
from flask_sqlalchemy_session import flask_scoped_session
from flask import Flask, jsonify
import gc3libs

import tmlib.models as tm
from tmlib.log import map_logging_verbosity
from tmlib.models.utils import create_db_engine, create_db_session_factory

from tmserver.extensions import jwt
from tmserver.serialize import TmJSONEncoder
from tmserver.error import register_http_error_classes
from tmserver import cfg
from tmlib import cfg as libcfg


logger = logging.getLogger(__name__)


def get_interrupted_tasks():
    """Gets the IDs of all tasks that are not in state ``STOPPED`` or
    ``TERMINATED``. If tasks are have one of these states at server startup,
    they have probably been interrupted by a previous shutdown and need to
    be resubmitted.

    Returns
    -------
    List[int]
    """
    with tm.utils.MainSession() as session:
        top_task_ids = session.query(tm.Submission.top_task_id).all()
        tasks = session.query(tm.Task.id).\
            filter(
                tm.Task.id.in_(top_task_ids),
                ~tm.Task.state.in_({'STOPPED', 'TERMINATED', 'TERMINATING'})
            ).\
            all()
        return [t.id for t in tasks]


def create_app(verbosity=None):
    """Creates a Flask application object that registers all the blueprints on
    which the actual routes are defined.

    Parameters
    ----------
    verbosity: int, optional
        logging verbosity to override the
        :attr:`logging_verbosity <tmserver.config.ServerConfig.logging_verbosity>`
        setting in the configuration file (default: ``None``)

    Returns
    -------
    flask.Flask
        Flask application

    """
    log_formatter = logging.Formatter(
        fmt='%(asctime)s | %(levelname)-8s | %(name)-40s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    log_handler = logging.StreamHandler(stream=sys.stdout)
    log_handler.setFormatter(log_formatter)
    if verbosity is None:
        verbosity = cfg.logging_verbosity
    log_level = map_logging_verbosity(verbosity)

    app = Flask('wsgi')
    app.config['PROPAGATE_EXCEPTIONS'] = True

    app.logger.handlers = []  # remove standard handlers
    app.logger.setLevel(log_level)
    app.logger.addHandler(log_handler)

    tmserver_logger = logging.getLogger('tmserver')
    tmserver_logger.setLevel(log_level)
    tmserver_logger.addHandler(log_handler)

    tmlib_logger = logging.getLogger('tmlib')
    tmlib_logger.setLevel(log_level)
    tmlib_logger.addHandler(log_handler)

    flask_jwt_logger = logging.getLogger('flask_jwt')
    flask_jwt_logger.setLevel(log_level)
    flask_jwt_logger.addHandler(log_handler)

    gevent_logger = logging.getLogger('gevent')
    gevent_logger.addHandler(log_handler)
    gc3pie_logger = logging.getLogger('gc3.gc3libs')
    gc3pie_logger.addHandler(log_handler)
    wsgi_logger = logging.getLogger('wsgi')
    wsgi_logger.addHandler(log_handler)
    apscheduler_logger = logging.getLogger('apscheduler')
    apscheduler_logger.addHandler(log_handler)
    if verbosity > 4:
        gevent_logger.setLevel(logging.DEBUG)
        gc3pie_logger.setLevel(logging.DEBUG)
        wsgi_logger.setLevel(logging.DEBUG)
        apscheduler_logger.setLevel(logging.DEBUG)
    elif verbosity > 3:
        gevent_logger.setLevel(logging.INFO)
        gc3pie_logger.setLevel(logging.INFO)
        wsgi_logger.setLevel(logging.INFO)
        apscheduler_logger.setLevel(logging.INFO)
    else:
        gevent_logger.setLevel(logging.ERROR)
        gc3pie_logger.setLevel(logging.ERROR)
        wsgi_logger.setLevel(logging.ERROR)
        apscheduler_logger.setLevel(logging.ERROR)

    app.json_encoder = TmJSONEncoder

    if cfg.secret_key == 'default_secret_key':
        app.logger.warn('The application will run with the default secret key!')
    elif not cfg.secret_key:
        app.logger.critical('Specify a secret key for this application!')
        sys.exit(1)
    app.config['SECRET_KEY'] = cfg.secret_key

    app.config['JWT_EXPIRATION_DELTA'] = cfg.jwt_expiration_delta

    ## Error handling

    # Register custom error classes
    register_http_error_classes(app)

    # Register SQLAlchemy error classes
    @app.errorhandler(NoResultFound)
    def _handle_no_result_found(error):
        response = jsonify(error={
            'message': error.message,
            'status_code': 400,
            'type': error.__class__.__name__
        })
        logger.error('no result found: ' + error.message)
        response.status_code = 400
        return response

    @app.errorhandler(MultipleResultsFound)
    def _multiple_results_found(error):
        response = jsonify(error={
            'message': error.message,
            'status_code': 409,
            'type': error.__class__.__name__
        })
        logger.error('multiple results found: ' + error.message)
        response.status_code = 409
        return response

    @app.errorhandler(IntegrityError)
    def _handle_integrity_error(error):
        response = jsonify(error={
            'error': True,
            'message': error.message,
            'status_code': 500,
            'type': error.__class__.__name__
        })
        logger.error('database integrity error: ' + error.message)
        response.status_code = 500
        return response


    ## Initialize Plugins
    jwt.init_app(app)

    # Create a session scope for interacting with the main database
    engine = create_db_engine(cfg.db_master_uri)
    session_factory = create_db_session_factory()
    session_factory.configure(bind=engine)
    session = flask_scoped_session(session_factory, app)

    from tmserver.extensions import gc3pie
    gc3pie.init_app(app)

    ## Import and register blueprints
    from tmserver.api import api
    app.register_blueprint(api, url_prefix='/api')

    from tmserver.jtui import jtui
    app.register_blueprint(jtui, url_prefix='/jtui')

    # Restart all jobs that might have been accidentially stopped by
    # a server shutdown.
    with app.app_context():
        task_ids = get_interrupted_tasks()
        for tid in task_ids:
            task = gc3pie.retrieve_task(tid)
            gc3pie.continue_task(task)

    # For uWSGI fork()
    engine.dispose()

    return app
