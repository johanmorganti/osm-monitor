import os

# Browser SDK CDN region per Datadog site.
_SDK_REGIONS = {
    'datadoghq.com': 'us1', 'us3.datadoghq.com': 'us3', 'us5.datadoghq.com': 'us5',
    'datadoghq.eu': 'eu1', 'ap1.datadoghq.com': 'ap1', 'ap2.datadoghq.com': 'ap2',
}


def datadog_rum(request):
    """Datadog RUM browser-SDK settings for base.html, from the environment.
    Optional: RUM is only loaded when both the application ID and the client
    token are set (see env.example / docker-compose.datadog.yml)."""
    application_id = os.environ.get('DD_RUM_APPLICATION_ID')
    client_token = os.environ.get('DD_RUM_CLIENT_TOKEN')
    if not (application_id and client_token):
        return {}
    config = {
        'applicationId': application_id,
        'clientToken': client_token,
        'site': os.environ.get('DD_SITE', 'datadoghq.com'),
        'service': os.environ.get('DD_RUM_SERVICE', 'osm-monitor-frontend'),
        'env': os.environ.get('DD_ENV', 'production'),
        'version': os.environ.get('DD_VERSION', 'unknown'),
        'sessionSampleRate': float(os.environ.get('DD_RUM_SESSION_SAMPLE_RATE', 100)),
        'sessionReplaySampleRate': float(os.environ.get('DD_RUM_SESSION_REPLAY_SAMPLE_RATE', 20)),
        'trackResources': True,
        'trackUserInteractions': True,
        'trackLongTasks': True,
        'defaultPrivacyLevel': 'mask-user-input',
    }
    # Remote configuration makes init asynchronous: RUM (and trace-header
    # injection) only starts once the config has been fetched, so the API
    # calls the dashboard fires at page load go out untraced and lose their
    # RUM <-> APM link. Only set it if the remote config is worth that.
    if os.environ.get('DD_RUM_REMOTE_CONFIGURATION_ID'):
        config['remoteConfiguration'] = {'id': os.environ['DD_RUM_REMOTE_CONFIGURATION_ID']}
    if os.environ.get('DD_RUM_PROXY_URL'):
        # Send intake (and load the SDK, below) through a first-party domain
        # instead of Datadog's, which ad blockers block.
        config['proxy'] = os.environ['DD_RUM_PROXY_URL']
    if config.get('proxy'):
        # The proxy also serves the SDK itself at /sdk.js, so the script
        # isn't blocked either (Datadog's CDN domain is on blocklists).
        sdk_url = config['proxy'].rstrip('/') + '/sdk.js'
    else:
        region = _SDK_REGIONS.get(config['site'], 'us1')
        sdk_url = f'https://www.datadoghq-browser-agent.com/{region}/v7/datadog-rum.js'
    return {'dd_rum_config': config, 'dd_rum_sdk_url': sdk_url}
