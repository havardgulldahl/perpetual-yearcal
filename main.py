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
import collections, copy

# third party stuff
# install howto in appengine_requirements.txt
import dateutil.parser

# appengine stuff
import webapp2, jinja2
from apiclient.discovery import build
from google.appengine.ext import webapp
from oauth2client.appengine import OAuth2DecoratorFromClientSecrets
from google.appengine.api import memcache

# our own stuff
from models import Color, CalendarPrettyTitle

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
        return dateutil.parser.parse(d['date'], fuzzy=True).date()
    elif d.has_key('dateTime'):
        return dateutil.parser.parse(d['dateTime'], fuzzy=True).date()
    else:
        return None

class Date(object):
    def __init__(self, date, events):
        self.date = date #datetime.datetime
        self.month = date.month
        self.year, self.weeknumber, self.weekday = date.isocalendar() # Return a 3-tuple, (ISO year, ISO week number, ISO weekday).
        self.events = events # list
        self.yearmonth = self.date.strftime('%Y-%m')


class Event(object):
    "High level access to the dict returned from gcal"
    def __init__(self, gcaldict): # parse a dict from gcal
        #logging.info('Event from %s', gcaldict)
        self.startdate = parse_date(gcaldict['start']) # get datetime.datetime
        self.enddate = parse_date(gcaldict['end']) # get datetime.datetime
        self.days = max(1, (self.enddate-self.startdate).days) # integer, at least 1
        self.items = gcaldict
        self.colorId = gcaldict.get('colorId', None)
        self.slug = self.slugify(gcaldict['summary'], self.days)
    def slugify(self, s, span):
        'Shorten a string according to available span'
        SLUGLENGTH=int(30*span)
        if len(s) < SLUGLENGTH:
            return s
        return u'%s..' % s[:SLUGLENGTH]
    def multiple_days(self):
        return self.days > 1
    def multiple_months(self):
        return self.startdate.month != self.enddate.month and self.enddate.day > 1

class YearCalendar(calendar.Calendar):
    "Super Class of calendar.Calendar to display a year with events"
    def __init__(self, cal_id, events, firstweekday=None):
        super(YearCalendar, self).__init__(firstweekday=firstweekday or 0) # 0 == Monday
        self.id = cal_id
        # logging.info('eents:%s', events)
        _e = []
        for e in events:
            E = Event(e)
            _e.append( (E.startdate, E) )
            if E.multiple_months():
                # we need to clone this event and display it next month, too
                # TODO: handle many monhts, not just next
                _new_startdate = E.enddate.replace(day=1) # start cloned event at first of enddate month
                _new_E = copy.deepcopy(E)
                _new_E.days = max(1, (_new_E.enddate-_new_startdate).days)
                _e.append( (_new_startdate, _new_E) )
        self.events = _e

    def iterdates(self, startdate=None, enddate=None):
        """iterate over all dates from startdate to enddate, defaulting to 1jan-31dec of current year.

        startdate and enddate can be None, datetime.date or dict instance from gcal

        """
        logging.info('iterdates: start %s - > end %s', startdate, enddate)
        _thisyear = datetime.datetime.now()
        if startdate is None:
            startdate = datetime.date(_thisyear, 1, 1)
        elif isinstance(startdate, dict):
            startdate = parse_date(startdate)
        if enddate is None:
            enddate = datetime.date(_thisyear, 12, 31)
        elif isinstance(enddate, dict):
            enddate = parse_date(enddate)

        h24 = datetime.timedelta(days=1)

        curdate = startdate
        while curdate < enddate:
            yield Date(curdate, self.get_events(curdate))
            curdate = curdate + h24

    def dates(self, startdate=None, enddate=None):
        "return all dates as a dict"
        _all = collections.OrderedDict({})
        for d in self.iterdates(startdate, enddate):
            # d is Date object
            try:
                _all[d.yearmonth].append(d)
            except KeyError:
                _all[d.yearmonth] = [d,]
        return _all

    def get_events(self, date):
        "return a list of all events for a specific datetime.date"
        evts = []
        for ev in self.events:
            # (datetime.date, data)
            if ev[0] == date:
                evts.append(ev[1])
        return evts

    def by_color(self, startdate=None, enddate=None):
        "return a dict of all events keyed by color"
        _r = {}
        for (evdate, e) in self.events:
            if startdate < evdate < enddate:
                try:
                    _r[e.colorId].append(e)
                except KeyError:
                    _r[e.colorId] = [e,]
        return _r


class CalListHandler(webapp2.RequestHandler):
    @decorator.oauth_aware
    def get(self):
        if decorator.has_credentials():
            cal_list = service.calendarList().list().execute(http=decorator.http())
            for c in cal_list['items']:
                # do we have a pretty title? Store it.
                #logging.info(c)
                try:
                    CPT = CalendarPrettyTitle.get_by_id(c['id'])
                    if CPT is None:
                        CPT = CalendarPrettyTitle(cal_id = c['id'],
                                                  id = c['id'])
                    if c.has_key('summaryOverride'):
                        CPT.pretty_title = c['summaryOverride']
                    elif not c['summary'].startswith('http'): # dont store urls
                        CPT.pretty_title = c['summary']
                    CPT.put()
                except Exception as e:
                    logging.exception(e)

            self.response.write(render_response('index.html', calendars=cal_list['items']))
        else:
            url = decorator.authorize_url()
            self.response.write(render_response('index.html', calendars=[], authorize_url=url))

class CalHandler(webapp2.RequestHandler):
    @decorator.oauth_aware
    def get(self, cal_id, startmonth=None, endmonth=None, **kwargs):
        #logging.info("got args: %s %s %s %s", cal_id, startmonth, endmonth, kwargs)
        if decorator.has_credentials():
            # get keywords or default values
            _thisyear = datetime.datetime.now().year
            try:
                startdate = datetime.datetime.strptime(startmonth, '%Y_%m').date()
            except (TypeError, ValueError):
                startdate = datetime.date(_thisyear, 1, 1)
            try:
                _end = datetime.datetime.strptime(endmonth, '-%Y_%m').date()
                enddate = _end.replace(month=_end.month+1) # stop at first of next month
            except (ValueError, TypeError):
                if startdate is not None:
                    enddate = datetime.date(startdate.year, 12, 31)
                else:
                    enddate = datetime.date(_thisyear, 12, 31)
            fields = kwargs.get('fields', 'description,items(colorId,creator,description,end,iCalUID,id,location,start,status,summary),nextPageToken,summary')
            #TODO: only list events from startdate to enddate, using timeMin & timeMax
            # https://developers.google.com/google-apps/calendar/v3/reference/events/list
            #   timeMin: string, Lower bound (inclusive) for an event's end time to filter by. Optional. The default is not to filter by end time. Must be an RFC3339 timestamp with mandatory time zone offset, e.g., 2011-06-03T10:00:00-07:00, 2011-06-03T10:00:00Z. Milliseconds may be provided but will be ignored.
            #   timeMax: string, Upper bound (exclusive) for an event's start time to filter by. Optional. The default is not to filter by start time. Must be an RFC3339 timestamp with mandatory time zone offset, e.g., 2011-06-03T10:00:00-07:00, 2011-06-03T10:00:00Z. Milliseconds may be provided but will be ignored.
            #TODO: add 'maxResults=2500' to make sure we get as much as we can
            #   maxResults: integer, Maximum number of events returned on one result page. By default the value is 250 events. The page size can never be larger than 2500 events. Optional.
            timeMin_dt = datetime.datetime.combine(startdate, datetime.datetime.min.time())
            timeMax_dt = datetime.datetime.combine(enddate, datetime.datetime.min.time()) + datetime.timedelta(days=1)
            cal_events = service.events().list(calendarId=cal_id,
                                               singleEvents=True,
                                               fields=fields,
                                               timeMin='%sZ' % timeMin_dt.isoformat(),
                                               timeMax='%sZ' % timeMax_dt.isoformat(),
                                               maxResults=2500,
                                               orderBy='startTime').execute(http=decorator.http())
            # TODO: use list_next(previous_request=*, previous_response=*) if there are more results pages
            #logging.info(cal_events)
            # try to get pretty title from db
            try:
                pretty_title = CalendarPrettyTitle.get_by_id(cal_id).pretty_title
            except AttributeError:
                # no pretty title recorded
                pretty_title = cal_id
            yc = YearCalendar(cal_id, cal_events['items'])
            months = ['Null', 'Januar', 'Februar', 'Mars', 'April', 'Mai', 'Juni', 'Juli',
                      'August', 'September', 'Oktober', 'November', 'Desember']
            self.response.write(render_response('calendar.html', title=pretty_title, calendar=yc, months=months,
                                                startdate=startdate, enddate=enddate))
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
        url = None
        self.response.write(render_response('index.html', calendars=[], authorize_url=url))

app = webapp2.WSGIApplication([
    ('/', MainHandler),
    ('/cals', CalListHandler),

    #('/cal/<cal_id:[^/]+>/<frommonth:\d{4}_\d{2}>-<tomonth:\d{4}_\d{2}>', CalHandler),
    #('/cal/<:.*>', CalHandler),
    (r'/cal/([^/]+)/(\d{4}_\d{2})(-\d{4}_\d{2})?', CalHandler),
    (r'/cal/([^/]+)', CalHandler),
    ('/getcolors', GetColorsHandler),
    ('/colors', ColorsHandler, 'colors'),
    ('/colors.css', ColorsCSSHandler, 'colors-css'),
    (decorator.callback_path, decorator.callback_handler()),

], debug=False)
