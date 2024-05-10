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

from errno import EADDRINUSE
from random import random
from functools import partial

try:
    from urllib import quote, unquote
except ImportError:
    from urllib.parse import quote, unquote

from DNRelay import DNRelay

from sippy.CLIManager import CLIConnectionManager
from sippy.Udp_server import Udp_server, Udp_server_opts
from sippy.Rtp_proxy_cmd import Rtp_proxy_cmd, Rtpp_stats
from sippy.Time.Timeout import TimeoutInact
from sippy.Core.EventDispatcher import ED2


def is_dst_local(destination_ip):
    #if destination_ip == '192.168.22.11':
    #    return True
    return False

class Broadcaster(object):
    bcount = None
    ecount = None
    nparts = None
    results = None
    clim = None
    cmd = None
    orig_cmd = None

    def __init__(self, bcount, clim, cmd, orig_cmd):
        self.results = []
        self.bcount = bcount
        self.ecount = bcount
        self.nparts = bcount
        self.clim = clim
        self.cmd = cmd
        self.orig_cmd = orig_cmd

class UdpCLIM(object):
    cookie = None
    address = None

    def __init__(self, address, cookie, server):
        self.cookie = cookie
        self.address = tuple(address)
        self.server = server

    def send(self, data):
        self.server.send_to('%s %s' % (self.cookie, data), self.address)

    def close(self):
        self.server = None

class Rtp_cluster(object):
    global_config = None
    address = None
    name = None
    active = None
    pending = None
    ccm = None
    commands_inflight = None
    l1rcache = None
    l2rcache = None
    cache_purge_el = None
    dnrelay = None
    capacity_limit_soft = True

    def __init__(self, global_config, name, address = '/var/run/rtpproxy.sock', \
      dnconfig = None, dry_run = False):
        self.active = []
        self.pending = []
        self.l1rcache = {}
        self.l2rcache = {}
        if len(address) == 2:
            if not dry_run:
                uopts = Udp_server_opts(address, self.up_command_udp)
                uopts.nworkers = 2
                self.ccm = Udp_server(global_config, uopts)
        else:
            sown = global_config.get('_rtpc_sockowner', None)
            if not dry_run:
                self.ccm = CLIConnectionManager(self.up_command, address, sown)
        self.global_config = global_config
        self.name = name
        self.address = address
        self.commands_inflight = []
        self.cache_purge_el = TimeoutInact(self.rCachePurge, 10, -1)
        self.cache_purge_el.spread_runs(0.1)
        self.cache_purge_el.go()
        self.update_dnrelay(dnconfig, dry_run)

    def update_dnrelay(self, dnconfig, dry_run = False):
        if self.dnrelay != None:
            if dnconfig != None and self.dnrelay.cmpconfig(dnconfig):
                return
            allow_from = self.dnrelay.get_allow_list()
            self.dnrelay.shutdown()
            self.dnrelay = None
        else:
            allow_from = None
        if dnconfig == None:
            return
        try:
            self.dnrelay = DNRelay(dnconfig, self.global_config['_sip_logger'])
        except OSError as ex:
            if not dry_run or ex.errno != EADDRINUSE:
                raise
        if allow_from != None:
            self.dnrelay.set_allow_list(allow_from)

    def add_member(self, member):
        member.on_state_change = self.rtpp_status_change
        if member.online:
            self.active.append(member)
        else:
            self.pending.append(member)
        if not member.is_local and self.dnrelay != None:
            self.dnrelay.allow_from(member.address)

    def up_command_udp(self, data, address, server, rtime):
        dataparts = data.decode('ascii').split(None, 1)
        if len(dataparts) == 1:
            return
        cookie, cmd = dataparts
        if cookie in self.commands_inflight:
            return
        cresp = self.l1rcache.get(cookie, self.l2rcache.get(cookie, None))
        if cresp != None:
            response = '%s %s' % (cookie, cresp)
            server.send_to(response, address)
            self.global_config['_sip_logger'].write('Rtp_cluster.up_command_udp(): '
              'sending cached response "%s" to %s' % (response[:-1], address))
            return
        self.commands_inflight.append(cookie)
        clim = UdpCLIM(address, cookie, server)
        return self.up_command(clim, cmd)

    def up_command(self, clim, orig_cmd):
        #print(f'up_command({orig_cmd=})')
        cmd = Rtp_proxy_cmd(orig_cmd)
        response_handler = self.down_command
        #print cmd
        if len(self.active) == 0:
            self.down_command(clim, cmd, orig_cmd, None, 'E999')
            return
        if cmd.type in ('U', 'L', 'D', 'P', 'S', 'R', 'C', 'Q'):
            #print(f'up_command: {cmd.call_id=}, {orig_cmd=}, {str(cmd)=}')
            for rtpp in self.active:
                if rtpp.isYours(cmd.call_id):
                    break
            else:
                rtpp = None
            if cmd.type == 'U' and cmd.ul_opts.to_tag == None:
                new_session = True
            else:
                new_session = False
            if rtpp == None and not new_session:
                # Existing session, also check if it exists on any of the offline
                # members and try to relay it there, it makes no sense to broadcast
                # the call to every other node in that case
                for rtpp in self.pending:
                    #print 'Looking for "%s" in pending"' % cmd.call_id
                    if rtpp.isYours(cmd.call_id):
                        break
                else:
                    rtpp = None
            if rtpp != None and cmd.type == 'D':
                rtpp.unbind_session(cmd.call_id)
                if not rtpp.online:
                    self.global_config['_sip_logger'].write('Delete request to a ' \
                      '(possibly) offline node "%s", sending fake reply and proceeding ' \
                      'in the background' % rtpp.name)
                    self.down_command(clim, cmd, orig_cmd, None, '0')
                    response_handler = self.ignore_response
            if rtpp == None and new_session:
                # New session
                rtpp = self.pick_proxy(cmd.call_id)
                if rtpp == None:
                    self.down_command(clim, cmd, orig_cmd, None, 'E998')
                    return
                rtpp.bind_session(cmd.call_id, cmd.type)
            if rtpp != None and cmd.type in ('U', 'L') and cmd.ul_opts.notify_socket != None:
                if rtpp.wdnt_supported and self.dnrelay != None and not rtpp.is_local and \
                  cmd.ul_opts.notify_socket.startswith(self.dnrelay.dest_sprefix):
                    pref_len = len(self.dnrelay.dest_sprefix)
                    dnstr = '%s %s' % (cmd.ul_opts.notify_socket[pref_len:], \
                      unquote(cmd.ul_opts.notify_tag))
                    cmd.ul_opts.notify_tag = quote(dnstr)
                    cmd.ul_opts.notify_socket = 'tcp:%%%%CC_SELF%%%%:%d' % self.dnrelay.in_address[1]
                    orig_cmd = str(cmd)
                elif not rtpp.is_local:
                    cmd.ul_opts.notify_tag = None
                    cmd.ul_opts.notify_socket = None
                    orig_cmd = str(cmd)
            if rtpp == None:
                # Existing session we know nothing about
                if cmd.type == 'U':
                    # Do a forced lookup
                    orig_cmd = 'L%s' % cmd.ul_opts.getstr(cmd.call_id, swaptags = True, skipnotify = True)
                active = [x for x in self.active if x.online]
                br = Broadcaster(len(active), clim, cmd, orig_cmd)
                for rtpp in active:
                    if cmd.type in ('U', 'L') and rtpp.lan_address != None:
                        out_cmd = Rtp_proxy_cmd(orig_cmd)
                        out_cmd.ul_opts.local_ip = rtpp.lan_address
                        out_cmd.ul_opts.destination_ip = None
                    else:
                        out_cmd = orig_cmd
                    rtpp.send_command(out_cmd, self.merge_results, br, rtpp)
                return
        elif cmd.type == 'I' and cmd.command_opts == 'b':
            active = [x for x in self.active if x.online]
            sessions_created = active_sessions = active_streams = preceived = ptransmitted = 0
            for rtpp in active:
                if rtpp.active_sessions == None:
                    # There might be some time between "online" and heartbeat reply,
                    # when stats are still empty, or when proxy goes from offline
                    # to online, skip it
                    continue
                sessions_created += rtpp.sessions_created
                active_sessions += rtpp.active_sessions
                active_streams += rtpp.active_streams
                preceived += rtpp.preceived
                ptransmitted += rtpp.ptransmitted
            reply = 'sessions created: %d\nactive sessions: %d\nactive streams: %d\npackets received: %d\npackets transmitted: %d' % \
              (sessions_created, active_sessions, active_streams, preceived, ptransmitted)
            self.down_command(clim, cmd, orig_cmd, None, reply)
            return
        elif cmd.type == 'G':
            active = [x for x in self.active if x.online]
            br = Broadcaster(len(active), clim, cmd, orig_cmd)
            br.sobj = Rtpp_stats(cmd.args.split())
            if cmd.command_opts != None and cmd.command_opts.lower() == 'v':
                cmd.command_opts = None
                br.sobj.verbose = True
            cmd.nretr = 0
            for rtpp in active:
                rtpp.send_command(cmd, self.merge_stats_results, br, rtpp)
            return
        else:
            rtpp = self.active[0]
            #print 'up', cmd
        #print 'rtpp.send_command'
        if cmd.type in ('U', 'L') and rtpp.lan_address != None:
            out_cmd = Rtp_proxy_cmd(orig_cmd)
            out_cmd.ul_opts.local_ip = rtpp.lan_address
            out_cmd.ul_opts.destination_ip = None
        else:
            out_cmd = orig_cmd
        rtpp.send_command(str(out_cmd), partial(response_handler, clim, cmd, out_cmd, rtpp))

    def ignore_response(self, clim, cmd, out_cmd, rtpp, result):
        if result is None:
            return
        self.global_config['_sip_logger'].write('Got delayed response ' \
          'from node "%s" to already completed request, ignoring: "%s"' \
          % (rtpp.name, result))

    def down_command(self, clim, cmd, out_cmd, rtpp, result):
        if isinstance(clim, UdpCLIM) and clim.cookie in self.commands_inflight:
            self.commands_inflight.remove(clim.cookie)
        #if cmd.type in ('U', 'L'): print(f'down_command({result=})')
        if result == None:
            result = 'E997'
        elif cmd.type in ('U', 'L') and not result[0].upper() == 'E' and \
          rtpp.wan_address != None:
            #print(f'down_command: {cmd.ul_opts.destination_ip=}, {cmd.ul_opts.local_ip=}, {rtpp.wan_address=}')
            req_dip = cmd.ul_opts.destination_ip
            req_lip = cmd.ul_opts.local_ip
            req_lip_out = out_cmd.ul_opts.local_ip
            result_parts = result.strip().split()
            if result_parts[0] != '0' and req_dip != None and not is_dst_local(req_dip) and \
              req_lip != rtpp.lan_address:
                result = '%s %s' % (result_parts[0], rtpp.wan_address)
            elif result_parts[0] != '0' and (req_lip is None or req_lip_out == rtpp.lan_address):
                result = '%s %s' % (result_parts[0], rtpp.wan_address)
        #    result = '%s %s' % (result_parts[0], '192.168.1.22')
        #if cmd.type in ('U', 'L'): print(f'down_command: clim.send({result=})')
        response = result + '\n'
        clim.send(response)
        if isinstance(clim, UdpCLIM):
            self.l1rcache[clim.cookie] = response
        clim.close()

    def merge_results(self, result, br, rtpp):
        if result == None:
            result = 'E996'
        if br != None and not result[0].upper() == 'E' and not \
          (br.cmd.type in ('U', 'L') and result == '0'):
            br.results.append(result)
        br.bcount -= 1
        if br.bcount > 0:
            # More results to come
            return
        if len(br.results) == 1:
            rtpp.bind_session(br.cmd.call_id, br.cmd.type)
            self.down_command(br.clim, br.cmd, br.orig_cmd, rtpp, br.results[0])
        else:
            # No results or more than one proxy returns positive
            # XXX: more than one result can probably be handled
            if br.cmd.type in ('U', 'L'):
                self.down_command(br.clim, br.cmd, br.orig_cmd, rtpp, '0')
            else:
                self.down_command(br.clim, br.cmd, br.orig_cmd, rtpp, 'E995')

    def merge_stats_results(self, result, br, rtpp):
        #print 'merge_stats_results, result', result
        if result == None:
            result = rtpp.stats_cache.get(br.sobj.all_names, 'E994')
            self.global_config['_sip_logger'].write('merge_stats_results: node "%s": ' \
              'getting from the cache "%s"' % (rtpp.name, result))
        elif result[0].upper() != 'E':
            rtpp.stats_cache[br.sobj.all_names] = result
        if br != None and not result[0].upper() == 'E':
            try:
                br.sobj.parseAndAdd(result)
                br.ecount -= 1
            except:
                pass
        br.bcount -= 1
        if br.bcount > 0:
            # More results to come
            return
        #print 'merge_stats_results, br.sobj', br.sobj
        if br.ecount == br.nparts:
            rval = 'E993'
        else:
            rval = str(br.sobj)
        self.down_command(br.clim, br.cmd, br.orig_cmd, rtpp, rval)

    def pick_proxy(self, call_id):
        active = [(rtpp, rtpp.weight * (1 - rtpp.get_caputil())) \
          for rtpp in self.active if rtpp.status == 'ACTIVE' and rtpp.online]
        available = [(rtpp, weight) for rtpp, weight in active if weight > 0]
        if len(available) > 0:
            # Normal case, there are some proxies that are loaded below their capacities
            total_weight = sum([x[1] for x in available])
            thr_weight = (random() * total_weight) % total_weight
            #print total_weight, thr_weight
            for rtpp, weight in available:
                thr_weight -= weight
                if thr_weight < 0:
                    break
            #print 'pick_proxyNG: picked up %s for the call %s (normal)' % (rtpp.name, call_id)
            return rtpp
        elif len(active) > 0 and self.capacity_limit_soft:
            max_rtpp, max_weight = active[0] 
            for rtpp, weight in active[1:]:
                if weight > max_weight:
                    max_rtpp, max_weight = rtpp, weight
            #print 'pick_proxyNG: picked up %s for the call %s (overload)' % (max_rtpp.name, call_id)
            return max_rtpp
        self.global_config['_sip_logger'].write('pick_proxyNG: OUCH, no proxies to ' \
          'pickup from for the call %s' % (call_id,))
        return None

    def rtpp_status_change(self, rtpp, online):
        #print 'rtpp_status_change', self, rtpp, online
        if online and rtpp in self.pending:
            self.pending.remove(rtpp)
            self.active.append(rtpp)
        if not online and rtpp in self.active:
            self.active.remove(rtpp)
            self.pending.append(rtpp)

    def bring_down(self, rtpp):
        #print 'bring_down', self, rtpp
        if not rtpp.is_local and self.dnrelay != None:
            self.dnrelay.disallow_from(rtpp.address)
        if rtpp in self.active:
            if len(rtpp.call_id_map) == 0 or rtpp.active_sessions in (0, None):
                self.active.remove(rtpp)
                rtpp.shutdown()
                return
            rtpp.status = 'DRAINING'
            rtpp.on_active_update = self.rtpp_active_change
            return
        self.pending.remove(rtpp)
        rtpp.shutdown()

    def rtpp_active_change(self, rtpp, active_sessions):
        if rtpp.status == 'DRAINING' and (len(rtpp.call_id_map) == 0 or active_sessions == 0):
            if rtpp in self.pending:
                self.pending.remove(rtpp)
            else:
                self.active.remove(rtpp)
            rtpp.shutdown()

    def rtpp_by_name(self, name):
        idx = 0
        for rtpp in self.active + self.pending:
            if rtpp.name == name:
                return (rtpp, idx)
            idx += 1
        return (None, None)

    def shutdown(self):
        for rtpp in self.active + self.pending:
            rtpp.shutdown()
        if self.ccm != None:
            self.ccm.shutdown()
        if self.cache_purge_el != None:
            self.cache_purge_el.cancel()
        self.active = None
        self.pending = None
        self.ccm = None
        self.cache_purge_el = None
        if self.dnrelay != None:
            self.dnrelay.shutdown()

    def all_members(self):
        return tuple(self.active + self.pending)

    def rCachePurge(self):
        self.l2rcache = self.l1rcache
        self.l1rcache = {}

if __name__ == '__main__':
    global_config = {}
    rtp_cluster = Rtp_cluster(global_config, 'supercluster')
    ED2.loop()
