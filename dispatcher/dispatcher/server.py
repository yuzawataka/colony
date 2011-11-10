# coding=utf-8
from __future__ import with_statement
try:
    import simplejson as json
except ImportError:
    import json
from base64 import urlsafe_b64encode, urlsafe_b64decode
from urllib import quote, unquote, urlencode
from urlparse import urlparse, urlunparse, parse_qs
from webob import Request, Response
from webob.exc import HTTPException, HTTPAccepted, HTTPBadRequest, \
    HTTPConflict, HTTPCreated, HTTPForbidden, HTTPMethodNotAllowed, \
    HTTPMovedPermanently, HTTPNoContent, HTTPNotFound, \
    HTTPServiceUnavailable, HTTPUnauthorized, HTTPGatewayTimeout, \
    HTTPBadGateway,  HTTPRequestEntityTooLarge, HTTPServerError
from eventlet import GreenPile, Queue, sleep, TimeoutError
from eventlet.timeout import Timeout
#from eventlet.green.httplib import HTTPConnection, HTTPResponse, HTTPSConnection
from swift.common.utils import get_logger, ContextPool
from swift.common.exceptions import ConnectionTimeout, ChunkReadTimeout, ChunkWriteTimeout
from swift.common.constraints import CONTAINER_LISTING_LIMIT, MAX_ACCOUNT_NAME_LENGTH, \
    MAX_CONTAINER_NAME_LENGTH, MAX_FILE_SIZE
from swift.common.bufferedhttp import http_connect_raw, BufferedHTTPConnection, BufferedHTTPResponse
from swift.proxy.server import update_headers
from dispatcher.common.fastestmirror import FastestMirror
import os
import sys


class RelayRequest(object):
    """ """
    def __init__(self, req, url, proxy=None, conn_timeout=None, node_timeout=None, chunk_size=65536):
        self.req = req
        self.method = req.method
        self.url = url
        self.headers = req.headers
        self.proxy = proxy
        self.chunk_size = chunk_size
        self.conn_timeout = conn_timeout if conn_timeout else 0.5
        self.client_timeout = 60
        self.node_timeout = node_timeout if node_timeout else 60
        self.retry_times_get_response_from_swift = 10

    def proxy_request_check(self, proxy, method, path):
        """
        using WebCache when 'Get Object' only.

        """
        if proxy and method == 'GET' and len(path.split('/')) == 5:
            return True
        else:
            return False

    def split_netloc(self, parsed_url):
        host, port = parsed_url.netloc.split(':')
        if not port:
            if parsed_url.scheme == 'http':
                port = 80
            elif parsed_url.scheme == 'https':
                port = 443
            else:
                return None, None
        return host, port

    def _send_file(self, conn, path):
        """Method for a file PUT coro"""
        while True:
            chunk = conn.queue.get()
            if not conn.failed:
                try:
                    with ChunkWriteTimeout(self.node_timeout):
                        conn.send(chunk)
                        print 'Sending...'
                except (Exception, ChunkWriteTimeout):
                    conn.failed = True
                    print 'Trying to write to %s' % path
            conn.queue.task_done()

    def _connect_server(self,  host, port, method, path, headers, query_string):
        with ConnectionTimeout(self.conn_timeout):
            conn = http_connect_raw(host, port, method, path, headers=headers, query_string=query_string)
            return conn

    def __call__(self):
        """
        :return httplib.HTTP(S)Connection in success, and webob.exc.HTTPException in failure
        """

        if self.headers.has_key('content-length'):
            if int(self.headers['content-length']) >= MAX_FILE_SIZE:
                return HTTPRequestEntityTooLarge(request=self.req)

        parsed = urlparse(self.url)
        if self.proxy:
            proxy_parsed = urlparse(self.proxy)

        if self.proxy_request_check(self.proxy, self.method, parsed.path):
            host, port = self.split_netloc(proxy_parsed)
            path = self.url
        else:
            host, port = self.split_netloc(parsed)
            path = parsed.path
        self.headers['host'] = '%s:%s' % (host, port)

        # if self.headers.has_key('x-auth-token'):
        #     print self.headers['x-auth-token']

        if self.method == 'PUT' and len(parsed.path.split('/')) == 5:
            chunked = self.req.headers.get('transfer-encoding')
            reader = self.req.environ['wsgi.input'].read
            data_source = iter(lambda: reader(self.chunk_size), '')
            bytes_transferred = 0
            # pile = GreenPile()
            # pile.spawn(self._connect_server, host, port, self.method, path, self.headers, parsed.query)
            # conns = [conn for conn in pile if conn]
            # conn = conns[0]
            with ConnectionTimeout(self.conn_timeout):
                conn = http_connect_raw(host, port, self.method, path, headers=self.headers, query_string=parsed.query)
            with ContextPool(1) as pool:
                conn.failed = False
                conn.queue = Queue(10)
                pool.spawn(self._send_file, conn, path)
                while True:
                    with ChunkReadTimeout(self.client_timeout):
                        try:
                            chunk = next(data_source)
                        except StopIteration:
                            if chunked:
                                conn.queue.put('0\r\n\r\n')
                            break
                        except Exception, err:
                            print 'Chunk Read Error: %s' % err
                    bytes_transferred += len(chunk)
                    if bytes_transferred > MAX_FILE_SIZE:
                        return HTTPRequestEntityTooLarge(request=self.req)
                    if not conn.failed:
                        conn.queue.put('%x\r\n%s\r\n' % (len(chunk), chunk) if chunked else chunk)
                while True:
                    if conn.queue.empty():
                        break
                    else:
                        sleep(0.1)
                if conn.queue.unfinished_tasks:
                    conn.queue.join()
            print conn
            # In heavy condition, getresponse() may fails, so it causes 'timed out' in eventlet.greenio.
            # Checking a result of response and retrying prevent it. But is this reason of the error really?
            resp = None
            with Timeout(self.node_timeout):
                for i in range(self.retry_times_get_response_from_swift):
                    print 'Retry counter: %s' % i
                    try:
                        resp = conn.getresponse()
                    except Exception, err:
                        print 'get response of PUT Error: %s' % err
                    if isinstance(resp, BufferedHTTPResponse):
                        break
                    else:
                        sleep(0.1)
            return resp
            # except ChunkReadTimeout, err:
            #     print "ChunkReadTimeout: %s" % err
            #     return HTTPRequestTimeout(request=self.req)
            # except (Exception, TimeoutError), err:
            #     print "Error: %s" % err
            #     return HTTPGatewayTimeout(request=self.req)
        else:
            try:
                with ConnectionTimeout(self.conn_timeout):
                    conn = http_connect_raw(host, port, self.method, path, headers=self.headers, query_string=parsed.query)
                with Timeout(self.node_timeout):
                    return conn.getresponse()
            except (Exception, TimeoutError), err:
                print "get response of GET or misc Error: %s" % err
                return HTTPGatewayTimeout(request=self.req)


class Dispatcher(object):
    """ """
    def __init__(self, conf):
        self.conf = conf
        self.logger = get_logger(conf, log_route='dispatcher')
        self.dispatcher_addr = conf.get('bind_ip', '127.0.0.1')
        self.dispatcher_port = int(conf.get('bind_port', 8000))
        self.ssl_enabled = True if 'cert_file' in conf else False
        self.relay_rule = conf.get('relay_rule')
        self.location, self.file_age = self.parse_location(self.relay_rule)
        self.combinater_char = conf.get('combinater_char', ':')
        self.node_timeout = int(conf.get('node_timeout', 11))
        self.conn_timeout = float(conf.get('conn_timeout', 0.5))
        self.client_timeout = int(conf.get('client_timeout', 60))

    def __call__(self, env, start_response):
        """ """

        # utilis
        def clean_path(path):
            ls = []
            for p in path.split('/'):
                if p != '':
                    ls.append(p)
            return ls

        def container_split(container, combinater_char):
            container_prefix = container.split(combinater_char)[0]
            selected_container_ls = container.split(combinater_char)[1:]
            selected_container = combinater_char.join(selected_container_ls) if len(selected_container_ls) > 1 else selected_container_ls[0]
            return container_prefix, selected_container

        def select_url_from_prefix(container_prefixes, container_prefix, each_urls):
            order = 0
            for each_url in each_urls:
                each_parsed = urlparse(each_url)
                if container_prefixes[each_parsed.scheme + '://' + each_parsed.netloc] == container_prefix:
                    selected_url = each_url
                    break
                order += 1
            return selected_url, order

        def get_info_for_selected_url(selected_url, order, path_str_ls, selected_container):
            selected_parsed = urlparse(selected_url)
            path_str_ls.pop(0)
            path_str_ls.insert(0, selected_container)
            selected_path = selected_parsed.path + '/' + '/'.join(path_str_ls)
            selected_path_str_ls = clean_path(selected_path)
            combinate_selected_url = (selected_parsed.scheme, selected_parsed.netloc, selected_path, parsed.params, parsed.query, parsed.fragment)
            relay_servers_ls = relay_servers_info['swift'][order]
            return combinate_selected_url, selected_path_str_ls, relay_servers_ls

        # check server list file's mtime
        if self.check_file_age(self.relay_rule) > self.file_age:
            self.location, self.file_age = self.parse_location(self.relay_rule)
            self.logger.info('Reloaded server list')

        # get client's request
        req = Request(env)
        parsed = urlparse(req.url)

        # get location prefix from Request
        path_location_prefix = None
        path_str_ls = clean_path(parsed.path)
        first_path_str = path_str_ls[0]

        # get swift server(s) to relay from config
        if self.location.has_key(first_path_str):
            relay_servers_info = self.location[first_path_str]
            del path_str_ls[0]
            path_location_prefix = first_path_str
        else:
            relay_servers_info = self.location['']

        webcaches = relay_servers_info['webcache']
        container_prefixes = relay_servers_info['container_prefix']

        # if relay_servers_info['swift'} has one list, enter normal mode, if it's more than 2, enter merge mode.
        relay_server_count = len(relay_servers_info['swift'])
        if relay_server_count > 1:
            resps = []

            # when has swift auth token
            if req.headers.has_key('x-auth-token') or req.headers.has_key('x-storage-token'):
                auth_token = req.headers.get('x-auth-token') or req.headers.get('x-storage-token')
                try:
                    servers_location_str = urlsafe_b64decode(unquote(path_str_ls[0]))
                except TypeError:
                    servers_location_str = None
                if servers_location_str:
                    each_urls = servers_location_str.split('__@@__')
                    path_str_ls.pop(0)
                    container = unquote(path_str_ls[0]) if len(path_str_ls) > 0 else None
                    each_token = auth_token.split('__@@__')
                else:
                    print 'Not valid url with merge mode'
                    resp = HTTPNotFound(request=req)
                    start_response(resp.status, resp.headerlist)
                    return resp.body
                query = parse_qs(parsed.query)

                # when operate to a container or an obeject
                if container:
                    if container.find(self.combinater_char) > 0:
                        container_prefix, selected_container = container_split(container, self.combinater_char)
                        selected_url, order = select_url_from_prefix(container_prefixes, container_prefix, each_urls)
                        combinate_selected_url, selected_path_str_ls, relay_servers_ls = get_info_for_selected_url(selected_url, order, path_str_ls, selected_container)
                        req.headers['x-auth-token'] = each_token[order]

                        # copy object
                        if req.method == 'PUT' and req.headers.has_key('x-copy-from') and req.headers['content-length'] == '0':
                            copy_from_container, copy_from_object = clean_path(req.headers['x-copy-from'])
                            if copy_from_container and copy_from_object and copy_from_container.find(self.combinater_char) > 0:
                                copy_from_container_prefix, copy_from_selected_container = container_split(copy_from_container, self.combinater_char)

                                # copy object in a same swift system
                                if container_prefix == copy_from_container_prefix:
                                    req.headers['x-copy-from'] = '/%s/%s' % (copy_from_selected_container, copy_from_object)
                                    print 'merge mode with copy object in a same swift system'
                                    resp = self.relay_req(req, urlunparse(combinate_selected_url), selected_path_str_ls, relay_servers_ls, webcaches)
                                    start_response(resp.status, resp.headerlist)
                                    return resp.body

                                # copy object across different swift systems
                                else:
                                    copy_from_selected_url, copy_from_order = select_url_from_prefix(container_prefixes, copy_from_container_prefix, each_urls)
                                    copy_from_combinate_selected_url, copy_from_tmp_path_str_ls, copy_from_relay_servers_ls = \
                                        get_info_for_selected_url(copy_from_selected_url, copy_from_order, path_str_ls, copy_from_selected_container)
                                    copy_from_selected_path_str_ls = \
                                        [copy_from_tmp_path_str_ls[0], copy_from_tmp_path_str_ls[1], copy_from_selected_container, copy_from_object]
                                    copy_from_req = req
                                    copy_from_req.headers['x-auth-token'] = each_token[copy_from_order]
                                    del copy_from_req.headers['content-length']
                                    del copy_from_req.headers['x-copy-from']
                                    copy_from_req.method = 'GET'
                                    copy_from_resp = self.relay_req(copy_from_req, urlunparse(copy_from_combinate_selected_url), \
                                                                        copy_from_selected_path_str_ls, copy_from_relay_servers_ls, webcaches)
                                    if copy_from_resp.status_int != 200:
                                        start_response(copy_from_resp.status, copy_from_resp.headerlist)
                                        return copy_from_resp.body
                                    req.body = copy_from_resp.body
                                    req.headers['content-length'] = len(req.body)
                                    req.headers['x-auth-token'] = each_token[order]
                                    req.method = 'PUT'
                                    resp = self.relay_req(req, urlunparse(combinate_selected_url), selected_path_str_ls, relay_servers_ls, webcaches)
                                    start_response(resp.status, resp.headerlist)
                                    return resp.body
                            # copy object error (no container)
                            else:
                                resp = HTTPNotFound(request=req)
                                start_response(resp.status, resp.headerlist)
                                return resp.body

                        print 'merge mode for each container'
                        resp = self.relay_req(req, urlunparse(combinate_selected_url), selected_path_str_ls, relay_servers_ls, webcaches)
                        start_response(resp.status, resp.headerlist)
                        return resp.body
                    else:
                        print 'merge mode for each container(error)'
                        resp = HTTPNotFound(request=req)
                        start_response(resp.status, resp.headerlist)
                        return resp.body
                else:
                    # when getting container list with marker param
                    if query.has_key('marker'):
                        container_prefix_list = []
                        marker = query['marker'][0]
                        if marker.find(self.combinater_char) > 0:
                            marker_prefix = marker.split(self.combinater_char)[0]
                            selected_marker_ls = marker.split(self.combinater_char)[1:]
                            selected_marker = self.combinater_char.join(selected_marker_ls) if len(selected_marker_ls) > 1 else selected_marker_ls[0]
                            i = 0
                            for each_url in each_urls:
                                parsed = urlparse(each_url)
                                if container_prefixes[parsed.scheme + '://' + parsed.netloc] == marker_prefix:
                                    selected_url = each_url
                                    break
                                i += 1
                            selected_parsed = urlparse(selected_url)
                            selected_path = selected_parsed.path + '/' + '/'.join(path_str_ls)
                            container_prefix_list.append(container_prefixes[selected_parsed.scheme + '://' + selected_parsed.netloc])
                            selected_path_str_ls = clean_path(selected_path)
                            query['marker'] = selected_marker
                            combinate_selected_url = (selected_parsed.scheme, selected_parsed.netloc, selected_path, parsed.params, urlencode(query, True), parsed.fragment)
                            req.headers['x-auth-token'] = each_token[i]
                            relay_servers_ls = relay_servers_info['swift'][i]

                            resp = self.relay_req(req, urlunparse(combinate_selected_url), selected_path_str_ls, relay_servers_ls, webcaches)
                            m_headers = self.merge_headers([resp], path_location_prefix)
                            content_type = None
                            for h, v in m_headers:
                                if h == 'content-type':
                                    content_type = v
                            m_body = self.merge_container_lists(content_type, [resp.body], container_prefix_list)

                            print 'container list merge mode with marker'
                            start_response(resp.status, resp.headerlist)
                            return resp.body
                        else:
                            print 'container list merge mode with marker(error)'
                            resp = HTTPNotFound(request=req)
                            start_response(resp.status, resp.headerlist)
                            return resp.body
                    else:
                        # when getting container list
                        i = 0
                        container_prefix_list = []
                        for each_url in each_urls:
                            each_parsed = urlparse(each_url)
                            container_prefix_list.append(container_prefixes[each_parsed.scheme + '://' + each_parsed.netloc])
                            path = each_parsed.path + '/'
                            each_path_str_ls = clean_path(path)

                            combinate_url = (each_parsed.scheme, each_parsed.netloc, path, parsed.params, parsed.query, parsed.fragment)
                            req.headers['x-auth-token'] = each_token[i]
                            relay_servers_ls = relay_servers_info['swift'][i]
                            resps.append(self.relay_req(req, urlunparse(combinate_url), each_path_str_ls, relay_servers_ls, webcaches))
                            i += 1

                        # for resp in resps:
                        #     print resp.status
                        #     print resp.headers
                        #     print resp.body

                        status_ls = [r.status_int for r in resps]
                        if 200 not in status_ls:
                            error_status = max(status_ls)
                            for resp in resps:
                                if resp.status_int == error_status:
                                    start_response(resp.status, resp.headerlist)
                                    return resp.body

                        success_resps = []
                        success_container_prefix_list = []
                        for resp, container_prefix in zip(resps, container_prefix_list):
                            if resp.status_int == 200:
                                success_resps.append(resp)
                                success_container_prefix_list.append(container_prefix)
                            # else:
                            #     ('X-Dispatcher-Merge-Miss', '%s: %s' % (resp.status, resp))

                        m_headers = self.merge_headers(success_resps, path_location_prefix)
                        if req.method == 'GET':
                            content_type = None
                            for h, v in m_headers:
                                if h == 'content-type':
                                    content_type = v
                            m_body = self.merge_container_lists(content_type, [r.body for r in success_resps], success_container_prefix_list)
                        else:
                            m_body = ''

                        print 'container list merge mode'
                        start_response('200 OK', m_headers)
                        return m_body
            # when authentication request
            else:
                for relay_servers_ls in relay_servers_info['swift']:
                    resps.append(self.relay_req(req, req.url, path_str_ls, relay_servers_ls, webcaches))

                # for resp in resps:
                #     print resp.status
                #     print resp.headers
                #     print resp.body

                status_ls = [r.status_int for r in resps]
                if 200 not in status_ls:
                    error_status = max(status_ls)
                    for resp in resps:
                        if resp.status_int == error_status:
                            start_response(resp.status, resp.headerlist)
                            return resp.body

                success_resps = []
                for resp in resps:
                    if resp.status_int == 200:
                        success_resps.append(resp)

                m_headers = self.merge_headers(success_resps, path_location_prefix)
                m_body = self.merge_storage_url_body([r.body for r in success_resps], path_location_prefix)

                print 'merge mode with get auth'
                start_response('200 OK', m_headers)
                return m_body
        else:
            # normal mode
            relay_servers = relay_servers_info['swift'][0]
            resp = self.relay_req(req, req.url, path_str_ls, relay_servers, webcaches)

            resp.headerlist = self.rewrite_storage_url_header(resp.headerlist, path_location_prefix)
            header_names = [h for h, v in resp.headerlist]
            if 'x-storage-url' in header_names and 'x-auth-token' in header_names and 'x-storage-token' in header_names:
                if resp.content_length > 0:
                    resp.body = self.rewrite_storage_url_body(resp.body, path_location_prefix)

            print 'normal mode'
            start_response(resp.status, resp.headerlist)
            #return resp.body
            return resp.app_iter if resp.app_iter is not None else resp.body

    def relay_req(self, req, req_url, path_str_ls, relay_servers, webcaches):
        """ """
        # util
        def get_relay_netloc(relay_server):
            parsed = urlparse(relay_server)
            svr = parsed.netloc.split(':')
            if len(svr) == 1:
                relay_addr = svr[0]
                relay_port = 443 if parsed.scheme == 'https' else 80
            else:
                relay_addr, relay_port = svr
            return relay_addr, relay_port

        parsed_req_url = urlparse(req_url)
        relay_servers_count = len(relay_servers)
        for relay_server in relay_servers:
            relay_addr, relay_port = get_relay_netloc(relay_server)
            connect_url = urlunparse((parsed_req_url.scheme, relay_addr + ':' + relay_port, '/' + '/'.join(path_str_ls),
                                      parsed_req_url.params, parsed_req_url.query, parsed_req_url.fragment))
            if webcaches[relay_server]:
                proxy = webcaches[relay_server]
            else:
                proxy = None

            self.logger.info('Req: %s %s, Connect to %s via %s' % (req.method, req.url, connect_url, proxy))

            result = RelayRequest(req, connect_url, proxy=proxy, conn_timeout=self.conn_timeout, node_timeout=self.node_timeout)()

            if isinstance(result, HTTPException):
                if relay_servers_count > 1:
                    relay_servers_count -= 1
                    self.logger.info('Retry Req: %s %s, Connect to %s via %s' % (req.method, req.url, connect_url, proxy))
                    continue
                else:
                    return result

            response = Response(status='%s %s' % (result.status, result.reason))
            response.bytes_transferred = 0

            def response_iter():
                try:
                    while True:
                        with ChunkReadTimeout(60):
                            chunk = result.read(65535)
                        if not chunk:
                            break
                        yield chunk
                        response.bytes_transferred += len(chunk)
                except GeneratorExit:
                    pass
                except (Exception, TimeoutError):
                    raise
            response.headerlist = result.getheaders()
            response.content_length = result.getheader('Content-Length')
            if response.content_length < 4096:
                response.body = result.read()
            else:
                response.app_iter = response_iter()
                update_headers(response, {'accept-ranges': 'bytes'})
                response.content_length = result.getheader('Content-Length')
            update_headers(response, result.getheaders())
            response.status = result.status

        return response

    def rewrite_storage_url_header(self, headers, path_location_prefix=None):
        """ """
        rewrited = []
        for header, value in headers:
            if header == 'x-storage-url':
                parsed = urlparse(value)
                if path_location_prefix:
                    path = '/' + path_location_prefix + parsed.path
                else:
                    path = parsed.path
                scheme = 'https' if self.ssl_enabled else 'http'
                rewrite_url = (scheme, '%s:%s' % (self.dispatcher_addr, self.dispatcher_port),\
                                   path, parsed.params, parsed.query, parsed.fragment)
                rewrited.append(('x-storage-url', urlunparse(rewrite_url)))
            else:
                rewrited.append((header, value))
        return rewrited

    def rewrite_storage_url_body(self, body, path_location_prefix=None):
        """ """
        # some auth filter (includes tempauth) doesn't return json body
        try:
            storage = json.loads(body)
        except ValueError:
            return body
        storage_rewrite = {'storage': {}}
        for k, v in storage['storage'].iteritems():
            parsed = urlparse(v)
            if parsed.scheme == '':
                storage_rewrite['storage'][k] = v
            else:
                if path_location_prefix:
                    path = '/' + path_location_prefix + parsed.path
                else:
                    path = parsed.path
                scheme = 'https' if self.ssl_enabled else 'http'
                rewrite_url = (scheme, '%s:%s' % (self.dispatcher_addr, self.dispatcher_port),\
                                   path, parsed.params, parsed.query, parsed.fragment)
                storage_rewrite['storage'][k] = urlunparse(rewrite_url)
        return json.dumps(storage_rewrite)

    def merge_headers(self, resps, merged_path_location_prefix):
        """ """
        merged = []
        storage_urls = []
        tokens = []
        account_byte_used = 0
        acount_container_count = 0
        account_object_count = 0
        storage_urls_appear = 0
        tokens_appear = 0
        account_byte_used_appear = 0
        account_container_count_appear = 0
        account_object_count_appear = 0
        misc = {}
        for resp in resps:
            if resp.status_int >= 200 and resp.status_int <= 300:
                for header, value in resp.headerlist:
                    #print '%s: %s' % (header, value)
                    if header.lower() == 'x-storage-url':
                        storage_urls.append(value)
                        storage_urls_appear += 1
                    elif header.lower() == 'x-auth-token':
                        tokens.append(value)
                        tokens_appear += 1
                    elif header.lower() == 'x-storage-token':
                        continue
                    elif header.lower() == 'x-account-bytes-used':
                        account_byte_used += int(value)
                        account_byte_used_appear += 1
                    elif header.lower() == 'x-account-container-count':
                        acount_container_count += int(value)
                        account_container_count_appear += 1
                    elif header.lower() == 'x-account-object-count':
                        account_object_count += int(value)
                        account_object_count_appear += 1
                    elif header.lower() == 'content-length':
                        continue
                    else:
                        misc[header.lower()] = value
        if storage_urls_appear:
            scheme = 'https' if self.ssl_enabled else 'http'
            scheme = 'https' if self.ssl_enabled else 'http'
            if merged_path_location_prefix:
                path = '/' + merged_path_location_prefix + '/' + urlsafe_b64encode('__@@__'.join(storage_urls))
            else:
                path = '/' + urlsafe_b64encode('__@@__'.join(storage_urls))
            rewrite_url = (scheme, '%s:%s' % (self.dispatcher_addr, self.dispatcher_port),\
                               path, None, None, None)
            merged.append(('x-storage-url', urlunparse(rewrite_url)))
        if tokens_appear:
            merged.append(('x-auth-token', '__@@__'.join(tokens)))
            merged.append(('x-storage-token', '__@@__'.join(tokens)))
        if account_byte_used_appear:
            merged.append(('x-account-bytes-used', str(account_byte_used)))
        if account_container_count_appear:
            merged.append(('x-account-container-count', str(acount_container_count)))
        if account_object_count_appear:
            merged.append(('x-account-object-count', str(account_object_count)))
        for header in misc.keys():
            merged.append((header, misc[header]))
        return merged

    def merge_storage_url_body(self, bodies, merged_path_location_prefix):
        """ """
        storage_merged = {'storage': {}}
        storage_urls = []
        for body in bodies:
            storage = json.loads(body)
            for k, v in storage['storage'].iteritems():
                parsed = urlparse(v)
                if parsed.scheme == '':
                    storage_merged['storage'][k] = v
                else:
                    storage_urls.append(v)
        scheme = 'https' if self.ssl_enabled else 'http'
        if merged_path_location_prefix:
            path = '/' + merged_path_location_prefix + '/' + urlsafe_b64encode('__@@__'.join(storage_urls))
        else:
            path = '/' + urlsafe_b64encode('__@@__'.join(storage_urls))
        rewrite_url = (scheme, '%s:%s' % (self.dispatcher_addr, self.dispatcher_port),\
                           path, None, None, None)
        storage_merged['storage'][k] = urlunparse(rewrite_url)
        return json.dumps(storage_merged)

    def merge_container_lists(self, content_type, bodies, prefixes):
        """ """
        if content_type.startswith('text/plain'):
            merge_body = []
            for prefix, body in zip(prefixes, bodies):
                for b in body.split('\n'):
                    if b != '':
                        merge_body.append(str(prefix) + self.combinater_char + b)
            merge_body.sort(cmp)
            return '\n'.join(merge_body)
        elif content_type.startswith('application/json'):
            merge_body = []
            for prefix, body in zip(prefixes, bodies):
                tmp_body = json.loads(body)
                for e in tmp_body:
                    e['name'] = prefix + self.combinater_char + e['name']
                    merge_body.append(e)
            return json.dumps(merge_body)
        elif content_type.startswith('application/xml'):
            pass
        else:
            pass

    def parse_location(self, location_str):

        """
        識別子A:サーバーリスト1, 識別子B: サーバリスト2 サーバーリスト3
        - 識別子はURLのPATH要素の先頭ディレクトリ名
        - コロンで接続された文字列は、接続対象のswiftサーバのリストのファイル。スペースで区切って複数のファイルを指定可能
        - 複数のサーバーリストファイルを持つ識別子は、2つ以上のswiftのコンテナリストの結果を融合するマージモード動作となる
        - サーバリストファイルの先頭に'('と')'で囲まれた文字列があった場合には、マージモード時のコンテナの修飾プリフィックスになる（省略可）
        - 識別子とサーバーリストファイルの組は、カンマで区切って複数指定する
        - サーバリストは、swiftのURL（スキーム・ホスト名・ポート番号）を1行ごとに記述したもの
        - 更に、swift URLの後にカンマで連結してそのswift URLに対するWebCacheサーバを指定できる（省略可）
        - 識別子が空文字列の場合には、デフォルト動作の指定となる

        :param location_str: a string like 'local:/etc/dispatcher/server0.txt, both:(cont_prefx0)/etc/dispatcher/server1.txt (cont_prefx0)/etc/dispatcher/server1.txt'

        :return dict: '{location_name0: {'swift': [['http://192.168.0.1:8080']],
                                         'webcache': {'http://192.168.0.0:8080': 'http://proxy.nii.sc.jp:8080'}},
                        location_name1: {'swift': [['http://192.168.0.2:8080','http://192.168.0.3:8080']],
                                         'webcache': {'http://192.168.0.2:8080': None, 'http://192.168.0.3:8080': None}},
                        location_name3(both): {'swift': [['http://192.168.0.4:8080'], ['http://192.168.10.5:8080', 'http://192.168.10.6:8080']],
                                         'webcache': {'http://192.168.0.4:8080': None, 'http://192.168.10.5:8080': None, 'http://192.168.10.6:8080: None'}}}
        :return file_age: the newest mtime of server list files
        """
        location = {}
        file_age = None
        for loc in location_str.split(','):
            loc_prefix, files = loc.split(':')
            location[loc_prefix.strip()] = {'swift': [], 'webcache': {}, 'container_prefix': {}}
            webcache_svrs = {}
            container_prefix = {}
            for f in files.split(None):
                prefix = None
                if f.startswith('('):
                    f_str = f.split(')')
                    prefix = f_str[0].split('(')[1]
                    f = f_str[1]
                tmp_file_age = os.stat(f).st_mtime
                if file_age:
                    if file_age < tmp_file_age:
                        file_age = tmp_file_age
                else:
                    file_age = tmp_file_age
                with open(f) as fh:
                    swift_ls = []
                    for line in fh.readlines():
                        if line.startswith('#'):
                            continue
                        svr_ls = line.split(',')
                        if len(svr_ls) == 2:
                            webcache = svr_ls[1].strip()
                        else:
                            webcache = None
                        swift = svr_ls[0].strip()
                        parsed = urlparse(swift)
                        webcache_svrs[swift] = webcache
                        container_prefix[parsed.scheme + '://' + parsed.netloc] = prefix
                        swift_ls.append(swift)
                    swift_ls = FastestMirror(swift_ls).get_mirrorlist()
                    location[loc_prefix.strip()]['swift'].append(swift_ls)
                    location[loc_prefix.strip()]['webcache'] = webcache_svrs
                    location[loc_prefix.strip()]['container_prefix'] = container_prefix
        print location
        return location, file_age

    def check_file_age(self, location_str):
        """ """
        file_age = None
        for loc in location_str.split(','):
            loc_prefix, files = loc.split(':')
            for f in files.split(None):
                if f.startswith('('):
                    f_str = f.split(')')
                    f = f_str[1]
                tmp_file_age = os.stat(f).st_mtime
                if file_age:
                    if file_age < tmp_file_age:
                        file_age = tmp_file_age
                else:
                    file_age = tmp_file_age
        return file_age

    def make_request(self, method, url, headers, body=None, proxy=None):
        parsed = urlparse(url)
        if proxy:
            proxy_parsed = urlparse(proxy)
        print proxy

        try:
            with ConnectionTimeout(300):
                if parsed.scheme == 'https':
                    conn = HTTPSConnection((proxy_parsed if proxy else parsed).netloc)
                else:
                    conn = HTTPConnection((proxy_parsed if proxy else parsed).netloc)
                if parsed.query:
                    path = parsed.path + '?' + parsed.query
                else:
                    path = parsed.path
                if proxy:
                    #conn._set_tunnel(parsed.hostname, parsed.port)
                    conn.putrequest(method, url)
                else:
                    conn.path = path
                    conn.putrequest(method, path)
                if headers:
                    for header, value in headers.iteritems():
                        conn.putheader(header, str(value))
                conn.endheaders()
                if body:
                    conn.send(body)

                with Timeout(300):
                    return conn.getresponse()
        except(Exception, Timeout):
            return HTTPGatewayTimeout


def app_factory(global_conf, **local_conf):
    """paste.deploy app factory for creating WSGI proxy apps."""
    conf = global_conf.copy()
    conf.update(local_conf)
    return Dispatcher(conf)