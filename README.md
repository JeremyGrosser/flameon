Campfire IRC services
---------------------
This service bridges a campfire account to an IRC server by connecting as a
server-to-server peer. Only one campfire account may be used, and all proxied
messages appear as that single user on the campfire side.

Installation
------------
Requirements:
 - Python (>=2.5 should work, tested with 2.6)
 - eventlet (tested with 0.9.16, others may work)
 - An IRC server configured for a services peer (tested with ngircd-15)

Configuration
-------------
Edit the second-to-last line of flame.py, filling in your campfire and IRC
credentials.

Running
-------

  $ python flame.py

Caveat Emptor
-------------
This code certainly has bugs. I don't use it on a day-to-day basis anymore, but
I will accept patches.
