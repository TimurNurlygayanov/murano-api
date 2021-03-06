# Copyright (c) 2013 Mirantis Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import eventlet

import muranoapi.dsl.murano_class as murano_class
import muranoapi.dsl.murano_object as murano_object
import muranoapi.engine.system.common as common


@murano_class.classname('io.murano.system.AgentListener')
class AgentListener(murano_object.MuranoObject):
    def initialize(self, _context, name):
        self._results_queue = str('-execution-results-%s' % name.lower())
        self._subscriptions = {}
        self._receive_thread = None

    def queueName(self):
        return self._results_queue

    def start(self):
        if self._receive_thread is None:
            self._receive_thread = eventlet.spawn(self._receive)

    def stop(self):
        if self._receive_thread is not None:
            self._receive_thread.kill()
            self._receive_thread = None

    def subscribe(self, message_id, event):
        self._subscriptions[message_id] = event

    def _receive(self):
        with common.create_rmq_client() as client:
            client.declare(self._results_queue, enable_ha=True, ttl=86400000)
            with client.open(self._results_queue) as subscription:
                while True:
                    msg = subscription.get_message()
                    if not msg:
                        continue
                    msg.ack()
                    msg_id = msg.body.get('SourceID', msg.id)
                    if msg_id in self._subscriptions:
                        event = self._subscriptions.pop(msg_id)
                        event.send(msg.body)
