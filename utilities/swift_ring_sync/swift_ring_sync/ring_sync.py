#!/usr/bin/env python
#
# Copyright 2013 National Institute of Informatics.
#
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

from swift.common.internal_client import InternalClient, UnexpectedResponse
import hashlib
from glob import glob
from os import stat, remove, rename, chmod, chown, mkdir, access, \
F_OK, W_OK, X_OK
from time import strftime, gmtime
from os.path import abspath, dirname, join, exists, basename, getmtime
from shutil import copy2
from tempfile import mkstemp
try:
    from swiftclient import Connection, ClientException
except ImportError:
    pass


class RingSyncError(Exception):
    """ """
    def __init__(self, msg):
        self.msg = msg

    def __str__(self):
        return self.msg


class RingSync(object):
    """ """
    def __init__(self, account, auth_url=None, password=None,
                 container='rings', internal=True):
        """ """
        self.internal = internal
        self.account = account
        self.auth_url = auth_url
        self.password = password
        self.container = container
        self.conn = None
        retry_times = 3
        if self.internal:
            try:
                conf_path = join(abspath(dirname(__file__)), 'stub.conf')
                self.conn = InternalClient(conf_path, 'swift_ring_sync',
                                           retry_times)
            except IOError, msg:
                raise RingSyncError('InternalClient Init Error: [%s]' % msg)
            except UnexpectedResponse, (msg, resp):
                raise RingSyncError('InternalClient Init Error: [%s]' % msg)
        else:
            try:
                self.conn = (lambda: Connection(authurl=self.auth_url,
                                                user=self.account,
                                                key=self.password))()
            except ClientException, msg:
                raise RingSyncError('SwiftClient Init Error: [%s]' % msg)

    def get_rings(self, ring_name, ring_dir=''):
        """ """
        if self.internal:
            return self._get_rings_internal(ring_name, ring_dir)
        return self._get_rings(ring_name, ring_dir)

    def put_rings(self, ring_file, ring_name=None):
        """ """
        if self.internal:
            return self._put_rings_internal(ring_file, ring_name)
        return self._put_rings(ring_file, ring_name)

    def _get_rings(self, ring_name, ring_dir=''):
        """ get by swiftclient. """
        local_hash = None
        try:
            headers, containers = self.conn.get_account()
        except ClientException, msg:
            raise RingSyncError('Get account failed: [%s]' % msg)
        if not self.container in [c['name'] for c in containers]:
            raise RingSyncError('Container(%s) not found.' % self.container)
        if exists(join(ring_dir, ring_name)):
            local_hash = self._get_hash(join(ring_dir, ring_name))
        try:
            headers, objects = self.conn.get_container(self.container)
        except ClientException, msg:
            raise RingSyncError('Get container(%s) failed: [%s]' %
                                (self.container, msg))
        for i in objects:
            if i['name'] == ring_name:
                if i['hash'] == local_hash:
                    return False
                try:
                    headers, body = self.conn.get_object(self.container,
                                                         i['name'])
                    _lv, tempf = mkstemp(prefix=ring_name, dir=ring_dir)
                    with open(tempf, 'wb') as ring:
                        ring.write(body)
                except ClientException, msg:
                    raise RingSyncError('Get ring(%s) faild: [%s]' %
                                        (ring_name, msg))
                except Exception, msg:
                    raise RingSyncError('Write ring(%s) faild: [%s]' %
                                        (ring_name, msg))
                return tempf
        raise RingSyncError('Ring(%s) not found.' % ring_name)

    def _put_rings(self, ring_file, ring_name=None):
        """ put by swiftclient. """
        if not ring_name:
            ring_name = basename(ring_file)
        if exists(ring_file):
            local_hash = self._get_hash(ring_file)
        else:
            raise RingSyncError('Ring(%s) not found' % ring_file)
        try:
            headers, containers = self.conn.get_account()
        except ClientException, msg:
            raise RingSyncError('Get account failed: [%s]' % msg)
        if not self.container in [c['name'] for c in containers]:
            try:
                self.conn.put_container(self.container)
            except ClientException, msg:
                raise RingSyncError('Create Container(%s) faild: [%s]' %
                                    (self.container, msg))
        try:
            headers, objects = self.conn.get_container(self.container)
        except ClientException, msg:
            raise RingSyncError('Get Container(%s) faild: [%s]' %
                                (self.container, msg))
        for i in objects:
            if i['name'] == ring_name:
                if i['hash'] == local_hash:
                    return False
        try:
            with open(ring_file, 'rb') as f:
                self.conn.put_object(self.container, ring_name, f,
                                     etag=local_hash)
        except ClientException, msg:
            raise RingSyncError('Upload ring(%s) faild: [%s]' %
                                (ring_name, msg))
        except Exception, msg:
            raise RingSyncError('Read ring(%s) faild: [%s]' %
                                (ring_name, msg))
        return True

    def _get_rings_internal(self, ring_name, ring_dir=''):
        """ get by internal_client. """
        local_hash = None
        try:
            if not self.conn.container_exists(self.account,
                                              self.container):
                raise RingSyncError('Container(%s) not found.' %
                                    self.container)
        except UnexpectedResponse, (msg, resp):
            raise RingSyncError('Check container(%s) failed: [%s]' %
                                (self.container, msg))
        if exists(join(ring_dir, ring_name)):
            local_hash = self._get_hash(join(ring_dir, ring_name))
        try:
            iter_objs = self.conn.iter_objects(self.account,
                                               self.container)
        except UnexpectedResponse, (msg, resp):
            raise RingSyncError('Get container(%s) failed: [%s]' %
                                (self.container, msg))
        for i in iter_objs:
            if i['name'] == ring_name:
                try:
                    meta = self.conn.get_object_metadata(self.account,
                                                         self.container,
                                                         i['name'])
                    if local_hash == meta['etag']:
                        return False
                    path = self.conn.make_path(self.account, self.container,
                                               i['name'])
                    resp = self.conn.make_request('GET', path, {}, [200])
                    _lv, tempf = mkstemp(prefix=ring_name, dir=ring_dir)
                    with open(tempf, 'wb') as ring:
                        ring.write(resp.body)
                except UnexpectedResponse, (msg, resp):
                    raise RingSyncError('Get ring(%s) failed: [%s]' %
                                        (ring_name, msg))
                except Exception, msg:
                    raise RingSyncError('Write ring(%s) failed: [%s]' %
                                        (ring_name, msg))
                return tempf
        raise RingSyncError('Ring(%s) not found.' % ring_name)

    def _put_rings_internal(self, ring_file, ring_name=None):
        """ put by internal_client. """
        if not ring_name:
            ring_name = basename(ring_file)
        if exists(ring_file):
            local_hash = self._get_hash(ring_file)
        else:
            raise RingSyncError('Ring(%s) not found' % ring_file)
        try:
            if not self.conn.container_exists(self.account, self.container):
                self.conn.create_container(self.account, self.container)
        except UnexpectedResponse, (msg, resp):
            raise RingSyncError('Create container(%s) faild: [%s]' %
                                (self.container, msg))
        try:
            iter_objs = self.conn.iter_objects(self.account, self.container)
        except UnexpectedResponse, (msg, resp):
            raise RingSyncError('Get container(%s) failed: [%s]' %
                                (self.container, msg))
        try:
            for i in iter_objs:
                if i['name'] == ring_name:
                    meta = self.conn.get_object_metadata(self.account,
                                                         self.container,
                                                         i['name'])
                    if local_hash == meta['etag']:
                        return False
            with open(ring_file, 'rb') as f:
                self.conn.upload_object(f, self.account,
                                        self.container, ring_name)
        except UnexpectedResponse, (msg, resp):
            raise RingSyncError('Upload ring(%s) faild: [%s]' %
                                (ring_name, msg))
        except Exception, msg:
            raise RingSyncError('Read ring(%s) faild: [%s]' %
                                (ring_name, msg))
        return True

    def _set_same_attr(self, src, dst):
        """ """
        if not exists(src) or not exists(dst):
            raise RingSyncError('file(%s or %s) not found' %
                                (src, dst))
        try:
            st = stat(src)
            chmod(dst, st.st_mode)
            chown(dst, st.st_uid, st.st_gid)
        except OSError, msg:
            raise RingSyncError('Attibute change failed: [%s]' %
                                msg)

    def update_ring_file(self, temp_file, ring_name, ring_dir='',
                         ring_backup_dir=None,
                         max_backup=8, dir_create=False):
        """ """
        if not ring_backup_dir:
            ring_backup_dir = join(ring_dir, 'backup')
        if not access(ring_backup_dir, F_OK | W_OK | X_OK):
            if dir_create:
                try:
                    mkdir(ring_backup_dir)
                except OSError, msg:
                    raise RingSyncError(
                        'Backup directory craete failed: [%s]' % msg)
            else:
                raise RingSyncError('Backup directory(%s) not found' %
                                    ring_backup_dir)
        ring_file = join(ring_dir, ring_name)
        try:
            if exists(ring_file):
                suffix = '.' + strftime('%Y%m%d%H%M%S',
                                        gmtime(getmtime(ring_file)))
                copy2(ring_file, join(ring_backup_dir, ring_file + suffix))
                self._set_same_attr(ring_file, temp_file)
            rename(temp_file, ring_file)
        except Exception, msg:
            raise RingSyncError('Old ring file backup failed: [%s]' % msg)
        self._clean_old_rings(ring_backup_dir, ring_name, max_backup)
        return True

    def _clean_old_rings(self, ring_backup_dir, base, max_backup=8):
        """ """
        try:
            old_rings = glob(join(ring_backup_dir, base + '.*'))
            old_rings.sort(None)
            if len(old_rings) >= max_backup:
                for r in [old_rings.pop(0)
                          for i in range(len(old_rings) - max_backup)]:
                    remove(r)
        except Exception, msg:
            raise RingSyncError('Clean old rings failed: [%s]' % msg)

    def _get_hash(self, filename):
        """ """
        try:
            with open(filename, 'rb') as f:
                return hashlib.md5(f.read()).hexdigest()
        except Exception, msg:
            raise RingSyncError('Read ring(%s) faild: [%s]' % (filename, msg))
