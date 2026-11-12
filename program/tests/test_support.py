"""Synthetic fixtures for isolated software tests only. Never imported by server.py."""
import copy
from controller import Controller, signature
from fakes import DemoHardware

def demo_profiles():
    pulse = {'steps':100, 'rpm':1, 'timeout_s':5}
    return [dict(id=f'demo-{s}-{v}-{c}',source=s,sample=v,condition=c,target_ml=.2,
                 aspirate=copy.deepcopy(pulse),chase=copy.deepcopy(pulse),bubble=copy.deepcopy(pulse),
                 bubble_count=2,bubble_gap_s=.1,settle_s=0,max_hold_s=86400,max_plug_hold_s=30,initial_dose_count_min=0 if c=='first' else 1,initial_dose_count_max=0 if c=='first' else 4,immersion_mm=0 if c=='first' else 2,bubble_immersion_mm=2,
                 validated=False,bubble_validated=False,evidence={'note':'纯软件演示；没有对应实测体积'})
            for s in range(11,16) for v in range(1,7) for c in ('first','continued')]

def install_test_profiles(c):
    c.config['free_debug']=False  # Legacy calibrated workflow remains explicitly testable.
    c.config['reset'].update(validated=True,covers_initial_air_filled_water_branch=True,evidence='Synthetic test fixture only, not physical evidence')
    c.config['profiles']=demo_profiles()
    for p in c.config['profiles']:
        p.update(validated=True,bubble_validated=True,geometry_signature=signature(c.config))
        p['evidence']=dict(n=10,mean_ml=.2,rsd_percent=0,residual_water_ml=.001,carryover_percent=.1,water_blank_n=3,carryover_n=3,note='Synthetic test fixture only, not physical evidence')
    return c

def make_test_controller(directory):
    return install_test_profiles(Controller(directory, hardware_factory=lambda cfg:DemoHardware(cfg)))
