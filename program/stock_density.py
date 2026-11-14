"""Density evidence audit. Reference values never replace batch measurements."""
import copy
import math


REFERENCES = {
    'ammonium_sulfate': dict(density_g_ml=1.225, mass_fraction=.4, temperature_c=25,
        url='https://www.sigmaaldrich.com/US/en/sds/aldrich/451975',
        note='供应商40 wt%硫酸铵-14N2水溶液，25℃；同位素产品，仅作量级核对，非本批次实测。'),
    'K₂HPO₄': dict(density_g_ml=1.420, mass_fraction=.408, temperature_c=25,
        url='https://pmc.ncbi.nlm.nih.gov/articles/PMC12327354/',
        note='原论文表6：40.8 wt% K₂HPO₄，25℃，1.420±0.007 g/mL。40.8%不等于40%，不能直接代入。'),
    'PEG4000': dict(density_g_ml=None, mass_fraction=None, temperature_c=25,
        url='https://pubs.acs.org/doi/10.1021/je801004n',
        note='原研究含5—50% PEG4000水溶液密度；本次未核实40%、25℃的原始表值，留空。固体PEG密度不可代入。'),
    'sodium_sulfate': dict(density_g_ml=None, mass_fraction=None, temperature_c=25,
        url='https://trc.nist.gov/ThermoML/10.1021/je501152a.html',
        note='NIST收录Na₂SO₄水溶液原始密度数据；未取得与本批15%、25℃完全对应值，留空。不可误用亚硫酸钠。'),
    'sodium_citrate': dict(density_g_ml=None, mass_fraction=None, temperature_c=25,
        url='https://www.sciencedirect.com/science/article/pii/S0167732204001618',
        note='柠檬酸三钠水溶液密度原研究；未核实本批二水试剂30%配制、25℃对应值，留空。不能使用晶体/堆积密度。'),
    'water': dict(density_g_ml=.9970, mass_fraction=0, temperature_c=25,
        url='https://www.sigmaaldrich.com/US/en/product/sial/denwat',
        note='供应商纯水密度标准在25℃的参考值；不是本批水的测量结果。'),
}

# The UI reports percentages to three decimals. Half of that last displayed
# digit is rounding tolerance, not a claim about measured density accuracy.
CONCENTRATION_CONTEXT_ROUNDING = .0000050001


def _finite(value, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('密度依据需要有限数值')
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError('密度依据数值超出范围')
    return float(value)


def validate_density_context(stock):
    s = copy.deepcopy(stock)
    value = s.get('density_g_ml')
    basis = s.get('density_basis', 'unknown' if value is None else 'legacy_unverified')
    if basis not in ('unknown', 'measured', 'reference', 'legacy_unverified'):
        raise ValueError('密度依据须为未测、批次实测、文献参考或旧值待核对')
    if value is None:
        s['density_basis'] = 'unknown'
        return s
    _finite(value, .001, 10)
    if basis == 'unknown':
        raise ValueError('已填写密度时须说明密度依据')
    if basis in ('measured', 'reference'):
        s['density_temperature_c'] = _finite(s.get('density_temperature_c'), 0, 100)
        s['density_mass_fraction'] = _finite(s.get('density_mass_fraction'), 0, 1)
        if not isinstance(s.get('density_batch'), str) or len(s['density_batch']) > 200:
            raise ValueError('请记录密度对应的批次（参考值可留空字符串）')
    s['density_basis'] = basis
    return s


def _context_issues(s):
    issues = []
    try:
        if abs(float(s['density_temperature_c'])-float(s['temperature_c'])) > 1e-9:
            issues.append('密度温度与当前母液条件不一致')
        if abs(float(s['density_mass_fraction'])-float(s['mass_fraction'])) > CONCENTRATION_CONTEXT_ROUNDING:
            issues.append('密度对应浓度与当前有效溶质质量分数不一致')
        if s.get('density_basis') == 'measured' and s.get('density_batch') != s.get('batch', ''):
            issues.append('密度测量批次与当前母液批次不一致')
    except (ValueError, TypeError, KeyError):
        issues.append('密度温度、浓度或批次依据缺失')
    return issues


def measured_density_usable(s):
    try:
        s = validate_density_context(s)
        return (s.get('density_g_ml') is not None and s['density_basis'] == 'measured'
                and bool(str(s.get('density_evidence', '')).strip()) and not _context_issues(s))
    except ValueError:
        return False


def audit_stocks(stocks):
    rows = []
    for s in stocks:
        reference = copy.deepcopy(REFERENCES.get(s.get('solute'), dict(
            density_g_ml=None, mass_fraction=None, temperature_c=None, url=None,
            note='当前溶质名称无对应密度证据；不按阀口号猜测物质。')))
        value = s.get('density_g_ml')
        basis = s.get('density_basis', 'unknown' if value is None else 'legacy_unverified')
        issues = []
        status = 'unknown'
        if value is not None:
            try:
                normalized = validate_density_context(s)
                issues.extend(_context_issues(normalized) if basis in ('measured','reference') else [])
                if not str(s.get('density_evidence', '')).strip():
                    issues.append('缺少密度依据')
                status = 'context_mismatch' if issues else (
                    'measured' if basis == 'measured' else 'reference' if basis == 'reference' else 'unverified')
            except ValueError as exc:
                status = 'unverified'
                issues.append(str(exc))
            if basis == 'legacy_unverified':
                issues.append('旧密度未区分批次实测与参考值，须核对原记录')
        else:
            issues.append('未测；可直接采用本来源的克/步交付标定')
        if s.get('port') == 10 and s.get('composition_basis') != 'anhydrous_equivalent':
            issues.append('10口已确认按二水合物试剂配制；旧30%不应作为无水盐30%。保存正确来源新版本并新建项目版本。')
        if reference['density_g_ml'] is not None:
            if reference['mass_fraction'] != s.get('mass_fraction') or reference['temperature_c'] != s.get('temperature_c'):
                issues.append('所列文献条件与当前母液不完全一致，仅供核查')
        rows.append(dict(port=s.get('port'), name=s.get('name'), configured_density_g_ml=value,
            basis=basis, status=status, status_label={
                'unknown':'未测', 'measured':'已记录批次实测依据（未独立验证）',
                'reference':'文献参考，非批次实测', 'context_mismatch':'条件不匹配',
                'unverified':'旧值/依据待核对'}[status], issues=issues, reference=reference,
            mass_calibration_independent=True, composition_basis=s.get('composition_basis'),
            effective_mass_fraction=s.get('mass_fraction'), reagent_mass_fraction=s.get('reagent_mass_fraction')))
    return rows
