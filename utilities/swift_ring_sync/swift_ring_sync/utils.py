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

import logging
import os
from subprocess import check_call, CalledProcessError
import sys


def exec_hook_cmd(exec_cmd, label='', continue_on_fail=False, logger=None, quiet=False):
    """
    command launcher in script
    Config keys (like 'ring_dir') are exported to environ variables with prefix 'srs_'.
    So hook command can call that variables.

    ex: "hook_command_pre_sync = ls ${srs_ring_dir}" -> "ls /etc/swift"
        "hook_command_post_sync = test.sh"

        "test.sh" as following.
        ---------------
        #!/bin/bash
        for i in ${srs_rings}
        do
          echo ${i}
        done
        exit 0
        ---------------
    """
    try:
        check_call(exec_cmd, shell=True)
    except OSError, msg:
        p('%s cmd(%s) failed: [%s]' % (label, exec_cmd, msg), logger=logger, quiet=quiet)
    except CalledProcessError as e:
        p('%s cmd(%s) failed: rc=%s [%s]' % (label, exec_cmd, e.returncode, e),
           logger=logger, lv='warn', quiet=quiet)
        if not continue_on_fail:
            sys.exit(e.returncode)
        return True
    else:
        p('%s cmd(%s) succeed.' % (label, exec_cmd), logger=logger, quiet=quiet)
        return True


def log_level(lv='info'):
    """ """
    if lv == 'debug':
        return logging.DEBUG
    if lv == 'info':
        return logging.INFO
    if lv == 'warn':
        return logging.WARNING
    if lv == 'error':
        return logging.ERROR
    if lv == 'critical':
        return logging.CRITICAL
    return logging.INFO


def log_init(log=None, default='info'):
    """ """
    logger = logging.getLogger('swift-ring-sync')
    logger.setLevel(log_level(default))
    if log:
        fh = logging.FileHandler(log)
        fh.setLevel(log_level(default))
        fh.setFormatter(logging.Formatter(
                '%(asctime)s [%(name)s] %(levelname)s %(message)s'))
        logger.addHandler(fh)
    return logger


def p(msg, logger=None, lv='info', quiet=False):
    """ """
    if not quiet:
        print msg
    if logger:
        logging.log(log_level(lv), msg)
