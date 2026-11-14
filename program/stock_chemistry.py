"""Explicit reagent and anhydrous-equivalent concentration bookkeeping."""
import copy
import math

CITRATE_ANHYDROUS_MOLAR_MASS = 258.06
CITRATE_DIHYDRATE_MOLAR_MASS = 294.10
CITRATE_FACTOR = CITRATE_ANHYDROUS_MOLAR_MASS / CITRATE_DIHYDRATE_MOLAR_MASS


def normalize_reagent_basis(stock):
    s = copy.deepcopy(stock)
    form = s.get('reagent_form', 'as_configured')
    if form not in ('as_configured', 'anhydrous', 'dihydrate'):
        raise ValueError('试剂形态无效')
    if form == 'dihydrate':
        if s.get('port') != 10 or s.get('solute') != 'sodium_citrate':
            raise ValueError('二水合物换算仅适用于10口柠檬酸三钠')
        reagent = s.get('reagent_mass_fraction')
        if isinstance(reagent,bool) or not isinstance(reagent,(float,int)) or not math.isfinite(reagent) or not 0 < reagent <= 1:
            raise ValueError('请填写二水合物试剂/溶液总质量分数')
        s['mass_fraction'] = reagent * CITRATE_FACTOR
        s['composition_basis'] = 'anhydrous_equivalent'
        s['hydrate_water_mass_fraction'] = reagent * (1-CITRATE_FACTOR)
        s['concentration_conversion'] = dict(anhydrous_molar_mass_g_mol=258.06,
            reagent_molar_mass_g_mol=294.10, factor=CITRATE_FACTOR,
            evidence='用户确认Na3C6H5O7·2H2O，按试剂质量配制；未作纯度修正')
    elif form == 'anhydrous':
        s['composition_basis'] = 'anhydrous_equivalent'
        s['reagent_mass_fraction'] = s['mass_fraction']
        s['hydrate_water_mass_fraction'] = 0
        s.pop('concentration_conversion', None)
    else:
        s.pop('hydrate_water_mass_fraction', None)
        s.pop('concentration_conversion', None)
    return s
