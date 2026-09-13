"""Business asset identifiers shared by recorded controls and live page navigation."""
import re
from urllib.parse import parse_qs, urlsplit


def context_ids(url):
    query = parse_qs(urlsplit(url).query)
    result = {key: query.get(key, [''])[0] for key in ('asset_id', 'business_id')}
    if not all(re.fullmatch(r'\d+', value) for value in result.values()):
        raise ValueError('发布页面缺少可核对的业务资产标识')
    return result
