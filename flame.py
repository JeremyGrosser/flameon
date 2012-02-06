import eventlet
eventlet.monkey_patch()
import urllib2
import socket
import ssl
import json
import time

#urllib2.install_opener(urllib2.build_opener(urllib2.HTTPSHandler(debuglevel=1)))

class Request(urllib2.Request):
    def __init__(self, method, url, data=None, headers={}):
        urllib2.Request.__init__(self, url, data, headers)
        self.method = method

    def get_method(self):
        return self.method


class Campfire(object):
    def __init__(self, token, subdomain):
        self.token = token.encode('base64').rstrip('\n')
        self.subdomain = subdomain

    def request(self, method, endpoint, headers={}, data=None):
        h = {
            'Host': '%s.campfirenow.com' % self.subdomain,
            'Authorization': 'Basic %s' % self.token,
        }
        h.update(headers)

        req = Request(method, 'https://%s.campfirenow.com/%s.json' % (self.subdomain, endpoint), headers=h, data=data)
        resp = urllib2.urlopen(req)
        return resp.read()

    def join_room(self, room):
        return self.request('POST', '%s/join' % room)

    def get_rooms(self):
        resp = self.request('GET', 'rooms')
        return json.loads(resp)['rooms']

    def get_room(self, roomid):
        resp = self.request('GET', 'room/%s' % roomid)
        return json.loads(resp)['room']

    def get_user(self, userid):
        resp = self.request('GET', 'users/%s' % userid)
        return json.loads(resp)['user']

    def speak(self, roomid, message):
        message = message[1:]
        data = json.dumps({'message': {'body': message}})
        resp = self.request('POST', 'room/%s/speak' % roomid, {
            'Content-type': 'application/json',
        }, data=data)
        try:
            return json.loads(resp.read())['message']
        except Exception, e:
            print 'Error in speak(%s %s): %s' % (repr(roomid), repr(message), str(e))
            print e.read()


class CampfireStream(object):
    def __init__(self, token, room, name):
        self.token = token
        self.room = room
        self.name = name
        self.buf = ''
        self.last_keepalive = 0

    def consume(self):
        is_size = True
        while self.buf.find('\r\n') != -1:
            line, self.buf = self.buf.split('\r\n', 1)
            if line.isdigit():
                size = int(line, 16)
                is_size = False
            else:
                self.handle(line)
                is_size = True

    def get_stream(self):
        print 'Campfire stream starting for', self.name
        req = Request('GET', 'https://streaming.campfirenow.com/room/%s/live.json' % self.room, headers={
            'Host': 'streaming.campfirenow.com',
            'Authorization': 'Basic %s' % self.token.encode('base64')
        })
        print 'Campfire stream starting for', self.name
        return urllib2.urlopen(req)

    def run(self):
        resp = self.get_stream()
        sock = resp.fp._sock.fp._sock

        while True:
            data = sock.read(1024)
            self.buf += data
            self.consume()
            if data == '':
                print 'Lost campfire stream connection for', self.name
                print 'Reconnecting in 5 seconds'
                eventlet.sleep(5)
                resp = self.get_stream()
                sock = resp.fp._sock.fp._sock


    def handle(self, line):
        if line == ' ':
            self.last_keepalive = time.time()
            return
        #print len(line), size
        for part in line.split('\r'):
            part = part.strip('\n ')
            if not part:
                continue
            try:
                part = json.loads(part)
            except Exception, e:
                print repr(part)
                print str(e)
                continue
            print 'CAMP <<<', json.dumps(part)
            if not isinstance(part, dict):
                return
            method = getattr(self, 'handle_%s' % part['type'], None)
            if method is not None:
                method(self, part)

    #def handle_TextMessage(self, msg):
    #    pass


class IRCService(object):
    def __init__(self, name, host, password, ident, port=6667):
        self.name = name
        self.info = name
        self.server = (host, port)
        self.password = password
        self.ident = ident
        self.sock = None
        self.buf = ''

    def connect(self):
        self.sock = socket.socket()
        self.sock.connect(self.server)
        self.send('PASS %s' % self.password)
        self.send('SERVER %s %i :%s' % (self.name, 1, self.info))
        self.send('NICK campfire :1')
        self.send(':campfire USER campfire %s %s :campfire' % (self.ident, self.ident))

    def consume(self):
        while self.buf.find('\r\n') != -1:
            line, self.buf = self.buf.split('\r\n', 1)
            self.handle(line)

    def send(self, line):
        line = line.encode('ascii', 'replace')
        if not line.startswith('PONG'):
            print 'IRC', '>>>', line
        self.sock.sendall(line + '\r\n')

    def run(self):
        self.connect()
        while True:
            data = self.sock.recv(1024)
            if data == '':
                print 'IRC peer connection lost'
                break
            self.buf += data
            self.consume()

    def handle(self, line):
        if line.startswith('PING'):
            self.send('PONG %s' % line.split(' ', 1)[1])
            return
        print 'IRC', '<<<', line

        if not line.startswith(':'):
            return
        hostmask, cmd, line = line.split(' ', 2)
        hostmask = hostmask.lstrip(':')
        method = getattr(self, 'handle_%s' % cmd, None)
        if method is None:
            return
        method(line)
    

class Controller(object):
    def __init__(self, token, subdomain, ircpeer, ircpassword, ircport=6667, hostname=None):
        self.token = token
        self.subdomain = subdomain
        self.ircpeer = ircpeer
        self.ircpassword = ircpassword
        self.ircport = ircport

        if hostname is None:
            self.hostname = socket.getfqdn()
        else:
            self.hostname = hostname

        self.campfire = Campfire(self.token, subdomain, ident=self.hostname)
        self.channels = {}
        self.users = {}

    def update_rooms(self):
        channels = {}
        for room in self.campfire.get_rooms():
            name = room['name']
            name = name.lower().replace(' ', '_')
            channels[name] = room
        self.channels = channels

    def join_room(self, channel):
        print 'Syncing join', channel
        self.update_rooms()
        channel = channel.lstrip('#')
        if not channel in self.channels:
            return

        self.campfire.join_room(self.channels[channel]['id'])
        room = self.campfire.get_room(self.channels[channel]['id'])
        self.ircpeer.send(':campfire JOIN #%s' % channel)
        self.ircpeer.send(':campfire MODE #%s +o campfire' % channel)
        self.ircpeer.send(':campfire TOPIC #%s :%s' % (channel, room['topic']))
        for user in room['users']:
            user['ircname'] = user['name'].lower().replace(' ', '_').replace('.', '_')
            if not user['id'] in self.users:
                self.ircpeer.send('NICK %s :1' % user['ircname'])
                self.ircpeer.send(':%s USER %s %s %s :%s' % (user['ircname'], user['ircname'], self.hostname, self.hostname, user['name']))
            self.users[user['id']] = user
            self.ircpeer.send(':%s JOIN #%s' % (user['ircname'], channel))

        stream = CampfireStream(self.token, self.channels[channel]['id'], channel)
        stream.handle_TextMessage = self.campfire_message
        stream.handle_PasteMessage = self.campfire_message
        stream.handle_EnterMessage = self.campfire_join
        stream.handle_KickMessage = self.campfire_kick
        stream.handle_LeaveMessage = self.campfire_leave
        eventlet.spawn_n(stream.run)

    def update_user(self, userid):
        user = self.campfire.get_user(userid)
        user['ircname'] = user['name'].lower().replace(' ', '_').replace('.', '_')
        self.users[userid] = user
        self.ircpeer.send('NICK %s :1' % user['ircname'])
        self.ircpeer.send(':%s USER %s %s %s :%s' % (user['ircname'], user['ircname'], self.hostname, self.hostname, user['name']))
        return user['ircname']

    def campfire_message(self, stream, msg):
        if not msg['user_id'] in self.users:
            username = self.update_user(msg['user_id'])
        else:
            username = self.users[msg['user_id']]['ircname']
        if username == 'jeremy_grosser':
            return

        body = msg['body']
        for line in body.split('\n'):
            self.ircpeer.send(':%s PRIVMSG #%s :%s' % (username, stream.name, line))

    def campfire_join(self, stream, msg):
        if not msg['user_id'] in self.users:
            username = self.update_user(msg['user_id'])
        else:
            username = self.users[msg['user_id']]['ircname']
        self.ircpeer.send(':%s JOIN #%s' % (username, stream.name))

    def campfire_kick(self, stream, msg):
        username = self.users[msg['user_id']]['ircname']
        self.ircpeer.send(':campfire KICK #%s %s :%s' % (stream.name, username, msg.get('body', '')))

    def campfire_leave(self, stream, msg):
        username = self.users[msg['user_id']]['ircname']
        self.ircpeer.send(':%s PART #%s' % (username, stream.name))

    def irc_message(self, line):
        channel, message = line.split(' ', 1)
        channel = channel.lstrip('#')
        self.campfire.speak(self.channels[channel]['id'], message)

    def run(self):
        self.ircpeer = IRCService(self.hostname, self.ircpeer, self.ircpassword, self.ircport)
        self.ircpeer.handle_JOIN = self.join_room
        self.ircpeer.handle_PRIVMSG = self.irc_message
        eventlet.spawn_n(self.ircpeer.run)

        while True:
            eventlet.sleep(300)
            self.update_rooms()


if __name__ == '__main__':
    app = Controller('<campfire token>', '<campfire subdomain>', '<irc server>', 6668, '<irc services password>')
    app.run()
