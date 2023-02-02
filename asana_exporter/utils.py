import threading

LOCK = threading.Lock()


def with_lock(f):
    def with_lock_inner(*args, **kwargs):
        with LOCK:
            return f(*args, **kwargs)

    return with_lock_inner


def required(opts):
    """
    @param opts: dict where key is attr name and val is opt name.
    """
    def _required(f):
        def _inner_required(self, *args, **kwargs):
            has = all([hasattr(self, o) for o in opts])
            if not has or not all([getattr(self, o) for o in opts]):
                msg = ("one or more of the following required options have "
                       "not been provided: {}".
                       format(', '.join(opts.values())))
                raise Exception(msg)
            return f(self, *args, **kwargs)

        return _inner_required
    return _required


class Logger(object):

    def __init__(self):
        self._debug = False

    def debug(self, msg):
        if self._debug:
            print("DEBUG: {}".format(msg))

    def info(self, msg):
        print("INFO: {}".format(msg))

    def warning(self, msg):
        print("WARNING: {}".format(msg))

    def error(self, msg):
        print("ERROR: {}".format(msg))

    def set_level(self, level):
        if level == 'debug':
            self._debug = True
        else:
            self._debug = False


LOG = Logger()
