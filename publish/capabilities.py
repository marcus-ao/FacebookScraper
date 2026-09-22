"""One read-only capability contract for preflight, activation and publication."""
from datetime import datetime

from core.config import ROOT, cfg
from publish import business_suite as bs, channels, evidence, journal, planning, snapshots, month_inventory


@evidence.validation_scope()
def checks(channel=None):
    if channel not in {None, 'facebook', 'instagram'}:
        raise bs.ProbeRequired('每篇仅接受一个来源对应的发布渠道')
    selected = (channel,) if channel else ('facebook', 'instagram')
    # Config.state_dir creates directories; a preflight on a fresh checkout must not.
    if not (ROOT / cfg().get('paths', 'state', 'state')).is_dir():
        return [{'name': 'state_directory', 'available': False, 'reason': '运行状态目录尚未建立，缺少本机验收证据'}]
    operations = [('browser_isolation', cfg().assert_publish_chrome_isolated),
                  ('account_context', bs.require_account_context_evidence),
                  ('submission', bs.require_submission_evidence),
                  ('readback', bs.require_readback_evidence),
                  ('calendar_coverage', month_inventory.require)]
    operations.extend((f'{channel}_controls', lambda channel=channel:
                       channels.require_independent_channel_evidence((channel,)))
                      for channel in selected)
    operations.extend((f'{channel}_window', lambda channel=channel: planning.configured_window(channel))
                      for channel in selected)
    result = []
    for name, operation in operations:
        try:
            operation()
            result.append({'name': name, 'available': True, 'reason': ''})
        except (Exception, SystemExit) as exc:
            result.append({'name': name, 'available': False, 'reason': str(exc).splitlines()[0]})
    return result


def require(channel):
    if channel not in {'facebook', 'instagram'}:
        raise bs.ProbeRequired('每篇仅接受一个来源对应的发布渠道')
    for item in checks(channel):
        if not item['available']:
            raise bs.ProbeRequired(item['reason'])


def acceptance(state_dir):
    """Historic combined records retain dedupe power but do not prove the new mode."""
    latest = {row['attempt_id']: row for row in journal.load(state_dir)}
    accepted = {}
    for channel in ('facebook', 'instagram'):
        candidates = []
        for row in latest.values():
            if (row['status'] != journal.STATUS_SCHEDULED or row.get('target_channels') != [channel]
                    or row.get('channels_verified') != [channel] or not row.get('snapshot_id')
                    or not row.get('source_fingerprint') or not row.get('remote_id')
                    or not row.get('ui_readback') or not row.get('readback_signal')):
                continue
            diagnostics = row.get('readback_diagnostics') or {}
            if not isinstance(diagnostics, dict):
                continue
            media = diagnostics.get('remote_media') or {}
            if not isinstance(media, dict):
                continue
            images = media.get('images')
            if (type(media.get('image_count')) is not int or media['image_count'] < 1
                    or not isinstance(images, list) or not all(isinstance(image, dict) for image in images)):
                continue
            if (diagnostics.get('full_caption_equal') is not True
                    or diagnostics.get('remote_images_verified') is not True
                    or media.get('order_verified') is not True
                    or media.get('image_count') != len(row.get('image_sha256') or ())
                    or [image.get('source_sha256') for image in images]
                    != row.get('image_sha256')):
                # Editor thumbnails cannot certify media in a later scheduled post.
                continue
            try:
                metadata, source, _, _ = snapshots.load(row['snapshot_id'])
                fingerprint = row['final_text_sha256'] + ':' + ','.join(row['image_sha256'])
                if (metadata['fingerprint'] != fingerprint
                        or metadata['source_fingerprint'] != row['source_fingerprint']
                        or source['platform'] != channel or source['post_id'] != row['post_id']
                        or snapshots.require_bound(metadata) != datetime.fromisoformat(row['scheduled_at'])):
                    continue
            except (Exception, SystemExit):
                continue
            candidates.append(row)
        accepted[channel] = {'verified': bool(candidates),
                             'attempt_id': candidates[-1]['attempt_id'] if candidates else None}
    return accepted


def activation_blockers(state_dir):
    blockers = [item['reason'] for item in checks() if not item['available']]
    for channel, result in acceptance(state_dir).items():
        if not result['verified']:
            blockers.append(channel + ' 尚无绑定冻结快照且已回读完整正文、远端图片数量及顺序的单渠道真实验收记录；编辑器图片与历史双渠道记录不能替代')
    return tuple(dict.fromkeys(blockers))
