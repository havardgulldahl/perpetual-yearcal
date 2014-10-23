#!/usr/bin/env python
# encoding: utf-8
# The MIT License (MIT)

# Copyright (c) 2014 Håvard Gulldahl

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

# stdlib stuff
import os.path, logging, httplib2, datetime, calendar

# third party stuff
#import dateutil

# appengine stuff
import webapp2, jinja2
from apiclient.discovery import build
from google.appengine.ext import webapp
from oauth2client.appengine import OAuth2DecoratorFromClientSecrets
from google.appengine.api import memcache

# our own stuff
from models import Color

JINJA_ENVIRONMENT = jinja2.Environment(
    loader=jinja2.FileSystemLoader(os.path.dirname(__file__)),
    extensions=['jinja2.ext.autoescape'],
    autoescape=True)

# Restrict access to users that have granted access to Calendar information.
decorator = OAuth2DecoratorFromClientSecrets(
  os.path.join(os.path.dirname(__file__), 'client_secrets.json'),
  scope='https://www.googleapis.com/auth/calendar')

http = httplib2.Http(memcache)
service = build('calendar', 'v3', http=http)

def render_response(template, **context):
    template = JINJA_ENVIRONMENT.get_template(os.path.join('templates', template))
    return template.render(**context)

def parse_date(d):
    """Parse {u'date': u'2014-10-10'} or {u'dateTime': u'2014-10-10T12:30:00+02:00'} and return datetime"""
    if d.has_key('date'):
        return datetime.datetime.strptime(d['date'], '%Y-%m-%d').date()
    elif d.has_key('dateTime'):
        return datetime.datetime.strptime(d['dateTime'].split('+')[0], '%Y-%m-%dT%H:%M:%S').date()
    else:
        return None

class Date(object):
    def __init__(self, date, events):
        self.date = date
        self.month = date.month
        self.year, self.weeknumber, self.weekday = date.isocalendar() # Return a 3-tuple, (ISO year, ISO week number, ISO weekday).
        self.events = events

class YearCalendar(calendar.Calendar):
    "Super Class of calendar.Calendar to display a year with events"
    def __init__(self, year, events, firstweekday=None):
        super(YearCalendar, self).__init__(firstweekday=firstweekday or 0) # 0 == Monday
        self.year = year
        # logging.info('eents:%s', events)
        _e = []
        for e in events:
            eventdate = parse_date(e['start'])
            _e.append( (eventdate, e) )
            eventend = parse_date(e['end'])
            oneday = datetime.timedelta(days=1)
            while eventdate+oneday < eventend: # this events spans multiple days, expand it
                eventdate = eventdate+oneday
                _e.append( (eventdate, e) )
        self.events = _e
    def iterdates(self):
        "iterate over all dates"
        jan1 = datetime.date(self.year, 1, 1)
        for xd in range(0, 366):
            _d = jan1+datetime.timedelta(days=xd)
            yield Date(_d, self.get_events(_d))
    def dates(self):
        "return all dates as a list"
        _all = {}
        for d in self.iterdates():
            if d.year != self.year: 
                continue
            try:
                _all[d.month].append(d)
            except KeyError:
                _all[d.month] = [d,]
        return _all


    def get_events(self, date):
        "return a list of all events for a specific datetime.date"
        evts = []
        for ev in self.events:
            # (datetime.date, data)
            if ev[0] == date:
                evts.append(ev[1])
        return evts

class CalListHandler(webapp2.RequestHandler):
    @decorator.oauth_aware
    def get(self):
        if decorator.has_credentials():
            cal_list = service.calendarList().list().execute(http=decorator.http())
            self.response.write(render_response('index.html', calendars=list([c for c in cal_list['items']])))
        else:
            url = decorator.authorize_url()
            self.response.write(render_response('index.html', calendars=[], authorize_url=url))  	

class CalHandler(webapp2.RequestHandler):
    @decorator.oauth_aware
    def get(self, cal_id, **kwargs):
        if decorator.has_credentials():
            # get keywords or default values
            year = kwargs.get('year', datetime.datetime.now().year)
            fields = kwargs.get('fields', 'description,items(colorId,creator,description,end,iCalUID,id,location,start,status,summary),nextPageToken,summary')
            cal_events = service.events().list(calendarId=cal_id, 
                                               singleEvents=True,
                                               fields=fields, 
                                               orderBy='startTime').execute(http=decorator.http())
            
            yc = YearCalendar(year, cal_events['items'])
            self.response.write(render_response('calendar.html', calendar=yc))
        else:
            url = decorator.authorize_url()
            self.response.write(render_response('index.html', calendars=[], authorize_url=url))   

class GetColorsHandler(webapp2.RequestHandler):
    @decorator.oauth_aware
    def get(self):
        if decorator.has_credentials():
            colors = service.colors().get().execute(http=decorator.http())
            logging.info(colors)
            for z in ('calendar', 'event'):
                for colId, col in colors[z].items():
                    mycol = Color.get_or_insert('%s#%s' % (z, colId), colorId=colId, 
                                                                      category=z,
                                                                      **col)
            return webapp2.redirect_to('colors')
        else:
            url = decorator.authorize_url()
            self.response.write(render_response('index.html', calendars=[], authorize_url=url))     

class ColorsHandler(webapp2.RequestHandler):
    def get(self):
        self.response.write(render_response('colors.html', colors=Color.query()))     

class ColorsCSSHandler(webapp2.RequestHandler):
    def get(self):
        self.response.content_type = 'text/css'
        self.response.write(render_response('colors.css', colors=Color.query()))     

class MainHandler(webapp2.RequestHandler):
    def get(self):
        self.response.write('Hello world!')

app = webapp2.WSGIApplication([
    ('/', MainHandler),
    ('/cals', CalListHandler),
    (r'/cal/([^\s]+)', CalHandler),
    ('/getcolors', GetColorsHandler),
    ('/colors', ColorsHandler, 'colors'),
    ('/colors.css', ColorsCSSHandler, 'colors-css'),
    (decorator.callback_path, decorator.callback_handler()),

], debug=True)
