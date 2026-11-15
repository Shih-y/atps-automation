"""Pure, conservative optical candidate policy; never a phase-equilibrium measurement.

The scheduler owns deadlines and retry counts. This module only evaluates archived
evidence and never reads a clock, changes state, or performs hardware I/O.
"""
import math
from datetime import datetime


METHOD = 'local-optical-heuristic-v1'
DEFAULTS = dict(mode='human', max_auto_retries=2, retry_interval_s=60,
                stable_frames=2, min_frame_spacing_s=30,
                max_interface_shift_fraction=.03, min_phase_fraction=.1,
                single_phase_validated=False, single_phase_evidence='')


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def validate_observation_policy(values):
    """Normalize a bounded run policy. Omitted policy retains human review."""
    if values is None:
        values = {}
    if not isinstance(values, dict):
        raise ValueError('observation policy must be an object')
    result = {key: values.get(key, default) for key, default in DEFAULTS.items()}
    if result['mode'] not in ('human', 'automatic'):
        raise ValueError('observation mode must be human or automatic')
    for key, low, high in [('max_auto_retries', 0, 10), ('stable_frames', 2, 5)]:
        if type(result[key]) is not int or not low <= result[key] <= high:
            raise ValueError('%s must be an integer in %s..%s' % (key, low, high))
    for key, low, high in [('retry_interval_s', 1, 3600), ('min_frame_spacing_s', 1, 3600),
                           ('max_interface_shift_fraction', 0, .2), ('min_phase_fraction', .01, .45)]:
        if not _finite(result[key]) or not low <= result[key] <= high:
            raise ValueError('%s must be finite and in %s..%s' % (key, low, high))
    if type(result['single_phase_validated']) is not bool:
        raise ValueError('single_phase_validated must be a boolean')
    evidence = result['single_phase_evidence']
    if not isinstance(evidence, str) or len(evidence) > 4000:
        raise ValueError('single_phase_evidence must be text of at most 4000 characters')
    result['single_phase_evidence'] = evidence.strip()
    if result['single_phase_validated'] and not result['single_phase_evidence']:
        raise ValueError('validated single phase requires criterion and validation evidence')
    return result


def _time(value):
    try:
        if not isinstance(value, str) or len(value) > 50:
            return None
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.timestamp() if parsed.tzinfo is not None else None
    except (ValueError, OverflowError, OSError):
        return None


def _roi(items, bottle):
    if not isinstance(items, list):
        return None
    matches = [r for r in items if isinstance(r, dict) and r.get('sample') == bottle]
    if len(matches) != 1:
        return None
    roi = matches[0]
    if any(type(roi.get(k)) is not int for k in ('sample', 'x', 'y', 'width', 'height')):
        return None
    if roi['x'] < 0 or roi['y'] < 0 or roi['width'] < 20 or roi['height'] < 40:
        return None
    return tuple(roi[k] for k in ('sample', 'x', 'y', 'width', 'height'))


def _quality(bottle, roi, policy):
    """Recheck numeric thresholds; untrusted client flags alone cannot certify quality."""
    q = bottle.get('quality')
    if not isinstance(q, dict):
        return 'missing_quality', None
    flags = q.get('flags')
    if not isinstance(flags, list) or any(not isinstance(f, str) for f in flags):
        return 'invalid_quality', None
    if flags:
        return 'quality_flags', None
    for key, low, high in [('glare_fraction', 0, .03), ('texture_fraction', 0, .12),
                           ('edge_sharpness', 10, 255), ('contrast_range', 18, 255),
                           ('interface_contrast', 0, 255), ('horizontal_support', 0, 1)]:
        if not _finite(q.get(key)) or not low <= q[key] <= high:
            return 'invalid_quality_' + key, None
    signature = q.get('signature')
    if (not isinstance(signature, list) or len(signature) != 27 or
            any(not _finite(n) or not 0 <= n <= 255 for n in signature)):
        return 'invalid_quality_signature', None
    if q.get('stability') not in ('not_checked', 'stable'):
        return 'unstable_or_unavailable_quality', None
    classification = bottle.get('classification')
    if classification not in ('single', 'two_phase'):
        return 'uncertain_optical_classification', None
    region = bottle.get('inspected_region')
    if not isinstance(region, dict) or any(not _finite(region.get(k)) for k in ('x', 'y', 'width', 'height')):
        return 'invalid_inspected_region', None
    _, x, y, width, height = roi
    if (region['width'] <= 0 or region['height'] <= 0 or region['x'] < x or region['y'] < y or
            region['x'] + region['width'] > x + width or region['y'] + region['height'] > y + height):
        return 'invalid_inspected_region', None
    fraction = None
    if classification == 'two_phase':
        if q['interface_contrast'] < 8 or q['horizontal_support'] < .8:
            return 'weak_interface', None
        if not _finite(bottle.get('interface_y')):
            return 'invalid_interface', None
        # Fraction describes only the inspected optical region, not liquid volume.
        fraction = (bottle['interface_y'] - region['y']) / region['height']
        if not policy['min_phase_fraction'] <= fraction <= 1 - policy['min_phase_fraction']:
            return 'phase_fraction_out_of_bounds', None
    elif bottle.get('interface_y') is not None or q['interface_contrast'] >= 3 or q['horizontal_support'] != 0:
        return 'contradictory_single_phase_evidence', None
    return None, dict(classification=classification, fraction=fraction, signature=signature,
                      region=tuple(region[k] for k in ('x', 'y', 'width', 'height')))


def evaluate_observation(sample, project, policy):
    """Evaluate a consecutive, spaced sequence from one generation/mapping/ROI.

    `candidate` is machine provenance for review/adoption by the scheduler. Even in
    human mode it is only a candidate; the scheduler retains authority to adopt it.
    `unknown` carries stable machine reason codes. Deadlines remain external.
    """
    policy = validate_observation_policy(policy)
    result = dict(status='pending', classification='uncertain', reason_codes=[],
                  evidence_frame_ids=[], phase_height_fraction=None, method=METHOD)
    frames = sample.get('frames') or []
    if not frames:
        result['reason_codes'] = ['missing_frames']
        return result
    minimum = project.get('min_settle_s')
    maximum = sample.get('observation_max_s', project.get('max_observe_s'))
    mixed = _time(sample.get('mixed_at'))
    if (mixed is None or not _finite(minimum) or not _finite(maximum) or
            minimum < 0 or maximum < minimum):
        result.update(status='unknown', reason_codes=['invalid_observation_window'])
        return result
    # Old-generation evidence cannot influence either stability or current quality.
    current = [f for f in frames if isinstance(f, dict) and f.get('generation') == sample.get('generation')]
    if not current:
        result['reason_codes'] = ['no_current_generation_frames']
        return result
    # Preserve archive order so malformed late captures cannot be sorted before a
    # good pair and silently disappear. Capture timestamps must advance below.
    streak = []
    seen_ids, seen_jobs, seen_times = set(), set(), set()
    latest_reason = None
    for frame in current:
        reason = None
        captured = _time(frame.get('captured_at'))
        settle = frame.get('settle_s')
        frame_id = frame.get('id', frame.get('frame_id'))
        job = frame.get('job_id')
        roi = _roi(frame.get('rois'), sample.get('bottle'))
        mapping = frame.get('mapping_version')
        if frame.get('sample_id') != sample.get('id') or frame.get('bottle') != sample.get('bottle'):
            reason = 'sample_identity_mismatch'
        elif (captured is None or not _finite(settle) or not minimum <= settle <= maximum or
              abs(captured - mixed - settle) > .01 or _time(frame.get('mixed_at')) != mixed or
              frame.get('valid_observation_time') is not True):
            reason = 'invalid_frame_observation_time'
        elif not isinstance(frame_id, str) or not frame_id or type(mapping) is not int or mapping < 0 or roi is None:
            reason = 'invalid_frame_provenance'
        elif frame_id in seen_ids or (job is not None and (not isinstance(job, str) or job in seen_jobs)) or captured in seen_times:
            reason = 'duplicate_frame_evidence'
        analysis = frame.get('analysis')
        point = None
        if reason is None:
            seen_ids.add(frame_id)
            if job is not None: seen_jobs.add(job)
            seen_times.add(captured)
            if not isinstance(analysis, dict):
                reason = 'missing_analysis'
            elif analysis.get('method') != METHOD or analysis.get('validated') is not False:
                reason = 'unsupported_analysis_method'
            elif _roi(analysis.get('rois'), sample.get('bottle')) != roi:
                reason = 'analysis_roi_mismatch'
            else:
                bottles = analysis.get('bottles')
                bottles = [b for b in bottles if isinstance(b, dict) and b.get('sample') == sample.get('bottle')] if isinstance(bottles, list) else []
                if len(bottles) != 1:
                    reason = 'missing_bottle_analysis'
                else:
                    reason, point = _quality(bottles[0], roi, policy)
        if reason:
            streak = []
            latest_reason = reason
            continue
        point.update(id=frame_id, captured=captured, mapping=mapping, roi=roi)
        if streak and (streak[-1]['mapping'] != mapping or streak[-1]['roi'] != roi or streak[-1]['region'] != point['region']):
            streak = []
            latest_reason = 'mapping_or_roi_changed'
        if streak:
            prior = streak[-1]
            if (point['classification'] != prior['classification'] or
                    max(sum(abs(a - b) for a, b in zip(point['signature'], p['signature'])) / 27
                        for p in streak) > 12 or
                    (point['fraction'] is not None and
                     max(abs(point['fraction'] - p['fraction']) for p in streak) > policy['max_interface_shift_fraction'])):
                streak = [point]
                latest_reason = 'unstable_frame_sequence'
                continue
            if captured - prior['captured'] < policy['min_frame_spacing_s']:
                latest_reason = 'insufficient_frame_spacing'
                continue
        streak.append(point)
        streak = streak[-policy['stable_frames']:]
        if len(streak) >= policy['stable_frames']:
            latest_reason = None
    evidence = streak[-policy['stable_frames']:]
    result['evidence_frame_ids'] = [p['id'] for p in evidence]
    if len(evidence) < policy['stable_frames']:
        result['reason_codes'] = [latest_reason or 'insufficient_stable_frames']
        if latest_reason and latest_reason not in ('insufficient_frame_spacing', 'mapping_or_roi_changed'):
            result['status'] = 'unknown'
        return result
    if latest_reason:
        result.update(status='unknown', reason_codes=[latest_reason])
        return result
    if evidence[-1]['classification'] == 'single' and not policy['single_phase_validated']:
        result.update(status='unknown', reason_codes=['single_phase_criterion_unvalidated'])
        return result
    classification = evidence[-1]['classification']
    result.update(status='candidate', classification=classification,
                  reason_codes=['stable_optical_' + classification],
                  phase_height_fraction=(sum(p['fraction'] for p in evidence) / len(evidence)
                                         if classification == 'two_phase' else None))
    return result
