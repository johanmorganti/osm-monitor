"""One way to talk to OSM's servers: a session that identifies the app and retries
the connection failures that are routine on a long-lived poller."""
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

USER_AGENT = 'osm-monitor (https://github.com/johanmorganti/osm-monitor)'


def new_session():
    """A requests.Session with our User-Agent. A pooled keep-alive connection
    the server closed while the poller slept (RemoteDisconnected), a refused or
    timed-out connect and a timed-out read are retried up to 3 times with a
    growing pause, GET only. HTTP error statuses are not: the callers decide
    (reconcile waits out the OSM API's 429/503 for much longer than this)."""
    session = requests.Session()
    session.headers['User-Agent'] = USER_AGENT
    retry = Retry(total=3, connect=3, read=3, status=0, backoff_factor=2, allowed_methods=['GET'])
    session.mount('https://', HTTPAdapter(max_retries=retry))
    session.mount('http://', HTTPAdapter(max_retries=retry))
    return session
