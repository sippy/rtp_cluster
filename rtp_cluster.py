#!/usr/local/bin/python
#
# Copyright (c) 2009-2014 Sippy Software, Inc. All rights reserved.
#
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without modification,
# are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice, this
# list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
# this list of conditions and the following disclaimer in the documentation and/or
# other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR
# ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
# ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

from Rtp_cluster_config import read_cluster_config
from Rtp_cluster import Rtp_cluster
from Rtp_cluster_member import Rtp_cluster_member

import getopt, os
import sys
import signal
from pwd import getpwnam
from grp import getgrnam
from socket import AF_INET, AF_INET6, AF_UNIX

from twisted.internet import reactor

from sippy_lite.SipConf import MyAddress
from sippy_lite.Signal import LogSignal
from sippy_lite.SipLogger import SipLogger
from sippy_lite.misc import daemonize

from Rtp_cluster_cli import Rtp_cluster_cli

class fakecli(object):
    rtp_clusters = None

    def __init__(self):
        self.rtp_clusters = []

def usage():
    print('usage: rtp_cluster.py [-fd] [-P pidfile] [-c conffile] [-L logfile] [-s cmd_socket]\n' \
          '        [-o uname:gname]')
    sys.exit(1)

def debug_signal(signum, frame):
    import sys, traceback
    for thread_id, stack in sys._current_frames().iteritems():
        print 'Thread id: %s\n%s' % (thread_id, ''.join(traceback.format_stack(stack)))

def reopen(signum, logfile):
    print 'Signal %d received, reopening logs' % signum
    if logfile == None:
        return
    fake_stdout = file(logfile, 'a', 1)
    sys.stdout = fake_stdout
    sys.stderr = fake_stdout
    fd = fake_stdout.fileno()
    os.dup2(fd, sys.__stdout__.fileno())
    os.dup2(fd, sys.__stderr__.fileno())

if __name__ == '__main__':
    global_config = {}

    try:
        opts, args = getopt.getopt(sys.argv[1:], 'fP:c:L:s:o:dD')
    except getopt.GetoptError:
        usage()

    sip_logger = SipLogger('rtp_cluster')

    sip_logger.write('Starting up...')

    foreground = False
    dry_run = False
    debug_threads = False
    pidfile = '/var/run/rtp_cluster.pid'
    logfile = '/var/log/rtp_cluster.log'
    csockfile = '/var/run/rtp_cluster.sock'
    global_config['conffile'] = '/usr/local/etc/rtp_cluster.xml'
    global_config['_sip_address'] = MyAddress()
    for o, a in opts:
        if o == '-f':
            foreground = True
            continue
        if o == '-P':
            pidfile = a.strip()
            continue
        if o == '-c':
            global_config['conffile'] = a.strip()
            continue
        if o == '-L':
            logfile = a.strip()
            continue
        if o == '-s':
            csockfile = a.strip()
            continue
        if o == '-o':
            sown_user, sown_gpr = a.split(':', 1)
            sown_uid = getpwnam(sown_user).pw_uid
            sown_gid = getgrnam(sown_gpr).gr_gid
            global_config['_rtpc_sockowner'] = (sown_uid, sown_gid)
            continue
        if o == '-d':
            dry_run = True
            foreground = True
            continue
        if o == '-D':
            debug_threads = True
            continue

    sip_logger.write(' o reading config "%s"...' % \
      global_config['conffile'])

    global_config['_sip_logger'] = sip_logger
    f = open(global_config['conffile'])
    config = read_cluster_config(global_config, f.read())

    if not foreground:
        # Shut down the logger and reopen it again to make sure it's worker
        # thread won't be affected by the fork()
        sip_logger.shutdown()
        daemonize(logfile = logfile)
        file(pidfile, 'w').write(str(os.getpid()) + '\n')
        sip_logger = SipLogger('rtp_cluster')
        global_config['_sip_logger'] = sip_logger
        LogSignal(sip_logger, signal.SIGUSR1, reopen, logfile)

    sip_logger.write(' o initializing CLI...')

    if not dry_run:
        cli = Rtp_cluster_cli(global_config, address = csockfile)
    else:
        cli = fakecli()

    for c in config:
        #print 'Rtp_cluster', global_config, c['name'], c['address']
        sip_logger.write(' o initializing cluster "%s" at <%s>' % (c['name'], c['address']))
        rtp_cluster = Rtp_cluster(global_config, c['name'], c['address'], \
          dnconfig = c.get('dnconfig', None), dry_run = dry_run)
        rtp_cluster.capacity_limit_soft = c.get('capacity_limit_soft', True)
        for rtpp_config in c['rtpproxies']:
            sip_logger.write('  - adding RTPproxy member %s at <%s>' % (rtpp_config['name'], rtpp_config['address']))
            #Rtp_cluster_member('rtpproxy1', global_config, ('127.0.0.1', 22222))
            if rtpp_config['protocol'] not in ('unix', 'udp', 'udp6'):
                raise Exception('Unsupported RTPproxy protocol: "%s"' % rtpp_config['protocol'])
            if rtpp_config['protocol'] in ('udp', 'udp6'):
                address = rtpp_config['address'].rsplit(':', 1)
                if len(address) == 1:
                    address.append(22222)
                else:
                    address[1] = int(address[1])
                address = tuple(address)
                if rtpp_config['protocol'] == 'udp':
                    family = AF_INET
                else:
                    family = AF_INET6
            else:
                address = rtpp_config['address']
                family = AF_UNIX
            if rtpp_config.has_key('cmd_out_address'):
                bind_address = rtpp_config['cmd_out_address']
            else:
                bind_address = None
            rtpp = Rtp_cluster_member(rtpp_config['name'], global_config, address, bind_address, family = family)
            rtpp.weight = rtpp_config['weight']
            rtpp.capacity = rtpp_config['capacity']
            if rtpp_config.has_key('wan_address'):
                rtpp.wan_address = rtpp_config['wan_address']
            if rtpp_config.has_key('lan_address'):
                rtpp.lan_address = rtpp_config['lan_address']
            rtp_cluster.add_member(rtpp)
        cli.rtp_clusters.append(rtp_cluster)
    #rtp_cluster = Rtp_cluster(global_config, 'supercluster', dry_run = dry_run)
    if dry_run:
        sip_logger.write('Configuration check is complete, no errors found')
        for rtp_cluster in cli.rtp_clusters:
            rtp_cluster.shutdown()
        sip_logger.shutdown()
        from time import sleep
        # Give worker threads some time to cease&desist
        sleep(0.1)
        sys.exit(0)
    if debug_threads:
        signal.signal(signal.SIGINFO, debug_signal)
    sip_logger.write('Initialization complete, have a good flight.')
    reactor.run(installSignalHandlers = True)
