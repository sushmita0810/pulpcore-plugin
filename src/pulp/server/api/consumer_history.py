#!/usr/bin/python
#
# Copyright (c) 2010 Red Hat, Inc.
#
# This software is licensed to you under the GNU General Public License,
# version 2 (GPLv2). There is NO WARRANTY for this software, express or
# implied, including the implied warranties of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. You should have received a copy of GPLv2
# along with this software; if not, see
# http://www.gnu.org/licenses/old-licenses/gpl-2.0.txt.
#
# Red Hat trademarks are not licensed under GPLv2. No permission is
# granted to use or replicate Red Hat trademarks that are incorporated
# in this software or its documentation.

'''
Consumer history related API methods.
'''

# Python
import logging

# 3rd Party
import pymongo

# Pulp
from pulp.server.api.base import BaseApi
from pulp.server.api.consumer import ConsumerApi
from pulp.server.db.connection import get_object_db
from pulp.server.db.model import ConsumerHistoryEvent
from pulp.server.pexceptions import PulpException


# -- constants ----------------------------------------

LOG = logging.getLogger(__name__)

# Event Types
TYPE_CONSUMER_CREATED = 'consumer_created'
TYPE_CONSUMER_DELETED = 'consumer_deleted'
TYPE_REPO_BOUND = 'repo_bound'
TYPE_REPO_UNBOUND = 'repo_unbound'
TYPE_PACKAGE_INSTALLED = 'package_installed'
TYPE_PACKAGE_UNINSTALLED = 'package_uninstalled'
TYPE_PROFILE_CHANGED = 'profile_changed'

TYPES = (TYPE_CONSUMER_CREATED, TYPE_CONSUMER_DELETED, TYPE_REPO_BOUND,
         TYPE_REPO_UNBOUND, TYPE_PACKAGE_INSTALLED, TYPE_PACKAGE_UNINSTALLED,
         TYPE_PROFILE_CHANGED)

# Used to identify an event as triggered by the consumer (as compared to an admin)
ORIGINATOR_CONSUMER = 'consumer'

# Maps user entered query sort parameters to the pymongo representation
SORT_DIRECTION = {
    'ascending' : pymongo.ASCENDING,
    'descending' : pymongo.DESCENDING,
}


class ConsumerHistoryApi(BaseApi):

    # -- setup ----------------------------------------

    def __init__(self):
        BaseApi.__init__(self)
        self.consumer_api = ConsumerApi()

    def _getcollection(self):
        return get_object_db('consumer_history',
                             self._unique_indexes,
                             self._indexes)

    # -- public api ----------------------------------------

    def query(self, consumer_id=None, event_type=None, limit=None, sort='descending',
              start_date=None, end_date=None):
        '''
        Queries the consumer history storage.

        @param consumer_id: if specified, events will only be returned for the the
                            consumer referenced; an error is raised if there is no
                            consumer for the given ID
        @type  consumer_id: string or number

        @param event_type: if specified, only events of the given type are returned;
                           an error is raised if the event type mentioned is not listed
                           in the results of the L{event_types} call
        @type  event_type: string (enumeration found in TYPES)

        @param limit: if specified, the query will only return up to this amount of
                      entries; default is to not limit the entries returned
        @type  limit: number greater than zero

        @param sort: indicates the sort direction of the results; results are sorted
                     by timestamp
        @type  sort: string; valid values are 'ascending' and 'descending'

        @param start_date: if specified, no events prior to this date will be returned
        @type  start_date: L{datetime.datetime}

        @param end_date: if specified, no events after this date will be returned
        @type  end_date: L{datetime.datetime}

        @return: list of consumer history entries that match the given parameters;
                 empty list (not None) if no matching entries are found
        @rtype:  list of L{pulp.server.db.model.ConsumerHistoryEvent} instances 
        '''

        # Verify the consumer ID represents a valid consumer
        if consumer_id and not self.consumer_api.consumer(consumer_id):
            raise PulpException('Invalid consumer ID [%s]' % consumer_id)

        # Verify the event type is valid
        if event_type and event_type not in TYPES:
            raise PulpException('Invalid event type [%s]' % event_type)

        # Verify the limit makes sense
        if limit is not None and limit < 1:
            raise PulpException('Invalid limit [%s], limit must be greater than zero' % limit)
            
        # Verify the sort direction was valid
        if not sort in SORT_DIRECTION:
            valid_sorts = ', '.join(SORT_DIRECTION)
            raise PulpException('Invalid sort direction [%s], valid values [%s]' % (sort, valid_sorts))

        # Assemble the mongo search parameters
        search_params = {}
        if consumer_id:
            search_params['consumer_id'] = consumer_id
        if event_type:
            search_params['type_name'] = event_type

        # Add in date range limits if specified
        date_range = {}
        if start_date:
            date_range['$gt'] = start_date
        if end_date:
            date_range['$lt'] = end_date

        if len(date_range) > 0:
            search_params['timestamp'] = date_range

        # Determine the correct mongo cursor to retrieve
        if len(search_params) == 0:
            cursor = self.objectdb.find()
        else:
            cursor = self.objectdb.find(search_params)

        # Sort by most recent entry first
        cursor.sort('timestamp', direction=SORT_DIRECTION[sort])

        # If a limit was specified, add it to the cursor
        if limit:
            cursor.limit(limit)

        # Finally convert to a list before returning
        return list(cursor)

    def event_types(self):
        return TYPES

    # -- internal ----------------------------------------

    def consumer_created(self, consumer_id, originator=ORIGINATOR_CONSUMER):
        '''
        Creates a new event to represent a consumer being created.

        @param consumer_id: identifies the newly created consumer
        @type  consumer_id: string or number

        @param originator: if specified, should be the username of the admin who created
                           the consumer through the admin API; defaults to indicate the
                           create was triggered by the consumer itself
        @type  originator: string
        '''
        event = ConsumerHistoryEvent(consumer_id, originator, TYPE_CONSUMER_CREATED, None)
        self.insert(event)

    def consumer_deleted(self, consumer_id, originator=ORIGINATOR_CONSUMER):
        '''
        Creates a new event to represent a consumer being deleted.

        @param consumer_id: identifies the deleted consumer
        @type  consumer_id: string or number

        @param originator: if specified, should be the username of the admin who deleted
                           the consumer through the admin API; defaults to indicate the
                           create was triggered by the consumer itself
        @type  originator: string
        '''
        event = ConsumerHistoryEvent(consumer_id, originator, TYPE_CONSUMER_DELETED, None)
        self.insert(event)

    def repo_bound(self, consumer_id, repo_id, originator=ORIGINATOR_CONSUMER):
        '''
        Creates a new event to represent a consumer binding to a repo.

        @param consumer_id: identifies the consumer being modified
        @type  consumer_id: string or number

        @param repo_id: identifies the repo being bound to the consumer
        @type  repo_id: string or number

        @param originator: if specified, should be the username of the admin who bound
                           the repo through the admin API; defaults to indicate the
                           create was triggered by the consumer itself
        @type  originator: string
        '''
        details = {'repo_id' : repo_id}
        event = ConsumerHistoryEvent(consumer_id, originator, TYPE_REPO_BOUND, details)
        self.insert(event)

    def repo_unbound(self, consumer_id, repo_id, originator=ORIGINATOR_CONSUMER):
        '''
        Creates a new event to represent removing a binding from a repo.

        @param consumer_id: identifies the consumer being modified
        @type  consumer_id: string or number

        @param repo_id: identifies the repo being unbound from the consumer
        @type  repo_id: string or number

        @param originator: if specified, should be the username of the admin who unbound
                           the repo through the admin API; defaults to indicate the
                           create was triggered by the consumer itself
        @type  originator: string
        '''
        details = {'repo_id' : repo_id}
        event = ConsumerHistoryEvent(consumer_id, originator, TYPE_REPO_UNBOUND, details)
        self.insert(event)

    def packages_installed(self, consumer_id, package_nveras, originator=ORIGINATOR_CONSUMER):
        '''
        Creates a new event to represent packages that were installed on a consumer.

        @param consumer_id: identifies the consumer being modified
        @type  consumer_id: string or number

        @param package_nveras: identifies the packages that were installed on the consumer
        @type  package_nveras: list or string; a single string will automatically be wrapped
                               in a list

        @param originator: if specified, should be the username of the admin who installed
                           packages through the admin API; defaults to indicate the
                           create was triggered by the consumer itself
        @type  originator: string
        '''
        if type(package_nveras) != list:
            package_nveras = [package_nveras]

        details = {'package_nveras' : package_nveras}
        event = ConsumerHistoryEvent(consumer_id, originator, TYPE_PACKAGE_INSTALLED, details)
        self.insert(event)

    def packages_removed(self, consumer_id, package_nveras, originator=ORIGINATOR_CONSUMER):
        '''
        Creates a new event to represent packages that were removed from a consumer.

        @param consumer_id: identifies the consumer being modified
        @type  consumer_id: string or number

        @param package_nveras: identifies the packages that were removed from the consumer
        @type  package_nveras: list or string; a single string will automatically be wrapped
                               in a list

        @param originator: if specified, should be the username of the admin who removed
                           packages through the admin API; defaults to indicate the
                           create was triggered by the consumer itself
        @type  originator: string
        '''
        if type(package_nveras) != list:
            package_nveras = [package_nveras]

        details = {'package_nveras' : package_nveras}
        event = ConsumerHistoryEvent(consumer_id, originator, TYPE_PACKAGE_UNINSTALLED, details)
        self.insert(event)
