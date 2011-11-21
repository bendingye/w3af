"""
reqResViewer.py

Copyright 2008 Andres Riancho

This file is part of w3af, w3af.sourceforge.net .

w3af is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation version 2 of the License.

w3af is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with w3af; if not, write to the Free Software
Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA

"""
# For invoking the plugins
import threading
# Signal handler to handle SIGSEGV generated by gtkhtml2
import signal

import gtk
import gobject

from core.ui.gtkUi.entries import RememberingVPaned
from core.ui.gtkUi.entries import RememberingWindow
from core.ui.gtkUi.entries import SemiStockButton
# To show request and responses
from core.ui.gtkUi.httpeditor import HttpEditor
from core.data.db.history import HistoryItem
from core.data.constants import severity
from core.data.parsers.httpRequestParser import httpRequestParser
from core.data.dc.queryString import queryString

from core.controllers.w3afException import w3afException, w3afMustStopException, w3afMustStopOnUrlError

import core.controllers.outputManager as om
from .export_request import export_request
# import the throbber for the audit plugin analysis
from . import helpers
from core.ui.gtkUi.rrviews.raw import HttpRawView
from core.ui.gtkUi.rrviews.headers import HttpHeadersView
from core.ui.gtkUi.rrviews.rendering import getRenderingView

def sigsegv_handler(signum, frame):
    print _('This is a catched segmentation fault!')
    print _('I think you hitted bug #1933524 , this is mainly a gtkhtml2 problem. Please report this error here:')
    print _('https://sourceforge.net/tracker/index.php?func=detail&aid=1933524&group_id=170274&atid=853652')
signal.signal(signal.SIGSEGV, sigsegv_handler)
# End signal handler

class reqResViewer(gtk.VBox):
    '''
    A widget with the request and the response inside.

    @author: Andres Riancho ( andres.riancho@gmail.com )
    @author: Facundo Batista ( facundo@taniquetil.com.ar )

    '''
    def __init__(self, w3af, enableWidget=None, withManual=True, withFuzzy=True,
                        withCompare=True, withAudit=True, editableRequest=False, editableResponse=False,
                        widgname="default", layout='Tabbed'):
        super(reqResViewer,self).__init__()
        self.w3af = w3af
        # Request
        self.request = requestPart(w3af, enableWidget, editableRequest, widgname=widgname)
        self.request.show()
        # Response
        self.response = responsePart(w3af, editableResponse, widgname=widgname)
        self.response.show()
        self.layout = layout
        if layout == 'Tabbed':
            self._initTabbedLayout()
        else:
            self._initSplittedLayout()
        # Init req toolbox
        self._initToolBox(withManual, withFuzzy, withCompare, withAudit)
        self.show()

    def _initTabbedLayout(self):
        '''Init Tabbed layout. It's more convenient for quick view.'''
        nb = gtk.Notebook()
        nb.show()
        self.nb = nb
        self.pack_start(nb, True, True)
        nb.append_page(self.request, gtk.Label(_("Request")))
        nb.append_page(self.response, gtk.Label(_("Response")))
        # Info
        self.info = HttpEditor(self.w3af)
        self.info.set_editable(False)
        #self.info.show()
        nb.append_page(self.info, gtk.Label(_("Info")))

    def _initSplittedLayout(self):
        '''Init Splitted layout. It's more convenient for intercept.'''
        self._vpaned = RememberingVPaned(self.w3af, 'trap_view')
        self._vpaned.show()
        self.pack_start(self._vpaned, True, True)
        self._vpaned.add(self.request)
        self._vpaned.add(self.response)

    def focusResponse(self):
        if self.layout == 'Tabbed':
            self.nb.set_current_page(1)

    def focusRequest(self):
        if self.layout == 'Tabbed':
            self.nb.set_current_page(0)

    def _initToolBox(self, withManual, withFuzzy, withCompare, withAudit):
        # Buttons
        hbox = gtk.HBox()
        if withManual or withFuzzy or withCompare:
            from .craftedRequests import ManualRequests, FuzzyRequests
            
            if withManual:
                b = SemiStockButton("", gtk.STOCK_INDEX, _("Send Request to Manual Editor"))
                b.connect("clicked", self._sendRequest, ManualRequests)
                self.request.childButtons.append(b)
                b.show()
                hbox.pack_start(b, False, False, padding=2)
            if withFuzzy:
                b = SemiStockButton("", gtk.STOCK_PROPERTIES, _("Send Request to Fuzzy Editor"))
                b.connect("clicked", self._sendRequest, FuzzyRequests)
                self.request.childButtons.append(b)
                b.show()
                hbox.pack_start(b, False, False, padding=2)
            if withCompare:
                b = SemiStockButton("", gtk.STOCK_ZOOM_100, _("Send Request and Response to Compare Tool"))
                b.connect("clicked", self._sendReqResp)
                self.response.childButtons.append(b)
                b.show()
                hbox.pack_start(b, False, False, padding=2)
        # I always can export requests
        b = SemiStockButton("", gtk.STOCK_COPY, _("Export Request"))
        b.connect("clicked", self._sendRequest, export_request)
        self.request.childButtons.append(b)
        b.show()
        hbox.pack_start(b, False, False, padding=2)
        self.pack_start(hbox, False, False, padding=5)
        hbox.show()

        if withAudit:
            # Add everything I need for the audit request thing:
            # The button that shows the menu
            b = SemiStockButton("", gtk.STOCK_EXECUTE, _("Audit Request with..."))
            b.connect("button-release-event", self._popupMenu)
            self.request.childButtons.append(b)
            b.show()
            hbox.pack_start(b, False, False, padding=2)
        # The throbber (hidden!)
        self.throbber = helpers.Throbber()
        hbox.pack_start(self.throbber, True, True)
        
        self.pack_start(hbox, False, False, padding=5)
        hbox.show()

    def _popupMenu(self, widget, event):
        '''Show a Audit popup menu.'''
        _time = event.time
        # Get the information about the click
        #requestId = self._lstore[path][0]
        # Create the popup menu
        gm = gtk.Menu()
        pluginType = "audit"
        for pluginName in sorted(self.w3af.getPluginList(pluginType)):
            e = gtk.MenuItem(pluginName)
            e.connect('activate', self._auditRequest, pluginName, pluginType)
            gm.append(e)
        # Add a separator
        gm.append(gtk.SeparatorMenuItem())
        # Add a special item
        e = gtk.MenuItem('All audit plugins')
        e.connect('activate', self._auditRequest, 'All audit plugins',
                'audit_all')
        gm.append(e)
        # show
        gm.show_all()
        gm.popup(None, None, None, event.button, _time)

    def _auditRequest(self, menuItem, pluginName, pluginType):
        """
        Audit a request using one or more plugins.

        @parameter menuItem: The name of the audit plugin, or the 'All audit plugins' wildcard
        @parameter pluginName: The name of the plugin
        @parameter pluginType: The type of plugin
        @return: None
        """
        # We show a throbber, and start it
        self.throbber.show()
        self.throbber.running(True)
        request = self.request.getObject()
        # Now I start the analysis of this request in a new thread,
        # threading game (copied from craftedRequests)
        event = threading.Event()
        impact = ThreadedURLImpact(self.w3af, request, pluginName, pluginType, event)
        impact.start()
        gobject.timeout_add(200, self._impactDone, event, impact)

    def _impactDone(self, event, impact):
        # Keep calling this from timeout_add until isSet
        if not event.isSet():
            return True
        # We stop the throbber, and hide it
        self.throbber.hide()
        self.throbber.running(False)
        # Analyze the impact
        if impact.ok:
            #   Lets check if we found any vulnerabilities
            #
            #   TODO: I should actually show ALL THE REQUESTS generated by audit plugins...
            #               not just the ones with vulnerabilities.
            #
            for result in impact.result:
                
                # TODO: I'm not sure when this is None bug it appeared in Trac bug #167736
                if result.getId() is not None:
                    for itemId in result.getId():
                        historyItem = HistoryItem()
                        historyItem.load(itemId)
                        historyItem.updateTag(historyItem.tag + result.plugin_name)
                        historyItem.info = result.getDesc()
                        historyItem.save()
        else:
            if impact.exception.__class__ == w3afException:
                msg = str(impact.exception)
            elif impact.exception.__class__ == w3afMustStopException:
                msg = "Stopped sending requests because " + str(impact.exception)
            elif impact.exception.__class__ == w3afMustStopOnUrlError:
                msg = "Not sending requests because " + str(impact.exception)
            else:
                raise impact.exception
            # We stop the throbber, and hide it
            self.throbber.hide()
            self.throbber.running(False)
            gtk.gdk.threads_enter()
            helpers.friendlyException(msg)
            gtk.gdk.threads_leave()
        return False

    def _sendRequest(self, widg, func):
        """Sends the texts to the manual or fuzzy request.

        @param func: where to send the request.
        """
        headers,data = self.request.getBothTexts()
        func(self.w3af, (headers,data))

    def _sendReqResp(self, widg):
        """Sends the texts to the compare tool."""
        headers,data = self.request.getBothTexts()
        self.w3af.mainwin.commCompareTool((headers, data,\
            self.response.getObject()))

    def set_sensitive(self, how):
        """Sets the pane on/off."""
        self.request.set_sensitive(how)
        self.response.set_sensitive(how)

class requestResponsePart(gtk.Notebook):
    """Request/response common class."""
    
    def __init__(self, w3af, enableWidget=[], editable=False, widgname="default"):
        super(requestResponsePart, self).__init__()
        self._obj = None
        ### FIXME: REMOVE ME ###
        self._set_vals = (False, 'default_val')
        #######################
        self.w3af = w3af
        self.childButtons = []
        self._views = []
        self.enableWidget = enableWidget
        
    def addView(self, view):
        self._views.append(view)
        self.append_page(view, gtk.Label(view.label))

    def show(self):
        for view in self._views:
            view.show()
        super(requestResponsePart, self).show()

    def set_sensitive(self, how):
        super(requestResponsePart, self).set_sensitive(how)
        for but in self.childButtons:
            but.set_sensitive(how)

    def getViewById(self, viewId):
        for view in self._views:
            if view.id == viewId:
                return view
        return None

    def synchronize(self, viewId=None):
        for view in self._views:
            if view.id != viewId:
                view.initial = True
                view.showObject(self._obj)
                view.initial = False
        if self.enableWidget:
            for widg in self.enableWidget:
                widg(bool(len(self._obj.getHeaders())))

    def clearPanes(self):
        self._obj = None
        for view in self._views:
            view.initial = True
            view.clear()
            view.initial = False

    def showError(self, text):
        print text

    def showObject(self, obj):
        self._obj = obj
        ### FIXME: REMOVE ME ###
        self._set_vals = (True, obj)
        #######################
        self.synchronize()

    def setObject(self, obj):
        self._obj = obj
        ### FIXME: REMOVE ME ###
        self._set_vals = (True, obj)
        #######################
        
    def getObject(self):
        return self._obj

    def highlight(self, text, sev=severity.MEDIUM):
        for view in self._views:
            view.highlight(text, sev)

class requestPart(requestResponsePart):
    
    def __init__(self, w3af, enableWidget=[], editable=False, widgname="default"):
        requestResponsePart.__init__(self, w3af, enableWidget,editable, widgname=widgname+"request")
        self.addView(HttpRawView(w3af, self, editable))
        self.addView(HttpHeadersView(w3af, self, editable))
        
    def getBothTexts(self):
        try:
            data = ''
            raise AttributeError
            if self._obj.getData():
                data = str(self._obj.getData())
            return (self._obj.dumpRequestHead(), data)
        except AttributeError: ### FIXME: REMOVE ME ###
            msg = ("DEBUG_EXCEPTION: NoneType object has no attribute "
                   "'getData': Was object tried to set?? %s. Value: '%r'" % 
                   self._set_vals)
            raise AttributeError, msg
    
    def showRaw(self, head, body):
        self._obj = httpRequestParser(head, body)
        ### FIXME: REMOVE ME ###
        self._set_vals = (True, self._obj)
        #######################        
        self.synchronize()

class responsePart(requestResponsePart):
    def __init__(self, w3af, editable, widgname="default"):
        requestResponsePart.__init__(self, w3af, editable=editable, widgname=widgname+"response")
        http = HttpRawView(w3af, self, editable)
        http.is_request = False
        self.addView(http)
        headers = HttpHeadersView(w3af, self, editable)
        headers.is_request = False
        self.addView(headers)
        try:
            rend = getRenderingView(w3af, self)
            self.addView(rend)
        except Exception, ex:
            print ex

    def getBothTexts(self):
        return (self._obj.dumpResponseHead(), str(self._obj.getBody()))

class reqResWindow(RememberingWindow):
    """
    A window to show a request/response pair.
    """
    def __init__(self, w3af, request_id, enableWidget=None, withManual=True,
                 withFuzzy=True, withCompare=True, withAudit=True, editableRequest=False,
                 editableResponse=False, widgname="default"):
        # Create the window
        RememberingWindow.__init__(
            self, w3af, "reqResWin", _("w3af - HTTP Request/Response"), "Browsing_the_Knowledge_Base")

        # Create the request response viewer
        rrViewer = reqResViewer(w3af, enableWidget, withManual, withFuzzy, withCompare, withAudit, editableRequest, editableResponse, widgname)

        # Search the id in the DB
        historyItem = HistoryItem()
        historyItem.load(request_id)
        # Set
        rrViewer.request.showObject( historyItem.request )
        rrViewer.response.showObject( historyItem.response )
        rrViewer.show()
        self.vbox.pack_start(rrViewer)

        # Show the window
        self.show()

class ThreadedURLImpact(threading.Thread):
    '''Impacts an URL in a different thread.'''
    def __init__(self, w3af, request, pluginName, pluginType, event):
        '''Init ThreadedURLImpact.'''
        self.w3af = w3af
        self.request = request
        self.pluginName = pluginName
        self.pluginType = pluginType
        self.event = event
        self.result = []
        self.ok = False
        threading.Thread.__init__(self)

    def run(self):
        '''Start the thread.'''
        try:
            # First, we check if the user choosed 'All audit plugins'
            if self.pluginType == 'audit_all':
                
                #
                #   Get all the plugins and work with that list
                #
                for pluginName in self.w3af.getPluginList('audit'):
                    plugin = self.w3af.getPluginInstance(pluginName, 'audit')
                    tmp_result = []
                    try:
                        tmp_result = plugin.audit_wrapper(self.request)
                        plugin.end()
                    except w3afException, e:
                        om.out.error(str(e))
                    else:
                        #
                        #   Save the plugin that found the vulnerability in the result
                        #
                        for r in tmp_result:
                            r.plugin_name = pluginName
                        self.result.extend(tmp_result)

                
            else:
                #
                #   Only one plugin was enabled
                #
                plugin = self.w3af.getPluginInstance(self.pluginName, self.pluginType)
                try:
                    self.result = plugin.audit_wrapper(self.request)
                    plugin.end()
                except w3afException, e:
                    om.out.error(str(e))
                else:
                    #
                    #   Save the plugin that found the vulnerability in the result
                    #
                    for r in self.result:
                        r.plugin_name = self.pluginName
            
            #   We got here, everything is OK!
            self.ok = True
            
        except Exception, e:
            self.exception = e
            #
            #   This is for debugging errors in the audit button of the reqResViewer
            #
            #import traceback
            #print traceback.format_exc()
        finally:
            self.event.set()
