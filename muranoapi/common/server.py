#    Copyright (c) 2013 Mirantis, Inc.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import uuid

from oslo import messaging
from oslo.messaging.notify import dispatcher as oslo_dispatcher
from oslo.messaging import target

from sqlalchemy import desc

from muranoapi.common import config
from muranoapi.common.helpers import token_sanitizer
from muranoapi.db import models
from muranoapi.db.services import instances
from muranoapi.db import session
from muranoapi.openstack.common.gettextutils import _  # noqa
from muranoapi.openstack.common import log as logging
from muranoapi.openstack.common import timeutils


RPC_SERVICE = None
NOTIFICATION_SERVICE = None

LOG = logging.getLogger(__name__)


class ResultEndpoint(object):
    @staticmethod
    def process_result(context, result):
        secure_result = token_sanitizer.TokenSanitizer().sanitize(result)
        LOG.debug(_('Got result from orchestration '
                    'engine:\n{0}').format(secure_result))

        result_id = result['Objects']['?']['id']

        if 'deleted' in result:
            LOG.debug(_('Result for environment {0} is dropped. Environment '
                        'is deleted').format(result_id))
            return

        unit = session.get_session()
        environment = unit.query(models.Environment).get(result_id)

        if not environment:
            LOG.warning(_('Environment result could not be handled, specified '
                          'environment was not found in database'))
            return

        environment.description = result
        environment.description['Objects']['services'] = \
            environment.description['Objects'].get('applications', [])
        del environment.description['Objects']['applications']
        environment.networking = result.get('networking', {})
        environment.version += 1
        environment.save(unit)

        #close session
        conf_session = unit.query(models.Session).filter_by(
            **{'environment_id': environment.id, 'state': 'deploying'}).first()
        conf_session.state = 'deployed'
        conf_session.save(unit)

        #close deployment
        deployment = get_last_deployment(unit, environment.id)
        deployment.finished = timeutils.utcnow()

        num_errors = unit.query(models.Status)\
            .filter_by(level='error', deployment_id=deployment.id).count()
        num_warnings = unit.query(models.Status)\
            .filter_by(level='warning', deployment_id=deployment.id).count()

        final_status_text = "Deployment finished"
        if num_errors:
            final_status_text += " with errors"

        elif num_warnings:
            final_status_text += " with warnings"

        status = models.Status()
        status.deployment_id = deployment.id
        status.text = final_status_text
        status.level = 'info'
        deployment.statuses.append(status)
        deployment.save(unit)


def notification_endpoint_wrapper(priority='info'):
    def wrapper(func):
        class NotificationEndpoint(object):
            def __init__(self):
                setattr(self, priority, self._handler)

            def _handler(self, ctxt, publisher_id, event_type,
                         payload, metadata):
                if event_type == ('murano.%s' % func.__name__):
                    func(payload)

            def __call__(self, payload):
                return func(payload)
        return NotificationEndpoint()
    return wrapper


@notification_endpoint_wrapper()
def track_instance(payload):
    LOG.debug(_('Got track instance request from orchestration '
                'engine:\n{0}').format(payload))
    instance_id = payload['instance']
    instance_type = payload.get('instance_type', 0)
    environment_id = payload['environment']
    unit_count = payload.get('unit_count')
    type_name = payload['type_name']
    type_title = payload.get('type_title')

    instances.InstanceStatsServices.track_instance(
        instance_id, environment_id, instance_type,
        type_name, type_title, unit_count)


@notification_endpoint_wrapper()
def untrack_instance(payload):
    LOG.debug(_('Got untrack instance request from orchestration '
                'engine:\n{0}').format(payload))
    instance_id = payload['instance']
    environment_id = payload['environment']
    instances.InstanceStatsServices.destroy_instance(
        instance_id, environment_id)


@notification_endpoint_wrapper()
def report_notification(report):
    LOG.debug(_('Got report from orchestration '
                'engine:\n{0}').format(report))

    report['entity_id'] = report['id']
    del report['id']

    status = models.Status()
    status.update(report)

    unit = session.get_session()
    #connect with deployment
    with unit.begin():
        running_deployment = get_last_deployment(unit,
                                                 status.environment_id)
        status.deployment_id = running_deployment.id
        unit.add(status)


def get_last_deployment(unit, env_id):
    query = unit.query(models.Deployment)\
        .filter_by(environment_id=env_id)\
        .order_by(desc(models.Deployment.started))
    return query.first()


def _prepare_rpc_service(server_id):
    endpoints = [ResultEndpoint()]

    transport = messaging.get_transport(config.CONF)
    s_target = target.Target('murano', 'results', server=server_id)
    return messaging.get_rpc_server(transport, s_target, endpoints, 'eventlet')


def _prepare_notification_service(server_id):
    endpoints = [report_notification, track_instance, untrack_instance]

    transport = messaging.get_transport(config.CONF)
    s_target = target.Target(topic='murano', server=server_id)
    dispatcher = oslo_dispatcher.NotificationDispatcher(
        [s_target], endpoints, None, True)
    return messaging.MessageHandlingServer(transport, dispatcher, 'eventlet')


def get_rpc_service():
    global RPC_SERVICE

    if RPC_SERVICE is None:
        RPC_SERVICE = _prepare_rpc_service(str(uuid.uuid4()))
    return RPC_SERVICE


def get_notification_service():
    global NOTIFICATION_SERVICE

    if NOTIFICATION_SERVICE is None:
        NOTIFICATION_SERVICE = _prepare_notification_service(str(uuid.uuid4()))
    return NOTIFICATION_SERVICE
