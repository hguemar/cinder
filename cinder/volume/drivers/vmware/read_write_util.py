# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright (c) 2013 VMware, Inc.
# All Rights Reserved.
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

"""
Classes to handle image files.
Collection of classes to handle image upload/download to/from Image service
(like Glance image storage and retrieval service) from/to VMware server.
"""

import httplib
import netaddr
import urllib
import urllib2
import urlparse

from cinder.openstack.common import log as logging

LOG = logging.getLogger(__name__)
USER_AGENT = 'OpenStack-ESX-Adapter'
READ_CHUNKSIZE = 65536


class GlanceFileRead(object):
    """Glance file read handler class."""

    def __init__(self, glance_read_iter):
        self.glance_read_iter = glance_read_iter
        self.iter = self.get_next()

    def read(self, chunk_size):
        """Read an item from the queue.

        The chunk size is ignored for the Client ImageBodyIterator
        uses its own CHUNKSIZE.
        """
        try:
            return self.iter.next()
        except StopIteration:
            return ""

    def get_next(self):
        """Get the next item from the image iterator."""
        for data in self.glance_read_iter:
            yield data

    def close(self):
        """A dummy close just to maintain consistency."""
        pass


class VMwareHTTPFile(object):
    """Base class for HTTP file."""

    def __init__(self, file_handle):
        self.eof = False
        self.file_handle = file_handle

    def set_eof(self, eof):
        """Set the end of file marker."""
        self.eof = eof

    def get_eof(self):
        """Check if the end of file has been reached."""
        return self.eof

    def close(self):
        """Close the file handle."""
        try:
            self.file_handle.close()
        except Exception as exc:
            LOG.exception(exc)

    def __del__(self):
        """Close the file handle on garbage collection."""
        self.close()

    def _build_vim_cookie_headers(self, vim_cookies):
        """Build ESX host session cookie headers."""
        cookie_header = ""
        for vim_cookie in vim_cookies:
            cookie_header = vim_cookie.name + '=' + vim_cookie.value
            break
        return cookie_header

    def write(self, data):
        """Write data to the file."""
        raise NotImplementedError()

    def read(self, chunk_size):
        """Read a chunk of data."""
        raise NotImplementedError()

    def get_size(self):
        """Get size of the file to be read."""
        raise NotImplementedError()

    def _is_valid_ipv6(self, address):
        """Whether given host address is a valid IPv6 address."""
        try:
            return netaddr.valid_ipv6(address)
        except Exception:
            return False

    def get_soap_url(self, scheme, host):
        """return IPv4/v6 compatible url constructed for host."""
        if self._is_valid_ipv6(host):
            return '%s://[%s]' % (scheme, host)
        return '%s://%s' % (scheme, host)


class VMwareHTTPWriteFile(VMwareHTTPFile):
    """VMware file write handler class."""

    def __init__(self, host, data_center_name, datastore_name, cookies,
                 file_path, file_size, scheme='https'):
        soap_url = self.get_soap_url(scheme, host)
        base_url = '%s/folder/%s' % (soap_url, file_path)
        param_list = {'dcPath': data_center_name, 'dsName': datastore_name}
        base_url = base_url + '?' + urllib.urlencode(param_list)
        _urlparse = urlparse.urlparse(base_url)
        scheme, netloc, path, params, query, fragment = _urlparse
        if scheme == 'http':
            conn = httplib.HTTPConnection(netloc)
        elif scheme == 'https':
            conn = httplib.HTTPSConnection(netloc)
        conn.putrequest('PUT', path + '?' + query)
        conn.putheader('User-Agent', USER_AGENT)
        conn.putheader('Content-Length', file_size)
        conn.putheader('Cookie', self._build_vim_cookie_headers(cookies))
        conn.endheaders()
        self.conn = conn
        VMwareHTTPFile.__init__(self, conn)

    def write(self, data):
        """Write to the file."""
        self.file_handle.send(data)

    def close(self):
        """Get the response and close the connection."""
        try:
            self.conn.getresponse()
        except Exception as excep:
            LOG.debug(_("Exception during HTTP connection close in "
                        "VMwareHTTPWrite. Exception is %s.") % excep)
        super(VMwareHTTPWriteFile, self).close()


class VMwareHTTPReadFile(VMwareHTTPFile):
    """VMware file read handler class."""

    def __init__(self, host, data_center_name, datastore_name, cookies,
                 file_path, scheme='https'):
        soap_url = self.get_soap_url(scheme, host)
        base_url = '%s/folder/%s' % (soap_url, urllib.pathname2url(file_path))
        param_list = {'dcPath': data_center_name, 'dsName': datastore_name}
        base_url = base_url + '?' + urllib.urlencode(param_list)
        headers = {'User-Agent': USER_AGENT,
                   'Cookie': self._build_vim_cookie_headers(cookies)}
        request = urllib2.Request(base_url, None, headers)
        conn = urllib2.urlopen(request)
        VMwareHTTPFile.__init__(self, conn)

    def read(self, chunk_size):
        """Read a chunk of data."""
        # We are ignoring the chunk size passed for we want the pipe to hold
        # data items of the chunk-size that Glance Client uses for read
        # while writing.
        return self.file_handle.read(READ_CHUNKSIZE)

    def get_size(self):
        """Get size of the file to be read."""
        return self.file_handle.headers.get('Content-Length', -1)