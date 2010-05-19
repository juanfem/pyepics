#!/usr/bin/python
#  M Newville <newville@cars.uchicago.edu>
#  The University of Chicago, 2010
#  Epics Open License

"""
  Epics Process Variable
"""
import time
import sys
import math
import copy
from . import ca
from . import dbr

def fmt_time(ts=None):
    if ts is None:
        ts = time.time()
    ts, frac = divmod(ts, 1)
    return "%s.%6.6i" % (time.strftime("%Y-%m-%d %H:%M:%S",
                                       time.localtime(ts)), 1.e6*frac)

class PV(object):
    """== Epics Process Variable
    
    A PV encapsulates an Epics Process Variable.
   
    The primary interface methods for a pv are to get() and put() is value:
      >>>p = PV(pv_name)  # create a pv object given a pv name
      >>>p.get()          # get pv value
      >>>p.put(val)       # set pv to specified value. 

    Additional important attributes include:
      >>>p.pvname         # name of pv
      >>>p.value          # pv value (can be set or get)
      >>>p.char_value     # string representation of pv value
      >>>p.count          # number of elements in array pvs
      >>>p.type           # EPICS data type: 'string','double','enum','long',..
"""

    _fmt="<PV '%(pvname)s', count=%(count)i, type=%(type)s, access=%(access)s>"
    _fields = ('pvname',  'value',  'char_value',  'status',  'ftype',  'chid',
               'host', 'count', 'access', 'write_access', 'read_access',
               'severity', 'timestamp', 'precision', 'units', 'enum_strs',
               'upper_disp_limit', 'lower_disp_limit', 'upper_alarm_limit',
               'lower_alarm_limit', 'lower_warning_limit',
               'upper_warning_limit', 'upper_ctrl_limit', 'lower_ctrl_limit')

    def __init__(self, pvname, callback=None, form='native',
                 verbose=False, auto_monitor=True):
        self.pvname     = pvname.strip()
        self.form       = form.lower()
        self.verbose    = verbose
        self.auto_monitor = auto_monitor
        self.ftype      = None
        self.connected  = False
        self._args      = {}.fromkeys(self._fields)
        self._args['pvname'] = self.pvname
        self._args['count'] = -1
        self._args['type'] = 'unknown'
        self._args['access'] = 'unknown'

        self.callbacks  = {}

        self._monref = None  # holder of data returned from create_subscription
        self.chid = None

        # get current thread context to use for ca._cache
        ctx = ca.current_context()
        if ctx not in ca._cache: ca._cache[ctx] = {}
        if self.pvname in ca._cache[ctx]:
            entry = ca._cache[ctx][pvname]
            self.chid = entry['chid']
            self._onConnect(chid=self.chid, conn=entry['conn'])
        if self.chid is None:
            self.chid = ca.create_channel(self.pvname,
                                          userfcn=self._onConnect)

        self._args['chid'] = self.chid
        self._args['type'] = dbr.Name(ca.field_type(self.chid)).lower()
        if callback is not None:
            self.connect()
            self.add_callback(callback)

    def _write(self, msg):
        sys.stdout.write("%s\n" % msg)
    
    def _onConnect(self, chid=0, conn=True, **kw):
        # occassionally chid is still None (threading issue???)
        # just return here, and connection will be forced later
        if self.chid is None: return
        
        self.connected = conn
        if self.connected:
            self._args['host']   = ca.host_name(self.chid)
            self._args['count']  = ca.element_count(self.chid)
            self._args['access'] = ca.access(self.chid)
            self._args['read_access'] = (1 == ca.read_access(self.chid))
            self._args['write_access'] = (1 == ca.write_access(self.chid))
            self.ftype  = ca.promote_type(self.chid,
                                     use_ctrl= self.form == 'ctrl',
                                     use_time= self.form == 'time')
            self._args['type'] = dbr.Name(self.ftype).lower()
        return


    def connect(self, timeout=5.0, force=True):
        if not self.connected:
            ca.connect_channel(self.chid, timeout=timeout, force=force)
            self.poll()
        # should be only be called 1st time, to subscribe
        # and set self._monref
        count = ca.element_count(self.chid)
        if (self._monref is None and self.connected and
            self.auto_monitor  and count < ca.AUTOMONITOR_MAXLENGTH):
            self._monref = ca.create_subscription(self.chid,
                                        userfcn=self._onChanges,
                                        use_ctrl=(self.form == 'ctrl'),
                                        use_time=(self.form == 'time'))

        if  self._args['ftype'] is None and self._args['type'] is not None:
            self._args['ftype'] = dbr.Name(self._args['type'], reverse=True)

        return (self.connected and self.ftype is not None)

    def poll(self, evt=1.e-4, iot=1.0):
        ca.poll(evt=evt, iot=iot)

    def get(self, as_string=False, as_numpy=True):
        """returns current value of PV
        use argument 'as_string=True' to return string representation

        >>> p.get('13BMD:m1.DIR')
        0
        >>> p.get('13BMD:m1.DIR',as_string=True)
        'Pos'
        """
        if not self.connect(force=False):
            return None
        
        self._args['value'] = ca.get(self.chid, ftype=self.ftype, as_numpy=as_numpy)
        self.poll() 
        field = 'value'
        if as_string:
            self._set_charval(self._args['value'])
            field = 'char_value'
        return self._args[field]

    def put(self, value, wait=False, timeout=30.0,
            callback=None, callback_data=None):
        """set value for PV, optionally waiting until the processing is
        complete, and optionally specifying a callback function to be run
        when the processing is complete.        
        """
        if not self.connect(force=False):
            return None
        if (self.ftype in (dbr.ENUM, dbr.TIME_ENUM, dbr.CTRL_ENUM) and
            isinstance(value, str) and value in self._args['enum_strs']):
            value = self._args['enum_strs'].index(value)
        
        return ca.put(self.chid, value,
                      wait=wait, timeout=timeout,
                      callback=callback, callback_data=callback_data)

    def _set_charval(self, val, call_ca=True):
        """ sets the character representation of the value.
        intended only for internal use"""
        ftype = self._args['ftype']
        if ftype == dbr.STRING:
            self._args['char_value'] = val
            return val
        cval  = repr(val)       
        if self._args['count'] > 1:
            if ftype == dbr.CHAR:
                val = list(val)
                firstnull  = val.index(0)
                if firstnull < 0: firstnull = len(val)
                cval = ''.join([chr(i) for i in val[:firstnull]]).rstrip()
            else:
                cval = '<array size=%d, type=%s>' % (len(val),
                                                     dbr.Name(ftype))
        elif ftype in (dbr.FLOAT, dbr.DOUBLE):
            if call_ca and self._args['precision'] is None:
                self.get_ctrlvars()
            try: 
                fmt  = "%%.%if"
                if 4 < abs(int(math.log10(abs(val + 1.e-9)))):
                    fmt = "%%.%ig"
                cval = (fmt %  self._args.get('precision', 0)) % val
            except:
                pass 
        elif ftype == dbr.ENUM:
            if call_ca and self._args['enum_strs'] in ([], None):
                self.get_ctrlvars()
            try:
                cval = self._args['enum_strs'][val]
            except (TypeError, KeyError,  IndexError):
                pass
        self._args['char_value'] = cval
        return cval
    
    def get_ctrlvars(self):
        ""
        if not self.connect(force=False):
            return None
        kw = ca.get_ctrlvars(self.chid)
        ca.poll()
        self._args.update(kw)
        return kw

    def _onChanges(self, value=None, **kw):
        """internal callback function: do not overwrite!!
        To have user-defined code run when the PV value changes,
        use add_callback()
        """
        self._args.update(kw)
        self._args['value']  = value
        self._args['timestamp'] = kw.get('timestamp', time.time())
        self._set_charval(self._args['value'], call_ca=False)

        if self.verbose:
            now = fmt_time(self._args['timestamp'])
            self._write('%s: %s (%s)'% (self.pvname,
                                        self._args['char_value'],
                                        now))
        self.run_callbacks()
        
    def run_callbacks(self):
        """run all user-defined callbacks with the current data

        Normally, this is to be run automatically on event, but
        it is provided here as a separate function for testing
        purposes.

        Note that callback functions are called with keyword/val
        arguments including:
             self._args  (all PV data available, keys = __fields)
             keyword args included in add_callback()
             keyword 'cb_info' = (index, remove_callback)
        where the 'cb_info' is provided as a hook so that a callback
        function  that fails may de-register itself (for example, if
        a GUI resource is no longer available).
             
        """
        for index in sorted(self.callbacks.keys()):
            fcn, kwargs = self.callbacks[index]
            kw = copy.copy(self._args)
            kw.update(kwargs)
            kw['cb_info'] = (index, self.remove_callback)
            if hasattr(fcn, '__call__'):
                fcn(**kw)
            
    def add_callback(self, callback=None, **kw):
        """add a callback to a PV.  Optional keyword arguments
        set here will be preserved and passed on to the callback
        at runtime.

        Note that a PV may have multiple callbacks, so that each
        has a unique index (small integer) that is returned by
        add_callback.  This index is needed to remove a callback."""
        if not self.connected:
            self.connect(force=False)
        index = None
        if hasattr(callback, '__call__'):
            n_cb = len(self.callbacks)
            index = 1
            if n_cb > 1:  index = 1 + max(self.callbacks.keys())
            self.callbacks[index] = (callback, kw)
        return index
    
    def remove_callback(self, index=None):
        """remove a callback.
        """
        if index is None and len(self.callbacks)==1:
            index = list(self.callbacks.keys())[0]
        if index in self.callbacks:
            self.callbacks.pop(index)
            self.poll()

    def clear_callbacks(self, **kw):
        self.callbacks = {}

    def _getinfo(self):
        if not self.connect(force=False):
            return None
        if self._args['precision'] is None:
            self.get_ctrlvars()

        # list basic attributes
        out = []
        mod = 'native'
        xtype = self._args['type']
        if '_' in xtype:
            mod, xtype = xtype.split('_')

        out.append("== %s  (%s) ==" % (self.pvname, xtype))
        if self.count == 1:
            val = self._args['value']
            fmt = '%i'
            if   xtype in ('float','double'): fmt = '%g'
            elif xtype in ('string','char'):  fmt = '%s'
            out.append('   value      = %s' % fmt % val)
        else:
            aval, ext, fmt = [], '', "%i,"
            if self.count > 5:
                ext = '...'
            if xtype in  ('float','double'): fmt = "%g,"
            for i in range(min(5, self.count)):
                aval.append(fmt % self._args['value'][i])
            out.append("   value      = array  [%s%s]" % ("".join(aval),
                                                          ext))

        for i in ('char_value', 'count', 'type', 'units',
                  'precision', 'host', 'access',
                  'status', 'severity', 'timestamp',
                  'upper_ctrl_limit', 'lower_ctrl_limit',
                  'upper_disp_limit', 'lower_disp_limit',
                  'upper_alarm_limit', 'lower_alarm_limit',
                  'upper_warning_limit', 'lower_warning_limit'):
            if hasattr(self, i):
                att = getattr(self, i)
                if att is not None:
                    if i == 'timestamp':
                        att = "%.3f (%s)" % (att, fmt_time(att))
                    if len(i) < 12:
                        out.append('   %.11s= %s' % (i+' '*12, str(att)))
                    else:
                        out.append('   %.20s= %s' % (i+' '*20, str(att)))

        if xtype == 'enum':  # list enum strings
            out.append('   enum strings: ')
            for index, estr in enumerate(self.enum_strs):
                out.append("       %i = %s " % (index, estr))

        if self._monref is not None:
            msg = 'PV is internally monitored'
            out.append('   %s, with %i user-defined callbacks:' % (msg,
                                                         len(self.callbacks)))

            if len(self.callbacks) > 0:
                cblist = list(self.callbacks.keys())
                cblist.sort()
                for i in cblist:
                    cb = self.callbacks[i][0]
                    cb_name =  cb.func_name
                    cb_file = cb.func_code.co_filename
                    out.append('      %s in file %s' % (cb_name, cb_file))
        else:
            out.append('   PV is NOT internally monitored')
        out.append('=============================')
        return '\n'.join(out)
        
    def _getarg(self, arg):
        if self._args['value'] is None:  self.get()
        return self._args.get(arg, None)
        
    def __getval__(self):
        return self._getarg('value')
    def __setval__(self, v):
        return self.put(v)
    value = property(__getval__, __setval__, None, "value property")

    @property
    def char_value(self):
        "character string representation of value"
        return self._getarg('char_value')

    @property
    def status(self):
        "pv status"
        return self._getarg('status')

    @property
    def type(self):
        "pv type"
        return self._args['type']

    @property
    def host(self):
        "pv host"
        return self._getarg('host')

    @property
    def count(self):
        "count (number of elements)"
        return self._getarg('count')

    @property
    def read_access(self):
        "read access"
        return self._getarg('read_access')

    @property
    def write_access(self):
        "write access"
        return self._getarg('write_access')

    @property
    def access(self):
        "read/write access as string"
        return self._getarg('access')

    @property
    def severity(self):
        "pv severity"
        return self._getarg('severity')

    @property
    def timestamp(self):
        "timestamp of last pv action"
        return self._getarg('timestamp')

    @property
    def precision(self):
        "number of digits after decimal point"
        return self._getarg('precision')

    @property
    def units(self):
        "engineering units for pv"
        return self._getarg('units')

    @property
    def enum_strs(self):
        "list of enumeration strings"
        return self._getarg('enum_strs')

    @property
    def upper_disp_limit(self):
        "limit"
        return self._getarg('upper_disp_limit')

    @property
    def lower_disp_limit(self):
        "limit"
        return self._getarg('lower_disp_limit')

    @property
    def upper_alarm_limit(self):
        "limit"
        return self._getarg('upper_alarm_limit')

    @property
    def lower_alarm_limit(self):
        "limit"
        return self._getarg('lower_alarm_limit')

    @property
    def lower_warning_limit(self):
        "limit"
        return self._getarg('lower_warning_limit')

    @property
    def upper_warning_limit(self):
        "limit"
        return self._getarg('upper_warning_limit')

    @property
    def upper_ctrl_limit(self):
        "limit"
        return self._getarg('upper_ctrl_limit')

    @property
    def lower_ctrl_limit(self):
        "limit"
        return self._getarg('lower_ctrl_limit')

    @property
    def info(self):
        "info string"
        return self._getinfo()

    def __repr__(self):
        ""
        if self.connected:
            return self._fmt % self._args
        else:
            return "<PV '%s': not connected>" % self.pvname
    
    def __str__(self):
        ""
        return self.__repr__()

    def __eq__(self, other):
        ""
        try:
            return (self.chid  == other.chid)
        except:
            return False

    def disconnect(self):
        ""
        self.connected = False
        self.callbacks = {}
        if self._monref is not None:
            cb, uarg, evid = self._monref
            ca.clear_subscription(evid)
            del cb
            del uarg
            del evid
        ca.poll()
        
    def __del__(self):
        self.disconnect()
