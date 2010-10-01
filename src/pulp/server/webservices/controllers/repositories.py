#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright © 2010 Red Hat, Inc.
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

import itertools
import logging

import web

from pulp.server.api.package import PackageApi
from pulp.server.api.repo import RepoApi
from pulp.server.api.repo_sync import yum_rhn_progress_callback
from pulp.server.webservices import http
from pulp.server.webservices import mongo
from pulp.server.webservices.controllers.base import JSONController, AsyncController
from pulp.server.webservices.role_check import RoleCheck

# globals ---------------------------------------------------------------------

api = RepoApi()
pkg_api = PackageApi()
log = logging.getLogger('pulp')

# default fields for repositories being sent to the client
default_fields = [
    'id',
    'source',
    'name',
    'arch',
    'sync_schedule',
    'last_sync',
    'use_symlinks',
    'groupid',
    'relative_path',
    'files', ]

# restful controllers ---------------------------------------------------------

class Repositories(JSONController):

    @JSONController.error_handler
    @RoleCheck(admin=True)
    def GET(self):
        """
        List all available repositories.
        @return: a list of all available repositories
        """
        valid_filters = ['id', 'name', 'arch', 'groupid', 'relative_path']

        filters = self.filters(valid_filters)
        spec = mongo.filters_to_re_spec(filters)

        repositories = api.repositories(spec, default_fields)

        for repo in repositories:
            repo['uri_ref'] = http.extend_uri_path(repo['id'])
            repo['package_count'] = api.package_count(repo['id'])
            repo['files_count'] = len(repo['files'])
            for field in RepositoryDeferredFields.exposed_fields:
                repo[field] = http.extend_uri_path('/'.join((repo['id'], field)))

        return self.ok(repositories)

    @JSONController.error_handler
    @RoleCheck(admin=True)
    def PUT(self):
        """
        Create a new repository.
        @return: repository meta data on successful creation of repository
        """
        repo_data = self.params()

        id = repo_data['id']
        if api.repository(id, default_fields) is not None:
            return self.conflict('A repository with the id, %s, already exists' % id)

        repo = api.create(id,
                         repo_data['name'],
                         repo_data['arch'],
                         feed=repo_data.get('feed', None),
                         symlinks=repo_data.get('use_symlinks', False),
                         sync_schedule=repo_data.get('sync_schedule', None),
                         cert_data=repo_data.get('cert_data', None),
                         relative_path=repo_data.get('relative_path', None),
                         groupid=repo_data.get('groupid', None),)

        path = http.extend_uri_path(repo.id)
        repo['uri_ref'] = path
        return self.created(path, repo)

    @JSONController.error_handler
    @RoleCheck(admin=True)
    def DELETE(self):
        """
        @return: True on successful deletion of all repositories
        """
        api.clean()
        return self.ok(True)


class Repository(JSONController):

    @JSONController.error_handler
    @RoleCheck(consumer=True, admin=True)
    def GET(self, id):
        """
        Get information on a single repository.
        @param id: repository id
        @return: repository meta data
        """
        repo = api.repository(id, default_fields)
        if repo is None:
            return self.not_found('No repository %s' % id)
        for field in RepositoryDeferredFields.exposed_fields:
            repo[field] = http.extend_uri_path(field)
        repo['uri_ref'] = http.uri_path()
        repo['package_count'] = api.package_count(id)
        repo['files_count'] = len(repo['files'])
        return self.ok(repo)

    @JSONController.error_handler
    @RoleCheck(admin=True, consumer=True)
    def PUT(self, id):
        """
        Change a repository.
        @param id: repository id
        @return: True on successful update of repository meta data
        """
        repo_data = self.params()
        if repo_data['id'] != id:
            return self.bad_request('You cannot change a repository id')
        # we need to remove the substituted uri references
        # XXX we probably need to add the original data back as well
        for field in itertools.chain(['uri_ref'], # web services only field
                                     RepositoryDeferredFields.exposed_fields):
            if field in repo_data and isinstance(repo_data[field], basestring):
                repo_data.pop(field, None)
        api.update(repo_data)
        return self.ok(True)

    @JSONController.error_handler
    @RoleCheck(admin=True)
    def DELETE(self, id):
        """
        Delete a repository.
        @param id: repository id
        @return: True on successful deletion of repository
        """
        api.delete(id=id)
        return self.ok(True)


class RepositoryDeferredFields(JSONController):

    # NOTE the intersection of exposed_fields and exposed_actions must be empty
    exposed_fields = (
        'packages',
        'packagegroups',
        'packagegroupcategories',
        'errata'
    )

    @JSONController.error_handler
    def packages(self, id):
        valid_filters = ('name', 'arch')
        filters = self.filters(valid_filters)
        repo = api.repository(id, ['id', 'packages'])
        packages = [pkg_api.package(p) for p in repo['packages']]
        if repo is None:
            return self.not_found('No repository %s' % id)
        filtered_packages = self.filter_results(packages, filters)
        return self.ok(filtered_packages)

    @JSONController.error_handler
    def packagegroups(self, id):
        repo = api.repository(id, ['id', 'packagegroups'])
        if repo is None:
            return self.not_found('No repository %s' % id)
        return self.ok(repo.get('packagegroups'))

    @JSONController.error_handler
    def packagegroupcategories(self, id):
        repo = api.repository(id, ['id', 'packagegroupcategories'])
        if repo is None:
            return self.not_found('No repository %s' % id)
        return self.ok(repo.get('packagegroupcategories', []))

    @JSONController.error_handler
    def errata(self, id):
        """
         list applicable errata for a given repo.
         filter by errata type if any
        """
        valid_filters = ('type')
        types = self.filters(valid_filters)['type']
        return self.ok(api.errata(id, types))

    @JSONController.error_handler
    @RoleCheck(consumer=True, admin=True)
    def GET(self, id, field_name):
        field = getattr(self, field_name, None)
        if field is None:
            return self.internal_server_error('No implementation for %s found' % field_name)
        return field(id)


class RepositoryActions(AsyncController):

    # All actions have been gathered here into one controller class for both
    # convenience and automatically generate the regular expression that will
    # map valid actions to this class. This also provides a single point for
    # querying existing tasks.
    #
    # There are two steps to implementing a new action:
    # 1. The action name must be added to the tuple of exposed_actions
    # 2. You must add a method to this class with the same name as the action
    #    that takes two positional arguments: 'self' and 'id' where id is the
    #    the repository id. Additional parameters from the body can be
    #    fetched and de-serialized via the self.params() call.

    # NOTE the intersection of exposed_actions and exposed_fields must be empty
    exposed_actions = (
        'sync',
        '_sync',
        'upload',
        'add_package',
        'get_package',
        'add_packages_to_group',
        'delete_package_from_group',
        'delete_packagegroup',
        'create_packagegroup',
        'add_errata',
        'list_errata',
        'delete_errata',
    )

    @JSONController.error_handler
    @RoleCheck(admin=True)
    def sync(self, id):
        """
        Sync a repository from its feed.
        @param id: repository id
        @return: True on successful sync of repository from feed
        """
        timeout = self.timeout(self.params())
        #task = self.start_task(api.sync, [id], timeout=timeout, unique=True)
        task = api.sync(id, timeout=timeout)
        if not task:
            return self.conflict('Sync already in process for repo [%s]' % id)
        repo = api.repository(id, fields=['source'])
        if repo['source'] is not None and repo['source']['type'] in ('yum', 'rhn'):
            task.set_progress('progress_callback', yum_rhn_progress_callback)
        task_info = self._task_to_dict(task)
        task_info['status_path'] = self._status_path(task.id)
        return self.accepted(task_info)

    # XXX hack to make the web services unit tests work
    _sync = sync

    @JSONController.error_handler
    @RoleCheck(admin=True)
    def upload(self, id):
        """
        Upload a package to a repository.
        @param id: repository id
        @return: True on successful upload
        """
        data = self.params()
        api.upload(id,
                   data['pkginfo'],
                   data['pkgstream'])
        return self.ok(True)

    @JSONController.error_handler
    @RoleCheck(admin=True)
    def add_package(self, id):
        """
        @param id: repository id
        @return: True on successful addition of package to repository
        """
        data = self.params()
        api.add_package(id, data['packageid'])
        return self.ok(True)

    @JSONController.error_handler
    @RoleCheck(admin=True)
    def get_package(self, id):
        """
        Get package info from a repository.
        @deprecated: user deferred fields: packages with filters instead
        @param id: repository id
        @return: matched package object available in corresponding repository
        """
        name = self.params()
        return self.ok(api.get_package(id, name))

    @JSONController.error_handler
    @RoleCheck(admin=True)
    def add_packages_to_group(self, id):
        """
        Add a package to an existing package group
        @param id: repository id
        @return: True/False
        """
        p = self.params()
        if "groupid" not in p:
            return self.not_found('No groupid specified')
        if "packagenames" not in p:
            return self.not_found('No package name specified')
        groupid = p["groupid"]
        pkg_names = p.get('packagenames', [])
        gtype = "default"
        if p.has_key("type"):
            gtype = p["type"]
        return self.ok(api.add_packages_to_group(id, groupid, pkg_names, gtype))

    @JSONController.error_handler
    @RoleCheck(admin=True)
    def delete_package_from_group(self, id):
        """
        Removes a package from an existing package group
        @param id: repository id
        @return: True/False
        """
        p = self.params()
        if "groupid" not in p:
            return self.not_found('No groupid specified')
        if "name" not in p:
            return self.not_found('No package name specified')
        groupid = p["groupid"]
        pkg_name = p["name"]
        gtype = "default"
        if p.has_key("type"):
            gtype = p["type"]
        return self.ok(api.delete_package_from_group(id, groupid, pkg_name, gtype))

    @JSONController.error_handler
    @RoleCheck(admin=True)
    def create_packagegroup(self, id):
        """
        Creates a packagegroup in the referenced repository
        @param id: repository id
        @return: 
        """
        p = self.params()
        if "groupid" not in p:
            return self.not_found('No groupid specified')
        groupid = p["groupid"]
        if "groupname" not in p:
            return self.not_found('No groupname specified')
        groupname = p["groupname"]
        if "description" not in p:
            return self.not_found('No description specified')
        descrp = p["description"]
        return self.ok(api.create_packagegroup(id, groupid, groupname,
                                               descrp))

    @JSONController.error_handler
    @RoleCheck(admin=True)
    def delete_packagegroup(self, id):
        """
        Removes a packagegroup from a repository
        @param id: repository id
        @return: 
        """
        p = self.params()
        if "groupid" not in p:
            return self.not_found('No groupid specified')
        groupid = p["groupid"]
        return self.ok(api.delete_packagegroup(id, groupid))

    @JSONController.error_handler
    @RoleCheck(admin=True)
    def add_errata(self, id):
        """
        @param id: repository id
        @return: True on successful addition of errata to repository
        """
        data = self.params()
        api.add_errata(id, data['errataid'])
        return self.ok(True)

    @JSONController.error_handler
    @RoleCheck(admin=True)
    def delete_errata(self, id):
        """
        @param id: repository id
        @return: True on successful deletion of errata from repository
        """
        data = self.params()
        api.delete_errata(id, data['errataid'])
        return self.ok(True)

    @JSONController.error_handler
    @RoleCheck(admin=True)
    def list_errata(self, id):
        """
         list applicable errata for a given repo.
         filter by errata type if any
        """
        data = self.params()
        return self.ok(api.errata(id, data['types']))

    @JSONController.error_handler
    @RoleCheck(admin=True)
    def POST(self, id, action_name):
        """
        Action dispatcher. This method checks to see if the action is exposed,
        and if so, implemented. It then calls the corresponding method (named
        the same as the action) to handle the request.
        @type id: str
        @param id: repository id
        @type action_name: str
        @param action_name: name of the action
        @return: http response
        """
        repo = api.repository(id, fields=['id'])
        if not repo:
            return self.not_found('No repository with id %s found' % id)
        action = getattr(self, action_name, None)
        if action is None:
            return self.internal_server_error('No implementation for %s found' % action_name)
        return action(id)

    @JSONController.error_handler
    @RoleCheck(admin=True)
    def GET(self, id, action_name):
        """
        Get information on a given action and repository.
        """
        action_methods = {
            'sync': '_sync',
            '_sync': '_sync',
        }
        if action_name not in action_methods:
            return self.not_found('No information for %s on repository %s' %
                                 (action_name, id))
        tasks = [] # FIXME
        if not tasks:
            return self.not_found('No recent %s on repository %s found' %
                                 (action_name, id))
        self.ok([self._task_to_dict(task) for task in tasks])



class RepositoryActionStatus(AsyncController):

    @JSONController.error_handler
    @RoleCheck(admin=True)
    def GET(self, id, action_name, action_id):
        """
        Check the status of a sync operation.
        @param id: repository id
        @param action_name: name of the action
        @param action_id: action id
        @return: action status information
        """
        task_info = self.task_status(action_id)
        if task_info is None:
            return self.not_found('No %s with id %s found' % (action_name, action_id))
        return self.ok(task_info)

    @JSONController.error_handler
    @RoleCheck(admin=True)
    def DELETE(self, id, action_name, action_id):
        """
        Cancel an action
        """
        task = self.find_task(action_id)
        if task is None:
            return self.not_found('No %s with id %s found' % (action_name, action_id))
        if self.cancel_task(task):
            return self.accepted(self._task_to_dict(task))
        # action is complete and, therefore, not canceled
        # a no-content return means the client should *not* adjust its view of
        # the resource
        return self.no_content()


class Schedules(JSONController):

    @JSONController.error_handler
    @RoleCheck(admin=True)
    def GET(self):
        '''
        Retrieve a map of all repository IDs to their associated synchronization
        schedules.

        @return: key - repository ID, value - synchronization schedule
        '''
        # XXX this returns all scheduled tasks, it should only return those
        # tasks that are specified by the action_name
        schedules = api.all_schedules()
        return self.ok(schedules)

# web.py application ----------------------------------------------------------

urls = (
    '/$', 'Repositories',
    '/schedules/', 'Schedules',
    '/([^/]+)/$', 'Repository',

    '/([^/]+)/(%s)/$' % '|'.join(RepositoryDeferredFields.exposed_fields),
    'RepositoryDeferredFields',

    '/([^/]+)/(%s)/$' % '|'.join(RepositoryActions.exposed_actions),
    'RepositoryActions',

    '/([^/]+)/(%s)/([^/]+)/$' % '|'.join(RepositoryActions.exposed_actions),
    'RepositoryActionStatus',
)

application = web.application(urls, globals())
