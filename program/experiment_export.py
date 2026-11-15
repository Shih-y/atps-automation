"""Flat, project-scoped traceability tables derived only from frozen records."""
import csv
import io


def _csv(columns, rows):
    buffer=io.StringIO(newline='')
    writer=csv.writer(buffer)
    writer.writerow(columns)
    for row in rows:
        writer.writerow(["'"+value if isinstance(value,str) and value.startswith(('=','+','-','@')) else value for value in row])
    return '\ufeff'+buffer.getvalue()


def detailed_tables(data):
    additions, observations, events, ties=[],[],[],[]
    for sample in data.get('samples',[]):
        base=[sample['id'],sample['project_id'],sample.get('bottle'),sample.get('execution_generation')]
        recipe=sample.get('recipe',{})
        for row in recipe.get('plan',{}).get('items',[]):
            for index, parameters in enumerate(row.get('parameters',[])):
                ref=row.get('reference',{})
                aspirate,chase=parameters['aspirate'],parameters['chase']
                additions.append(base+[row['source'],index+1,row.get('requested_mass_g'),
                    row.get('estimated_mass_g'),aspirate.get('steps'),aspirate.get('rpm'),
                    chase.get('steps'),chase.get('rpm'),ref.get('id'),ref.get('mass_per_step_g'),
                    recipe.get('estimated',{}).get('peg_fraction'),recipe.get('estimated',{}).get('salt_fraction'),
                    'calibrated_estimate_not_measured','g','w/w'])
        label=sample.get('label') or {}
        observation=sample.get('auto_observation') or {}
        for frame in sample.get('frames',[]):
            candidate=next((b for b in (frame.get('analysis') or {}).get('bottles',[]) if b.get('sample')==sample.get('bottle')),{})
            observations.append(base[:3]+[frame.get('generation'),frame['id'],frame.get('job_id'),frame.get('captured_at'),
                frame.get('settle_s'),frame.get('lateness_s'),frame.get('mapping_version'),candidate.get('classification'),
                '; '.join(candidate.get('quality',{}).get('flags',[])),observation.get('status'),
                '; '.join(observation.get('reason_codes',[])),label.get('classification'),label.get('source','human') if label else '',
                label.get('version'),'; '.join(label.get('evidence_frame_ids',[label.get('frame_id','')]))])
        for event in sample.get('action_events',[]):
            current=event.get('current') or {}
            events.append(base[:3]+[event.get('generation'),event.get('at'),event.get('event'),current.get('item_index'),current.get('chunk_index'),
                current.get('phase'),event.get('completed_commands'),event.get('error'),event.get('amount_basis')])
        for tie in sample.get('tie_line_records',[]):
            for phase in ('upper','lower'):
                composition=tie[phase]
                ties.append(base[:3]+[tie.get('generation'),tie['id'],phase,composition['peg_fraction'],composition['salt_fraction'],
                    composition.get('mass_g'),tie['method'],tie['evidence'],tie['recorded_at'],
                    'independent_analysis_not_inferred_from_image'])
    return {
        'additions.csv':_csv(['sample_id','project_id','bottle','generation','source','segment_index','theoretical_source_total_g',
            'estimated_source_total_g','aspirate_steps','aspirate_rpm','chase_steps','chase_rpm','calibration_id','mass_per_step_g',
            'estimated_final_peg_fraction','estimated_final_salt_fraction','amount_basis','mass_unit','composition_unit'],additions),
        'observations.csv':_csv(['sample_id','project_id','bottle','generation','frame_id','job_id','captured_at','settle_s',
            'lateness_s','roi_mapping_version','optical_candidate','quality_flags','observation_status','reason_codes',
            'adopted_label','label_source','label_version','evidence_frame_ids'],observations),
        'action_events.csv':_csv(['sample_id','project_id','bottle','generation','at','event','item_index','segment_index','phase',
            'completed_commands','error','amount_basis'],events),
        'tie_lines.csv':_csv(['sample_id','project_id','bottle','generation','record_id','phase','peg_fraction','salt_fraction',
            'phase_mass_g','analysis_method','evidence','recorded_at','basis'],ties),
    }
